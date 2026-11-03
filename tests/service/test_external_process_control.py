"""Service-level tests of external task cancellation and parent death."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome, ExternalInvocation, ExternalRuntime
from evalscope.service.utils import process

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='External process groups require POSIX')


def _inputs(tmp_path: Path, task_id: str) -> tuple[ExecutionContext, ExternalInvocation, Path]:
    marker = tmp_path / 'ready'
    context = ExecutionContext(task_id=task_id, output_dir=str(tmp_path), runtime=ExternalRuntime(
        executable=sys.executable, graceful_timeout=0.3, terminate_timeout=0.3, kill_timeout=2,
    ))
    code = f'import os,pathlib,time; pathlib.Path({str(marker)!r}).write_text(str(os.getpgrp())); time.sleep(60)'
    invocation = ExternalInvocation(arguments=['-c', code], working_dir=str(tmp_path), log_file=str(tmp_path / 'log'))
    return context, invocation, marker


def _wait_marker(marker: Path, timeout: float = 15) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return int(marker.read_text())
        except (FileNotFoundError, ValueError):
            time.sleep(0.01)
    raise AssertionError('external workload did not start')


def _wait_group_gone(pid: int, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        time.sleep(0.01)
    raise AssertionError(f'process group {pid} still exists')


def test_stop_waits_for_confirmed_external_cleanup(tmp_path: Path) -> None:
    context, invocation, marker = _inputs(tmp_path, 'external-stop')
    results = []
    worker = threading.Thread(target=lambda: results.append(process.run_external_in_subprocess(context, invocation)))
    worker.start()
    try:
        group = _wait_marker(marker)
        assert context.task_id in process.active_task_ids()
        assert process.stop_process(context.task_id)
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert results[0].exit_reason == 'cancelled' and results[0].cleanup_confirmed
        _wait_group_gone(group)
        assert not process.stop_process(context.task_id)
    finally:
        if worker.is_alive():
            process.stop_process(context.task_id)
            worker.join(timeout=10)


def test_task_remains_active_during_result_finalization(tmp_path: Path) -> None:
    context, invocation, _ = _inputs(tmp_path, 'external-finalizing')
    invocation.arguments = ['-c', 'pass']
    observed = []

    def finalize(outcome: ExecutionOutcome) -> None:
        observed.append(context.task_id in process.active_task_ids())

    process._run_controlled_subprocess(context, process._external_worker, invocation, ExecutionOutcome, None, finalize)
    assert observed == [True]
    assert context.task_id not in process.active_task_ids()


def test_service_sigkill_triggers_parent_liveness_cleanup(tmp_path: Path) -> None:
    marker = tmp_path / 'ready'
    child_code = f'import os,pathlib,time; pathlib.Path({str(marker)!r}).write_text(str(os.getpgrp())); time.sleep(60)'
    service = '''
import sys
from evalscope.perf.executors.models import ExecutionContext, ExternalInvocation, ExternalRuntime
from evalscope.service.utils.process import run_external_in_subprocess
if __name__ == '__main__':
    path, code = sys.argv[1:]
    context = ExecutionContext(task_id='parent-death', output_dir=path, runtime=ExternalRuntime(
        executable=sys.executable, graceful_timeout=0.3, terminate_timeout=0.3, kill_timeout=2))
    invocation = ExternalInvocation(arguments=['-c', code], working_dir=path, log_file=path + '/log')
    run_external_in_subprocess(context, invocation)
'''
    parent = subprocess.Popen([sys.executable, '-c', service, str(tmp_path), child_code])
    group = None
    cleaned = False
    try:
        group = _wait_marker(marker)
        parent.kill()
        parent.wait(timeout=5)
        _wait_group_gone(group)
        cleaned = True
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if group is not None and not cleaned:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_worker_sigkill_does_not_report_confirmed_stop(tmp_path: Path) -> None:
    context, invocation, marker = _inputs(tmp_path, 'worker-death')
    errors = []

    def invoke() -> None:
        try:
            process.run_external_in_subprocess(context, invocation)
        except RuntimeError as error:
            errors.append(error)

    worker = threading.Thread(target=invoke)
    worker.start()
    group = None
    cleaned = False
    try:
        group = _wait_marker(marker)
        with process._active_lock:
            registered = process._active_processes[context.task_id]
        registered.proc.kill()
        worker.join(timeout=10)

        assert not worker.is_alive()
        assert errors and 'without a confirmed outcome' in str(errors[0])
        assert not process.stop_process(context.task_id)
        _wait_group_gone(group)
        cleaned = True
    finally:
        if group is not None and not cleaned:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        worker.join(timeout=10)


def test_status_preserves_cancellation_while_cleanup_is_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.service.app import create_app
    from evalscope.service.blueprints import perf, tasks

    monkeypatch.setattr(perf, 'stop_process', lambda task_id: False)
    monkeypatch.setattr(perf, 'task_is_cancelling', lambda task_id: True)
    response = create_app().test_client().post('/api/v1/perf/stop?task_id=external-pending')
    assert response.status_code == 202 and response.json['status'] == 'cancelling'
    record = {'task_id': 'external-pending', 'kind': 'perf', 'engine': 'aiperf', 'status': 'cancelling'}
    assert tasks._with_progress(record)['status'] == 'cancelling'
    record['engine'] = None
    assert tasks._with_progress(record)['status'] == 'cancelling'
