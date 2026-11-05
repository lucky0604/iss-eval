"""Redis/RQ task backend for deployments that must survive restarts.

The web process only enqueues jobs; one or more ``evalscope worker``
processes execute them. Task metadata lives in the RQ job (including the
``ActiveTaskInfo`` snapshot), while progress and results stay on the shared
output volume so the dashboard keeps working across restarts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from evalscope.service.api_models.common import ActiveTaskInfo
from evalscope.utils.logger import get_logger

from . import log as task_log
from . import queue_config

logger = get_logger()

# RQ statuses a client should still see in the active list.
_ACTIVE_STATUSES = ('queued', 'deferred', 'scheduled', 'ready_to_enqueue', 'rate_limited', 'started')
_TERMINAL_RQ_STATUSES = ('finished', 'failed', 'stopped', 'canceled')
_QUEUED_RQ_STATUSES = ('queued', 'deferred', 'scheduled', 'ready_to_enqueue', 'rate_limited')
_LIST_LIMIT = 200


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class RedisTaskQueue:
    """An RQ-backed queue exposing the same interface as the in-process queue."""

    backend_name = 'redis'

    def __init__(self, url: Optional[str] = None, queue_name: Optional[str] = None):
        try:
            import redis
            from rq import Queue
        except ImportError as error:  # pragma: no cover - dependency is declared with the service extra
            raise queue_config.QueueUnavailableError(
                'The Redis queue backend requires the redis and rq packages'
            ) from error
        self._url = url or queue_config.redis_url()
        self._connection = redis.Redis.from_url(self._url, socket_timeout=5.0, socket_connect_timeout=5.0)
        self._queue = Queue(queue_name or queue_config.queue_name(), connection=self._connection)
        self._queue_name = self._queue.name

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------

    def submit(
        self, task_id: str, kind: str, info: ActiveTaskInfo, runner: Callable[[], Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Enqueue *runner* as an RQ job and return its queued snapshot.

        Raises:
            TypeError: when *runner* is not a serializable job descriptor.
            ValueError: when a task with the same id is queued, running or being submitted.
            QueueFullError: when the configured queue depth is already reached.
            QueueUnavailableError: when Redis cannot accept the job.
        """
        from .task_jobs import TaskJob, execute_job

        if not isinstance(runner, TaskJob):
            raise TypeError('The Redis queue backend requires a TaskJob runner')

        lock_key = queue_config.submit_lock_key(task_id)
        try:
            acquired = bool(self._connection.set(lock_key, '1', nx=True, ex=queue_config.submit_lock_ttl()))
        except Exception as error:  # noqa: BLE001 - any connection problem means "cannot accept work"
            raise queue_config.QueueUnavailableError('Task queue is unavailable') from error
        if not acquired:
            raise ValueError('Task is already queued or running')

        try:
            existing = self._fetch_job(task_id)
            if existing is not None:
                status = self._job_status(existing)
                # A job whose status cannot be read is stale, not active: it must
                # not make the id unusable forever.
                if status is not None and status not in _TERMINAL_RQ_STATUSES:
                    raise ValueError('Task is already queued or running')
                try:
                    existing.delete()
                except Exception as error:  # noqa: BLE001 - a stale job must not block a new submission
                    logger.warning(f'[{task_id}] Failed to delete a finished job ({type(error).__name__})')

            # Checked after the duplicate/stale handling so an active task keeps
            # reporting 409 instead of "queue full" when the queue is at capacity.
            try:
                limit = queue_config.max_queue_depth()
                depth = len(self._queue) if limit else 0
            except Exception as error:  # noqa: BLE001 - any connection problem means "cannot accept work"
                raise queue_config.QueueUnavailableError('Task queue is unavailable') from error
            if limit and depth >= limit:
                raise queue_config.QueueFullError('Task queue is full')

            enqueued_at = datetime.now().isoformat()
            queued_info = info.model_copy(update={'status': 'queued', 'enqueued_at': enqueued_at})
            meta = {
                'evalscope': {
                    'kind': kind,
                    'info': queued_info.model_dump(mode='json'),
                    'enqueued_at': enqueued_at,
                }
            }
            try:
                job = self._queue.enqueue_call(
                    execute_job,
                    args=(runner,),
                    job_id=task_id,
                    timeout=-1,  # no RQ-side timeout: long evaluations must not be killed
                    result_ttl=0,  # results live in result.json, not in Redis
                    failure_ttl=queue_config.redis_failure_ttl(),
                    meta=meta,
                    description=f'{kind}:{task_id}',
                )
            except Exception as error:  # noqa: BLE001 - report an outage instead of a traceback
                logger.error(f'[{task_id}] Failed to enqueue task ({type(error).__name__})')
                raise queue_config.QueueUnavailableError('Task queue is unavailable') from error

            snapshot = queued_info.model_dump(mode='json')
            snapshot['status'] = 'queued'
            snapshot['enqueued_at'] = enqueued_at
            snapshot['queue_position'] = self._queue_position(job)

            try:
                task_log.update_task_manifest(task_id, status='queued', enqueued_at=enqueued_at)
            except (OSError, ValueError):
                logger.exception(f'[{task_id}] Failed to persist queued task state')
            return snapshot
        finally:
            try:
                self._connection.delete(lock_key)
            except Exception:  # noqa: BLE001 - the lock expires on its own
                pass

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Return a queued/running job snapshot, or ``None`` when not active."""
        job = self._fetch_job(task_id)
        if job is None:
            return None
        status = self._job_status(job)
        if status is None:
            return None
        return self._snapshot(job, status)

    def list(self) -> List[Dict[str, Any]]:
        """Return snapshots of queued and running jobs (most active first)."""
        from rq.job import Job
        from rq.registry import StartedJobRegistry

        try:
            queued_ids = self._queue.get_job_ids()[:_LIST_LIMIT]
            started_ids = StartedJobRegistry(self._queue_name, connection=self._connection).get_job_ids()[:_LIST_LIMIT]
            job_ids = list(dict.fromkeys(list(started_ids) + list(queued_ids)))
            jobs = Job.fetch_many(job_ids, connection=self._connection) if job_ids else []
        except Exception as error:  # noqa: BLE001 - listing must not take the dashboard down
            logger.warning(f'Failed to list Redis queue jobs ({type(error).__name__})')
            return []

        positions = {task_id: index + 1 for index, task_id in enumerate(queued_ids)}
        snapshots: List[Dict[str, Any]] = []
        for job in jobs:
            if job is None:
                continue
            status = self._job_status(job, refresh=False)
            if status is None:
                continue
            snapshot = self._snapshot(job, status, positions)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def cancel(self, task_id: str) -> str:
        """Cancel a queued job or ask a running worker to stop it.

        Returns:
            ``stopped`` for a queued job removed from the queue, ``cancelling``
            when a running worker was signalled, ``unknown`` otherwise.
        """
        job = self._fetch_job(task_id)
        if job is None:
            return 'unknown'
        status = self._job_status(job)
        if status in _TERMINAL_RQ_STATUSES or status is None:
            return 'unknown'
        if status == 'started':
            self._request_cancel(task_id)
            return 'cancelling'

        # Set the key first as a race guard: if a worker starts the job between
        # the status read and the cancel below, the watcher still stops it.
        self._request_cancel(task_id)
        try:
            job.cancel()
        except Exception as error:  # noqa: BLE001 - the job may have just started or finished
            logger.warning(f'[{task_id}] RQ cancel failed ({type(error).__name__})')
        current = self._job_status(job)
        if current in _ACTIVE_STATUSES:
            # The job is still queued (or just started): the cancel key makes the
            # worker stop it as soon as it picks the job up.
            return 'cancelling'
        if current != 'canceled':
            self._clear_cancel(task_id)
            return 'unknown'

        self._clear_cancel(task_id)
        try:
            task_log.finish_task(task_id, 'stopped')
            task_log.write_task_result(task_id, {'status': 'stopped', 'task_id': task_id})
        except (OSError, ValueError):
            logger.exception(f'[{task_id}] Failed to persist cancelled task state')
        return 'stopped'

    def shutdown(self, timeout: float = 1.0) -> None:
        """Close the Redis connection pool (jobs are unaffected)."""
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001 - shutdown is best effort
            pass

    def ping(self) -> bool:
        """Return whether Redis answers; used by the readiness endpoint."""
        return bool(self._connection.ping())

    def reconcile(self) -> int:
        """Persist terminal errors for jobs whose worker died before recording them.

        RQ moves abandoned jobs (heartbeat expired) to the failed registry, but
        nothing would write their ``task_info.json``/``result.json``. This sweep
        applies the same terminal-state contract the runner would have and
        returns how many tasks it recorded.
        """
        from rq.registry import FailedJobRegistry, clean_registries

        from .task_jobs import persist_interrupted_task

        recorded = 0

        def handler(
            job: Any,
            exc_type: Optional[type] = None,
            exc_value: Any = None,
            traceback: Any = None,
        ) -> bool:
            nonlocal recorded
            if persist_interrupted_task(job, exc_type):
                recorded += 1
            return True

        try:
            clean_registries(self._queue, [handler])
            failed_ids = FailedJobRegistry(self._queue_name, connection=self._connection).get_job_ids()[:_LIST_LIMIT]
        except Exception as error:  # noqa: BLE001 - a sweep must not stop a worker from starting
            logger.warning(f'Failed to reconcile the Redis queue ({type(error).__name__})')
            return 0

        for job_id in failed_ids:
            job = self._fetch_job(job_id)
            if job is not None and persist_interrupted_task(job):
                recorded += 1
        return recorded

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_job(self, task_id: str):
        try:
            return self._queue.fetch_job(task_id)
        except Exception:  # noqa: BLE001 - a deleted or unreachable job is simply absent
            return None

    @staticmethod
    def _job_status(job, refresh: bool = True) -> Optional[str]:
        try:
            status = job.get_status(refresh=refresh)
        except Exception:  # noqa: BLE001 - the job may have been deleted by its result TTL
            return None
        return status.value if status is not None else None

    def _queue_position(self, job, status: Optional[str] = None) -> Optional[int]:
        status = status or self._job_status(job)
        if status not in _QUEUED_RQ_STATUSES:
            return None
        try:
            position = self._queue.get_job_position(job)
        except Exception:  # noqa: BLE001
            return None
        return position + 1 if position is not None else None

    def _snapshot(self, job, status: str, positions: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Any]]:
        """Build the API view of *job*; ``None`` when it is no longer active."""
        if status not in _ACTIVE_STATUSES:
            return None

        payload = (job.meta or {}).get('evalscope') or {}
        data: Dict[str, Any] = dict(payload.get('info') or {})
        data['task_id'] = job.id
        data.setdefault('kind', payload.get('kind', ''))
        data['enqueued_at'] = payload.get('enqueued_at') or _iso(job.created_at)

        if status == 'started':
            data['status'] = 'cancelling' if self._cancel_requested(job.id) else 'running'
            data['started_at'] = _iso(job.started_at) or data.get('started_at', '')
            data['queue_position'] = None
            return data

        data['status'] = 'queued'
        data['queue_position'] = positions.get(job.id) if positions is not None else self._queue_position(job, status)
        return data

    def _cancel_requested(self, task_id: str) -> bool:
        try:
            return bool(self._connection.get(queue_config.cancellation_key(task_id)))
        except Exception:  # noqa: BLE001 - treat an unreadable key as "not requested"
            return False

    def _request_cancel(self, task_id: str) -> None:
        try:
            self._connection.set(
                queue_config.cancellation_key(task_id),
                '1',
                ex=queue_config.cancellation_ttl(),
            )
        except Exception as error:  # noqa: BLE001 - the API still reports the attempt
            logger.error(f'[{task_id}] Failed to request cancellation ({type(error).__name__})')

    def _clear_cancel(self, task_id: str) -> None:
        try:
            self._connection.delete(queue_config.cancellation_key(task_id))
        except Exception:  # noqa: BLE001 - the key expires on its own
            pass
