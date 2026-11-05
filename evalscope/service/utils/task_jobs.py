"""Serializable task jobs executed by the queue backend.

Both queue backends run the same entrypoints: the in-process queue calls the
:class:`TaskJob` directly, and the Redis backend executes
:func:`execute_job` inside a worker process. Job arguments are therefore
limited to picklable objects (Pydantic models already are).

The worker path also owns:

* cancellation polling — a running job watches its Redis cancel key and
  terminates the task's subprocess;
* terminal state — a job always leaves ``task_info.json`` and ``result.json``
  in a terminal state, even when the runner raised.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

from evalscope.utils.logger import get_logger

from . import log as task_log
from . import queue_config
from .process import TaskStoppedError, stop_process

if TYPE_CHECKING:
    from evalscope.config import TaskConfig
    from evalscope.perf.arguments import Arguments as PerfArguments
    from evalscope.perf.executors.arguments import AIPerfArguments
    from evalscope.perf.executors.models import ExternalRuntime
    from evalscope.perf.experiments.planner import CacheSubrunPlan

    from ..api_models.common import ActiveTaskInfo

logger = get_logger()

CANCEL_POLL_INTERVAL = 0.5


def watch_cancellation(task_id: str) -> threading.Event:
    """Start a thread that stops *task_id*'s subprocess when its cancel key appears.

    Returns an event the caller sets once the job is done. The watcher is a
    no-op for the in-process backend, where ``/stop`` terminates the
    subprocess directly.
    """
    finished = threading.Event()
    if queue_config.queue_backend() != queue_config.BACKEND_REDIS:
        return finished
    try:
        import redis
    except ImportError:
        logger.warning(f'[{task_id}] Cancellation polling disabled: redis package is not installed')
        return finished

    key = queue_config.cancellation_key(task_id)
    url = queue_config.redis_url()

    def poll() -> None:
        try:
            connection = redis.Redis.from_url(url, socket_timeout=2.0, socket_connect_timeout=2.0)
        except Exception as error:  # noqa: BLE001 - the watcher is a safety net, never fatal
            logger.warning(f'[{task_id}] Cancellation polling unavailable ({type(error).__name__})')
            return
        retrying = False
        try:
            while not finished.wait(CANCEL_POLL_INTERVAL):
                try:
                    requested = connection.get(key)
                except Exception as error:  # noqa: BLE001 - keep polling through transient Redis errors
                    logger.warning(f'[{task_id}] Cancellation check failed ({type(error).__name__}); retrying')
                    continue
                if not requested:
                    continue
                # The request may arrive before the task registered its subprocess;
                # keep polling until the stop is actually applied or the job ends.
                if stop_process(task_id):
                    logger.info(f'[{task_id}] Cancellation applied; stopped the task')
                    return
                if not retrying:
                    logger.info(f'[{task_id}] Cancellation pending: the task has no stoppable process yet')
                    retrying = True
        finally:
            connection.close()

    threading.Thread(target=poll, daemon=True, name=f'evalscope-cancel-{task_id}').start()
    return finished


def clear_cancellation(task_id: str) -> None:
    """Remove the task's cancel key after it reached a terminal state."""
    if queue_config.queue_backend() != queue_config.BACKEND_REDIS:
        return
    try:
        import redis

        connection = redis.Redis.from_url(queue_config.redis_url(), socket_timeout=2.0, socket_connect_timeout=2.0)
    except Exception as error:  # noqa: BLE001 - the key expires on its own
        logger.warning(f'[{task_id}] Failed to clear cancellation key ({type(error).__name__})')
        return
    try:
        connection.delete(queue_config.cancellation_key(task_id))
    except Exception as error:  # noqa: BLE001
        logger.warning(f'[{task_id}] Failed to clear cancellation key ({type(error).__name__})')
    finally:
        connection.close()


def persist_interrupted_task(job: Any, exc_type: Optional[type] = None) -> bool:
    """Record a terminal error for a task RQ finished without running our runner.

    An abandoned job (its worker died) or a job that failed before ``_execute``
    started never reaches the terminal bookkeeping, so without this its manifest
    would stay queued/running and ``result.json`` would be missing forever.

    Returns:
        True when a terminal state was written.
    """
    task_id = getattr(job, 'id', None)
    if not task_id:
        return False
    try:
        meta = (getattr(job, 'meta', None) or {}).get('evalscope')
    except Exception:  # noqa: BLE001 - a corrupt job is not a task we can report on
        return False
    if not meta:
        return False

    # The manifest is the authority: submitting a task id again resets it to
    # ``queued`` while the previous run's result.json is still on disk, so a
    # terminal result alone does not prove this run is finished.
    manifest = task_log.read_task_manifest(os.path.join(task_log.OUTPUT_DIR, task_id))
    if manifest is not None:
        if manifest.get('status') in queue_config.TERMINAL_STATUSES:
            return False
    else:
        existing = task_log.read_task_result(task_id)
        if existing is not None and existing.get('status') in queue_config.TERMINAL_STATUSES:
            return False

    name = getattr(exc_type, '__name__', '')
    message = (
        'Task interrupted: the worker executing it was lost'
        if name == 'AbandonedJobError'
        else 'Task failed before it produced a result'
    )
    payload = {'status': 'error', 'task_id': task_id, 'error': message}
    try:
        task_log.finish_task(task_id, 'error')
        task_log.write_task_result(task_id, payload)
    except (OSError, ValueError):
        logger.exception(f'[{task_id}] Failed to persist interrupted task state')
        return False
    clear_cancellation(task_id)
    logger.error(f'[{task_id}] {message}')
    return True


