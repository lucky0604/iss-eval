import contextlib
import multiprocessing
import queue
import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from multiprocessing.connection import wait
from typing import Any, Callable, Iterator, List, Optional, TextIO, Type

from evalscope.config import TaskConfig
from evalscope.perf.arguments import Arguments as PerfArguments
from evalscope.perf.executors.external import run_external
from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome, ExternalInvocation
from evalscope.perf.integrations.aiperf.single_run import AIPerfSingleRunRequest, run_single
from evalscope.perf.main import run_perf_benchmark
from evalscope.run import run_task
from evalscope.service.api_models.common import ActiveTaskInfo
from evalscope.utils.logger import get_logger

logger = get_logger()


@dataclass
class ActiveTask:
    """A running task's subprocess plus the identity shown in the Web dashboard."""

    proc: multiprocessing.Process
    info: ActiveTaskInfo
    cancel_event: Any = None
    cleanup_event: threading.Event = field(default_factory=threading.Event)
    external_outcome: Optional[ExecutionOutcome] = None
    stop_timeout: float = 0


# ---------------------------------------------------------------------------
# Active process registry – allows external stop by task_id
# ---------------------------------------------------------------------------

_active_processes: dict[str, ActiveTask] = {}
"""Maps task_id → the task record whose subprocess is currently running."""

_active_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Track tasks stopped by the user so we can distinguish intentional stop
# from a genuine subprocess crash.
# ---------------------------------------------------------------------------

_user_stopped_tasks: set[str] = set()
"""Task IDs that were explicitly stopped by the user via /stop."""


class TaskStoppedError(Exception):
    """Raised when a task is explicitly stopped by the user.

    This is a *normal* termination, not a crash — callers should handle it
    gracefully (e.g. return ``{'status': 'stopped'}`` instead of 500).
    """

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f'Task {task_id} was stopped by the user.')


def register_process(task_id: str, proc: multiprocessing.Process, meta: Optional[ActiveTaskInfo] = None) -> None:
    info = meta or ActiveTaskInfo(task_id=task_id, kind='', started_at=datetime.now().isoformat())
    info = info.model_copy(update={'status': 'running'})
    with _active_lock:
        _active_processes[task_id] = ActiveTask(proc=proc, info=info)


def unregister_process(task_id: str) -> None:
    """Remove a finished / stopped subprocess from the registry."""
    with _active_lock:
        _active_processes.pop(task_id, None)


def active_task_ids() -> set[str]:
    """Return a snapshot of task_ids with a currently registered subprocess."""
    with _active_lock:
        return set(_active_processes.keys())


def task_is_cancelling(task_id: str) -> bool:
    """Return whether an active external task is awaiting cleanup."""
    with _active_lock:
        record = _active_processes.get(task_id)
        return bool(record and record.info.status == 'cancelling')


def list_active_tasks() -> List[dict]:
    """Return display metadata for every registered task (without process handles)."""
    with _active_lock:
        records = list(_active_processes.items())
    return [record.info.model_dump(mode='json') for _, record in records]


def stop_process(task_id: str) -> bool:
    """Terminate the subprocess associated with *task_id*.

    Returns True if a process was found and terminated, False otherwise.
    """
    with _active_lock:
        record = _active_processes.get(task_id)
        if record is not None and record.cancel_event is None:
            _active_processes.pop(task_id, None)
    if record is None:
        return False
    proc = record.proc
    if record.cancel_event is not None:
        if wait([proc.sentinel], timeout=0):
            return False
        with _active_lock:
            if _active_processes.get(task_id) is record:
                record.info = record.info.model_copy(update={'status': 'cancelling'})
        record.cancel_event.set()
        if not record.cleanup_event.wait(timeout=record.stop_timeout):
            return False
        outcome = record.external_outcome
        return bool(outcome and outcome.exit_reason == 'cancelled' and outcome.cleanup_confirmed)
    if wait([proc.sentinel], timeout=0):
        # Process already exited on its own; do NOT mark as user-stopped, or a
        # pending success result would be masked as TaskStoppedError. Treat the
        # stop request as a no-op.
        logger.info(f'Task {task_id} already finished before stop; ignoring stop request.')
        return False
    # Mark as user-stopped BEFORE terminating: the parent thread blocked in
    # run_in_subprocess polls the child every 0.1s and would otherwise observe
    # the death first and misreport it as a crash.
    _user_stopped_tasks.add(task_id)
    proc.terminate()
    # Only run_in_subprocess reaps the child. Concurrent join/is_alive calls
    # can race in waitpid and misclassify a process that has already exited.
    if not wait([proc.sentinel], timeout=3):
        proc.kill()
    if not wait([proc.sentinel], timeout=2):
        _user_stopped_tasks.discard(task_id)
        with _active_lock:
            _active_processes[task_id] = record
        return False
    logger.info(f'Task {task_id} stopped by user.')
    return True


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _capture_stderr() -> Iterator[TextIO]:
    """Redirect stderr to a temporary text file usable by nested subprocesses.

    The file stays open for the worker's entire task, including libraries
    that bind sys.stderr when lazily imported. Read it before leaving the
    context; exit restores sys.stderr and closes the temporary file.
    """
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace', buffering=1) as buf:
        original = sys.stderr
        sys.stderr = buf
        try:
            yield buf
        finally:
            sys.stderr = original


