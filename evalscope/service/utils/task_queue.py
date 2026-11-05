"""Task queue backends for the Web service.

``inprocess`` (default) keeps execution inside the service process: submitted
tasks wait in a FIFO queue and run on dedicated daemon threads. ``redis``
enqueues RQ jobs executed by separate worker processes. Both backends expose
the same producer/consumer API so the blueprints do not depend on one.
"""

from __future__ import annotations

import os
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional, Union

from evalscope.service.api_models.common import ActiveTaskInfo
from evalscope.utils.logger import get_logger

from . import log as task_log
from . import queue_config
from .process import TaskStoppedError
from .queue_config import TERMINAL_STATUSES
from .redis_queue import RedisTaskQueue

logger = get_logger()

TaskRunner = Callable[[], Dict[str, Any]]


def _now() -> str:
    return datetime.now().isoformat()


@dataclass
class _TaskRecord:
    """A queued or running task and the callable that executes it."""

    task_id: str
    kind: str
    info: ActiveTaskInfo
    runner: TaskRunner
    status: str = 'queued'
    enqueued_at: str = field(default_factory=_now)
    running_at: Optional[str] = None


class TaskQueue:
    """A FIFO queue with worker threads and persisted task state.

    Only queues work; execution semantics (subprocesses, cancellation, result
    shaping) stay in the blueprints. That keeps the queue reusable when the
    backend moves to Redis.
    """

    def __init__(self, max_workers: int = 1):
        self._condition = threading.Condition()
        self._records: Dict[str, _TaskRecord] = {}
        self._pending: Deque[str] = deque()
        self._shutdown = False
        self._workers = [
            threading.Thread(target=self._work, daemon=True, name=f'evalscope-task-{index}')
            for index in range(max(1, max_workers))
        ]
        for worker in self._workers:
            worker.start()

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------

    def submit(self, task_id: str, kind: str, info: ActiveTaskInfo, runner: TaskRunner) -> Dict[str, Any]:
        """Enqueue *runner* and return the task's queued identity snapshot.

        Raises:
            ValueError: when a task with the same id is already queued or running.
            QueueFullError: when the configured queue depth is already reached.
        """
        with self._condition:
            if task_id in self._records:
                raise ValueError('Task is already queued or running')
            limit = queue_config.max_queue_depth()
            if limit and len(self._pending) >= limit:
                raise queue_config.QueueFullError('Task queue is full')
            record = _TaskRecord(
                task_id=task_id,
                kind=kind,
                info=info.model_copy(update={'status': 'queued', 'enqueued_at': _now()}),
                runner=runner,
            )
            self._records[task_id] = record
            self._pending.append(task_id)
            # Snapshot before notifying so the acknowledgement always describes
            # the task as queued, even when a worker picks it up immediately.
            snapshot = self._snapshot(record)
            self._condition.notify()

        task_log.update_task_manifest(task_id, status='queued', enqueued_at=record.enqueued_at)
        return snapshot

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Return a queued/running task snapshot, or ``None`` when unknown."""
        with self._condition:
            record = self._records.get(task_id)
            return self._snapshot(record) if record is not None else None

    def list(self) -> List[Dict[str, Any]]:
        """Return snapshots of every queued and running task."""
        with self._condition:
            return [self._snapshot(record) for record in self._records.values()]

    def cancel(self, task_id: str) -> str:
        """Request cancellation.

        Returns:
            ``stopped`` when a queued task was removed and marked stopped,
            ``running`` when the caller must terminate the running subprocess,
            ``unknown`` when the queue does not track the task.
        """
        with self._condition:
            record = self._records.get(task_id)
            if record is None:
                return 'unknown'
            if record.status != 'queued':
                return 'running'
            self._records.pop(task_id, None)
            if task_id in self._pending:
                self._pending.remove(task_id)

        try:
            task_log.finish_task(task_id, 'stopped')
            task_log.write_task_result(task_id, {'status': 'stopped', 'task_id': task_id})
        except (OSError, ValueError):
            logger.exception(f'[{task_id}] Failed to persist cancelled task state')
        return 'stopped'

    def shutdown(self, timeout: float = 1.0) -> None:
        """Stop accepting work and wake idle workers (daemon threads do not block exit)."""
        with self._condition:
            self._shutdown = True
            self._condition.notify_all()
        for worker in self._workers:
            worker.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _work(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._shutdown:
                    self._condition.wait()
                if self._shutdown:
                    return
                task_id = self._pending.popleft()
                record = self._records.get(task_id)
            if record is None:  # cancelled while waiting
                continue
            self._run(record)

    def _run(self, record: _TaskRecord) -> None:
        # Re-check under the lock: a cancel may have removed the record after
        # the worker dequeued it.
        with self._condition:
            if self._records.get(record.task_id) is not record:
                return
            record.status = 'running'
            record.running_at = _now()
            record.info = record.info.model_copy(update={'status': 'running', 'started_at': record.running_at})

        task_log.update_task_manifest(record.task_id, status='running', started_at=record.running_at)

        try:
            payload = record.runner()
        except TaskStoppedError:
            payload = {'status': 'stopped', 'task_id': record.task_id}
        except Exception as error:  # noqa: BLE001 - a worker thread must never die silently
            logger.error(f'[{record.task_id}] Queued task failed ({type(error).__name__})')
            payload = {'status': 'error', 'task_id': record.task_id, 'error': 'Task failed; check the task log'}

        if not isinstance(payload, dict) or payload.get('status') not in TERMINAL_STATUSES:
            payload = {'status': 'error', 'task_id': record.task_id, 'error': 'Task produced no result'}

        # Runners normally record their own terminal state; the queue repeats it
        # so the manifest is terminal even for a runner that forgot to, and for
        # payloads this worker had to synthesize.
        try:
            task_log.finish_task(record.task_id, payload['status'])
        except (OSError, ValueError):
            logger.exception(f'[{record.task_id}] Failed to persist terminal task state')

        try:
            task_log.write_task_result(record.task_id, payload)
        except (OSError, ValueError):
            logger.exception(f'[{record.task_id}] Failed to persist task result')

        with self._condition:
            self._records.pop(record.task_id, None)

    def _snapshot(self, record: _TaskRecord) -> Dict[str, Any]:
        data = record.info.model_copy(update={'status': record.status}).model_dump(mode='json')
        data['enqueued_at'] = record.enqueued_at
        if record.status == 'queued':
            try:
                data['queue_position'] = list(self._pending).index(record.task_id) + 1
            except ValueError:
                data['queue_position'] = None
        else:
            data['queue_position'] = None
        return data


_queue_lock = threading.Lock()
_queue: Optional[Union[TaskQueue, RedisTaskQueue]] = None


def _configured_workers() -> int:
    workers = os.getenv('EVALSCOPE_TASK_QUEUE_WORKERS', '1')
    try:
        return max(1, int(workers))
    except ValueError:
        return 1


def get_task_queue() -> Union[TaskQueue, RedisTaskQueue]:
    """Return the process-wide queue, creating the configured backend on first use.

    ``EVALSCOPE_QUEUE_BACKEND`` selects ``inprocess`` (default) or ``redis``.
    """
    global _queue
    with _queue_lock:
        if _queue is None:
            backend = queue_config.queue_backend()
            if backend == queue_config.BACKEND_REDIS:
                _queue = RedisTaskQueue()
            elif backend == queue_config.BACKEND_INPROCESS:
                _queue = TaskQueue(max_workers=_configured_workers())
            else:
                raise RuntimeError(f'Unknown EVALSCOPE_QUEUE_BACKEND: {backend}')
        return _queue


def queue_health() -> Dict[str, Any]:
    """Return the queue backend and whether it is reachable.

    Raises:
        Exception: when the Redis backend cannot answer ``PING``.
    """
    backend = get_task_queue()
    if isinstance(backend, RedisTaskQueue):
        backend.ping()
    return {'backend': getattr(backend, 'backend_name', 'inprocess')}


def reset_task_queue() -> None:
    """Drop the process-wide queue; intended for tests and service shutdown."""
    global _queue
    with _queue_lock:
        if _queue is not None:
            _queue.shutdown()
        _queue = None