def handle_rq_exception(
    job: Any,
    exc_type: Optional[type] = None,
    exc_value: Any = None,
    traceback: Any = None,
) -> bool:
    """RQ exception handler: record interrupted tasks and keep the handler chain going."""
    try:
        persist_interrupted_task(job, exc_type)
    except Exception:  # noqa: BLE001 - a handler must never break the worker
        logger.exception('Failed to persist interrupted task state')
    return True


def _persist_terminal(task_id: str, payload: Dict[str, Any]) -> None:
    """Make the task's on-disk state terminal, whatever the runner returned."""
    try:
        task_log.finish_task(task_id, payload['status'])
        task_log.write_task_result(task_id, payload)
    except (OSError, ValueError):
        logger.exception(f'[{task_id}] Failed to persist terminal task state')


def _execute(task_id: str, runner: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Run *runner* with cancellation support and terminal-state guarantees."""
    finished = watch_cancellation(task_id)
    try:
        payload = runner()
    except TaskStoppedError:
        payload = {'status': 'stopped', 'task_id': task_id}
    except Exception as error:  # noqa: BLE001 - a worker must never die without a record
        logger.error(f'[{task_id}] Queued task failed ({type(error).__name__})')
        payload = {'status': 'error', 'task_id': task_id, 'error': 'Task failed; check the task log'}
    finally:
        finished.set()

    if not isinstance(payload, dict) or payload.get('status') not in queue_config.TERMINAL_STATUSES:
        payload = {'status': 'error', 'task_id': task_id, 'error': 'Task produced no result'}

    _persist_terminal(task_id, payload)
    clear_cancellation(task_id)
    return payload


# ---------------------------------------------------------------------------
# Job entrypoints (top-level so workers can import them by name)
# ---------------------------------------------------------------------------


def execute_eval_job(
    task_id: str,
    task_config: 'TaskConfig',
    label: str = 'Task',
    task_meta: Optional['ActiveTaskInfo'] = None,
) -> Dict[str, Any]:
    """Run an evaluation (or resume) task."""
    from ..blueprints.eval import _run_eval_task

    return _execute(task_id, lambda: _run_eval_task(task_id, task_config, label=label, task_meta=task_meta))


def execute_native_perf_job(
    task_id: str,
    perf_args: 'PerfArguments',
    task_meta: 'ActiveTaskInfo',
) -> Dict[str, Any]:
    """Run a native EvalScope performance benchmark."""
    from ..blueprints.perf import _run_native_perf

    return _execute(task_id, lambda: _run_native_perf(task_id, perf_args, task_meta))


def execute_cache_experiment_job(
    task_id: str,
    arguments: 'AIPerfArguments',
    runtime: 'ExternalRuntime',
    plan: 'CacheSubrunPlan',
    task_meta: 'ActiveTaskInfo',
) -> Dict[str, Any]:
    """Run an AIPerf cache experiment (one or more subruns)."""
    from ..blueprints.perf import _run_cache_experiment, _run_perf_safely

    def run() -> Dict[str, Any]:
        return _run_perf_safely(task_id, lambda: _run_cache_experiment(arguments, task_id, runtime, plan, task_meta))

    return _execute(task_id, run)


def execute_single_aiperf_job(
    task_id: str,
    prepared: Dict[str, Any],
    arguments: 'AIPerfArguments',
    runtime: 'ExternalRuntime',
) -> Dict[str, Any]:
    """Run one inline-prompt AIPerf single run."""
    from ..blueprints.perf import _run_single_aiperf

    return _execute(task_id, lambda: _run_single_aiperf(prepared, arguments, task_id, runtime))


JOB_ENTRYPOINTS: Dict[str, Callable[..., Dict[str, Any]]] = {
    'eval': execute_eval_job,
    'perf': execute_native_perf_job,
    'cache_experiment': execute_cache_experiment_job,
    'single_aiperf': execute_single_aiperf_job,
}


@dataclass
class TaskJob:
    """A queue-agnostic description of work to run.

    ``entrypoint`` selects a function in :data:`JOB_ENTRYPOINTS`; ``args`` are
    passed to it verbatim and must be picklable for the Redis backend.
    """

    entrypoint: str
    args: Tuple[Any, ...]

    def __post_init__(self) -> None:
        if self.entrypoint not in JOB_ENTRYPOINTS:
            raise ValueError(f'Unknown task entrypoint: {self.entrypoint}')
        if not isinstance(self.args, tuple):
            self.args = tuple(self.args)

    def __call__(self) -> Dict[str, Any]:
        return execute_job(self)


def execute_job(job: TaskJob) -> Dict[str, Any]:
    """Execute a :class:`TaskJob`; the RQ worker's job function."""
    return JOB_ENTRYPOINTS[job.entrypoint](*job.args)