def _process_worker(func, result_queue, *args, **kwargs):
    """Target for multiprocessing.Process — executes *func* and posts result.

    stderr is captured and forwarded through the queue so the parent process
    can surface it even when the child crashes before *func* is reached.
    """
    with _capture_stderr() as stderr_buf:
        try:
            result = func(*args, **kwargs)
            result_queue.put({'status': 'success', 'result': result})
        except BaseException as e:
            # Task log handlers live in the child; the parent's logger only reaches the service console.
            # Exception messages may contain credentials echoed by an upstream provider.
            logger.error(f'Task failed ({type(e).__name__}). See the service console for exception details.')
            stderr_buf.flush()
            stderr_buf.seek(0)
            result_queue.put(
                {
                    'status': 'error',
                    'error': str(e),
                    'traceback': traceback.format_exc(),
                    'stderr': stderr_buf.read(),
                }
            )


def run_in_subprocess(func, *args, task_id=None, task_meta=None, **kwargs):
    """Run *func* in a child process and return its result (blocks caller).

    Returns the function's return value on success; raises on error.

    If *task_id* is provided the child process is registered in the active
    process registry so it can be terminated via :func:`stop_process` and
    listed via :func:`list_active_tasks`. *task_meta* carries the display
    identity (kind / model / endpoint / alias) stored alongside the handle.

    Design note — why polling instead of p.join() then queue.get():
    ``multiprocessing.Queue`` is backed by an OS pipe whose buffer is typically
    only 64 KB.  If the child calls ``queue.put()`` with a payload larger than
    that buffer it will *block* until the parent drains the pipe.  But if the
    parent is sitting in ``p.join()`` waiting for the child to exit first, both
    sides wait on each other forever — a classic deadlock.
    """
    # Use spawn context to avoid fork-based deadlocks on Linux and to
    # ensure consistent cross-platform behaviour (macOS defaults to spawn,
    # Linux defaults to fork).
    ctx = multiprocessing.get_context('spawn')
    result_queue = ctx.Queue()
    p = ctx.Process(target=_process_worker, args=(func, result_queue, *args), kwargs=kwargs)
    p.start()

    if task_id:
        register_process(task_id, p, meta=task_meta)

    res = None
    # Poll for the result while the child is alive so we continuously drain
    # the underlying pipe and never let queue.put() block in the child.
    while p.is_alive():
        try:
            res = result_queue.get(timeout=0.1)
            break  # Got the result; let the child finish normally.
        except queue.Empty:
            continue  # Child still running — keep draining.

    # Wait for the child to clean up after we have the result (or it crashed).
    p.join()

    if task_id:
        unregister_process(task_id)

    # Consume the user-stop marker exactly once so it never leaks, regardless
    # of which branch below handles the outcome.
    user_stopped = bool(task_id) and task_id in _user_stopped_tasks
    if task_id:
        _user_stopped_tasks.discard(task_id)

    if res is None:
        # The child exited without the poll loop seeing a result. Do one final
        # non-blocking drain in case the item arrived between the last loop
        # iteration and p.join() returning — a produced result always takes
        # priority over the user-stop marker.
        try:
            res = result_queue.get_nowait()
        except queue.Empty:
            res = None

    if res is not None:
        if res['status'] == 'error':
            # A user-initiated stop may surface as an error result when the
            # child converts SIGTERM into an exception — still a graceful stop.
            if user_stopped:
                raise TaskStoppedError(task_id)
            stderr_info = res.get('stderr', '')
            stderr_section = f'\n[stderr]\n{stderr_info}' if stderr_info.strip() else ''
            raise RuntimeError(f'Subprocess error: {res["error"]}\n{res.get("traceback", "")}{stderr_section}')
        return res['result']

    # If the task was explicitly stopped by the user, treat it as a graceful
    # termination — not a crash.
    if user_stopped:
        raise TaskStoppedError(task_id)

    # res is still None: the child exited without putting anything in the queue
    # (OOM, SIGKILL, import error, segfault, etc.).
    raise RuntimeError(
        f'Subprocess terminated unexpectedly (exit code {p.exitcode}). '
        'The child process may have crashed due to OOM, a missing import, '
        'GPU initialisation failure, or a signal (e.g. SIGKILL).'
    ) from None


# ---------------------------------------------------------------------------
# Task wrappers (thin shims kept for clarity / future extension)
# ---------------------------------------------------------------------------


def run_eval_wrapper(task_config: TaskConfig):
    """Run an evaluation task and return the result."""
    return run_task(task_config)


def run_perf_wrapper(perf_args: PerfArguments):
    """Run a performance benchmark and return the result."""
    return run_perf_benchmark(perf_args)


def _external_worker(
    context: ExecutionContext, invocation: ExternalInvocation, cancel_event: Any, start_event: Any
) -> ExecutionOutcome:
    while not start_event.wait(timeout=0.1):
        parent = multiprocessing.parent_process()
        if parent is not None and not parent.is_alive():
            return ExecutionOutcome(exit_reason='error', artifact_status='missing', cleanup_confirmed=True)
    return run_external(context, invocation, cancel_event=cancel_event)


def run_external_in_subprocess(
    context: ExecutionContext, invocation: ExternalInvocation, task_meta: Optional[ActiveTaskInfo] = None
) -> ExecutionOutcome:
    """Run an external task with registered cancellation and confirmed cleanup."""
    result = _run_controlled_subprocess(context, _external_worker, invocation, ExecutionOutcome, task_meta)
    assert isinstance(result, ExecutionOutcome)
    return result


def _single_aiperf_worker(
    context: ExecutionContext, request: AIPerfSingleRunRequest, cancel_event: Any, start_event: Any
) -> ExecutionOutcome:
    while not start_event.wait(timeout=0.1):
        parent = multiprocessing.parent_process()
        if parent is not None and not parent.is_alive():
            return ExecutionOutcome(exit_reason='error', artifact_status='missing', cleanup_confirmed=True)
    return run_single(request.arguments, context, request.input_file, cancel_event)


def run_single_aiperf_in_subprocess(
    context: ExecutionContext, request: AIPerfSingleRunRequest, task_meta: Optional[ActiveTaskInfo] = None
) -> ExecutionOutcome:
    """Run one validated AIPerf task under the same cancellation supervisor."""
    result = _run_controlled_subprocess(context, _single_aiperf_worker, request, ExecutionOutcome, task_meta)
    assert isinstance(result, ExecutionOutcome)
    return result


def _run_controlled_subprocess(
    context: ExecutionContext,
    worker: Callable[..., Any],
    payload: ExternalInvocation | AIPerfSingleRunRequest,
    result_type: Type[ExecutionOutcome],
    task_meta: Optional[ActiveTaskInfo],
) -> ExecutionOutcome:
    if context.runtime is None:
        raise ValueError('External runtime is required')
    ctx = multiprocessing.get_context('spawn')
    result_queue = ctx.Queue()
    cancel_event = ctx.Event()
    start_event = ctx.Event()
    proc = ctx.Process(target=_process_worker, args=(worker, result_queue, context, payload, cancel_event, start_event))
    info = task_meta or ActiveTaskInfo(task_id=context.task_id, kind='perf', started_at=datetime.now().isoformat())
    runtime = context.runtime
    record = ActiveTask(
        proc=proc,
        info=info.model_copy(update={'status': 'running'}),
        cancel_event=cancel_event,
        stop_timeout=runtime.graceful_timeout + runtime.terminate_timeout + runtime.kill_timeout + 8,
    )
    try:
        with _active_lock:
            if context.task_id in _active_processes:
                raise ValueError('Task is already running')
            proc.start()
            _active_processes[context.task_id] = record
    except BaseException:
        result_queue.close()
        raise
    result = None
    try:
        start_event.set()
        while proc.is_alive():
            try:
                result = result_queue.get(timeout=0.1)
                break
            except queue.Empty:
                continue
        proc.join()
        if result is None:
            try:
                result = result_queue.get_nowait()
            except queue.Empty:
                pass
        if result is None or result.get('status') != 'success' or not isinstance(result.get('result'), result_type):
            raise RuntimeError('External task ended without a confirmed outcome')
        record.external_outcome = result['result']
        return record.external_outcome
    except BaseException:
        cancel_event.set()
        if not wait([proc.sentinel], timeout=record.stop_timeout):
            proc.terminate()
            if not wait([proc.sentinel], timeout=2):
                proc.kill()
        proc.join()
        raise
    finally:
        with _active_lock:
            if _active_processes.get(context.task_id) is record:
                _active_processes.pop(context.task_id, None)
        record.cleanup_event.set()
        result_queue.close()


def serialize_result(result):
    """Convert Pydantic model objects (or containers of them) to plain dicts for JSON.

    Recursively walks dicts and lists, converting any Pydantic ``BaseModel``
    instances (``Report``, ``BenchmarkSummary``, ``PercentileResult``, etc.)
    to plain dicts via ``model_dump()`` / ``to_dict()``.
    """
    from pydantic import BaseModel

    if isinstance(result, BaseModel):
        # Report has a custom to_dict() that delegates to model_dump()
        if hasattr(result, 'to_dict'):
            return result.to_dict()
        return result.model_dump()
    if isinstance(result, dict):
        return {k: serialize_result(v) for k, v in result.items()}
    if isinstance(result, list):
        return [serialize_result(v) for v in result]
    return result
