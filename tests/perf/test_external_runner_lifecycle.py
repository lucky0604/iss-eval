"""Exercise the real POSIX watchdog and its controlled process group."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from evalscope.perf.executors import _external_watchdog as watchdog
from evalscope.perf.executors import external
from evalscope.perf.executors.external import run_external
from evalscope.perf.executors.models import ExecutionContext, ExternalInvocation, ExternalRuntime

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='The external watchdog requires POSIX process groups')


def _context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(task_id='runner-test', output_dir=str(tmp_path), runtime=ExternalRuntime(
        executable=sys.executable, graceful_timeout=0.3, terminate_timeout=0.3, kill_timeout=2,
    ))


def _invocation(tmp_path: Path, script: str) -> ExternalInvocation:
    return ExternalInvocation(arguments=['-u', '-c', script], working_dir=str(tmp_path), log_file=str(tmp_path / 'run.log'))


def _wait_for(path: Path, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f'process did not create {path}')
        time.sleep(0.01)


def _wait_group_gone(pid: int, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            # On macOS a just-exited group can briefly be visible but not signalable.
            pass
        if time.monotonic() > deadline:
            raise AssertionError(f'process group {pid} survived cleanup')
        time.sleep(0.01)


@pytest.mark.parametrize('exit_code', [0, 7])
def test_exit_status_and_bounded_redacted_log(tmp_path: Path, exit_code: int) -> None:
    invocation = _invocation(tmp_path, (
        'import os,sys; '
        "os.write(1,b'abcse'); os.write(2,b'cretxyz\\xff'); "
        f"os.write(1,b'x'*1000000); sys.exit({exit_code})"
    ))
    invocation = ExternalInvocation(**{**invocation.model_dump(), 'redactions': ['secret'], 'max_log_bytes': 2048})
    outcome = run_external(_context(tmp_path), invocation)
    assert outcome.exit_reason == ('completed' if exit_code == 0 else 'error')
    assert outcome.exit_code == exit_code
    assert outcome.cleanup_confirmed
    content = (tmp_path / 'run.log').read_text()
    assert 'secret' not in content and '[REDACTED]' in content
    assert len(content.encode()) <= 2048
    assert (tmp_path / 'run.log').stat().st_mode & 0o077 == 0


def test_cancel_before_gate_does_not_start_workload(tmp_path: Path) -> None:
    marker = tmp_path / 'started'
    cancel = threading.Event()
    invocation = _invocation(tmp_path, f'from pathlib import Path; Path({str(marker)!r}).touch()')
    outcome = run_external(_context(tmp_path), invocation, cancel_event=cancel, on_started=lambda pid: cancel.set())
    assert outcome.exit_reason == 'cancelled'
    assert outcome.cleanup_confirmed
    assert not marker.exists()


def test_unsupported_platform_rejects_runner_before_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(external.sys, 'platform', 'win32')
    with pytest.raises(RuntimeError, match='Linux or macOS'):
        run_external(_context(tmp_path), _invocation(tmp_path, 'pass'))


def test_missing_process_inventory_rejects_runner_before_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(external.os, 'access', lambda path, mode: False)
    with pytest.raises(RuntimeError, match='/bin/ps'):
        run_external(_context(tmp_path), _invocation(tmp_path, 'pass'))


def test_guard_release_requires_a_complete_process_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        stdout = '100 100\n101 100\n'

    monkeypatch.setattr(watchdog.subprocess, 'run', lambda *args, **kwargs: Result())
    monkeypatch.setattr(watchdog.os, 'getpgid', lambda pid: 100)
    assert not watchdog._group_has_only_guard(100, 101)
    Result.stdout = '101 100\n'
    assert watchdog._group_has_only_guard(100, 101)
    Result.stdout = 'invalid process inventory\n'
    with pytest.raises(RuntimeError, match='Cannot verify'):
        watchdog._group_has_only_guard(100, 101)


def test_cancellation_kills_stubborn_descendants(tmp_path: Path) -> None:
    child_ready = tmp_path / 'child'
    parent_ready = tmp_path / 'parent'
    child = (
        'import signal,time,pathlib; '
        'signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        f'pathlib.Path({str(child_ready)!r}).touch(); time.sleep(60)'
    )
    script = (
        'import signal,subprocess,sys,time,pathlib; '
        'signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        f'subprocess.Popen([sys.executable,"-c",{child!r}]); '
        f'p=pathlib.Path({str(child_ready)!r}); '
        'exec("while not p.exists():\\n time.sleep(0.01)"); '
        f'pathlib.Path({str(parent_ready)!r}).touch(); time.sleep(60)'
    )
    cancel = threading.Event()
    pids = []
    results = []
    runner = threading.Thread(target=lambda: results.append(run_external(
        _context(tmp_path), _invocation(tmp_path, script), cancel_event=cancel, on_started=pids.append,
    )))
    runner.start()
    try:
        _wait_for(parent_ready)
        cancel.set()
        runner.join(timeout=10)
        assert not runner.is_alive()
        assert results[0].exit_reason == 'cancelled'
        _wait_group_gone(pids[0])
    finally:
        cancel.set()
        runner.join(timeout=10)


def test_parent_liveness_eof_cancels_workload(tmp_path: Path) -> None:
    ready = tmp_path / 'ready'
    read_fd, write_fd = os.pipe()
    results = []
    pids = []
    script = f'import pathlib,time; pathlib.Path({str(ready)!r}).touch(); time.sleep(60)'
    runner = threading.Thread(target=lambda: results.append(run_external(
        _context(tmp_path), _invocation(tmp_path, script), parent_liveness_fd=read_fd, on_started=pids.append,
    )))
    runner.start()
    try:
        _wait_for(ready)
        os.close(write_fd)
        write_fd = -1
        runner.join(timeout=10)
        assert not runner.is_alive()
        assert results[0].exit_reason == 'cancelled'
        _wait_group_gone(pids[0])
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        runner.join(timeout=10)
        os.close(read_fd)


def test_controller_sigkill_still_cleans_external_group(tmp_path: Path) -> None:
    ready = tmp_path / 'target-ready'
    target = f'import pathlib,time; pathlib.Path({str(ready)!r}).touch(); time.sleep(60)'
    controller = '''
import sys
from evalscope.perf.executors.external import run_external
from evalscope.perf.executors.models import ExecutionContext, ExternalInvocation, ExternalRuntime
path, target = sys.argv[1:]
context = ExecutionContext(task_id='killed-controller', output_dir=path, runtime=ExternalRuntime(
    executable=sys.executable, graceful_timeout=0.3, terminate_timeout=0.3, kill_timeout=2))
invocation = ExternalInvocation(arguments=['-u', '-c', target], working_dir=path, log_file=path + '/runner.log')
run_external(context, invocation, on_started=lambda pid: print(pid, flush=True))
'''
    process = subprocess.Popen([sys.executable, '-u', '-c', controller, str(tmp_path), target], stdout=subprocess.PIPE,
                               text=True)
    pid = None
    try:
        assert process.stdout is not None
        pid = int(process.stdout.readline())
        _wait_for(ready)
        process.kill()
        process.wait(timeout=5)
        _wait_group_gone(pid, timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()


def test_registration_failure_does_not_release_gate(tmp_path: Path) -> None:
    marker = tmp_path / 'workload-started'
    invocation = _invocation(tmp_path, f'from pathlib import Path; Path({str(marker)!r}).touch()')

    def fail_registration(pid: int) -> None:
        raise RuntimeError('registration failed')

    with pytest.raises(RuntimeError, match='registration failed'):
        run_external(_context(tmp_path), invocation, on_started=fail_registration)
    assert not marker.exists()


def test_normal_exit_drains_short_lived_descendant(tmp_path: Path) -> None:
    script = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(0.1)"])'
    outcome = run_external(_context(tmp_path), _invocation(tmp_path, script))
    assert outcome.exit_reason == 'completed'
    assert outcome.cleanup_confirmed


def test_normal_exit_with_lingering_descendant_is_not_success(tmp_path: Path) -> None:
    script = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])'
    groups = []
    outcome = run_external(_context(tmp_path), _invocation(tmp_path, script), on_started=groups.append)
    assert outcome.exit_reason == 'error'
    _wait_group_gone(groups[0])


def test_watchdog_crash_never_claims_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ready = tmp_path / 'ready'
    target = f'import pathlib,time; pathlib.Path({str(ready)!r}).touch(); time.sleep(60)'
    watcher_pids = []
    group_pids = []
    results = []
    cleaned = False
    original_popen = subprocess.Popen

    def track_watcher(*args: object, **kwargs: object) -> subprocess.Popen:
        process = original_popen(*args, **kwargs)
        watcher_pids.append(process.pid)
        return process

    monkeypatch.setattr(external.subprocess, 'Popen', track_watcher)
    runner = threading.Thread(target=lambda: results.append(run_external(
        _context(tmp_path), _invocation(tmp_path, target), on_started=group_pids.append,
    )))
    runner.start()
    try:
        _wait_for(ready)
        os.kill(watcher_pids[0], signal.SIGKILL)
        runner.join(timeout=10)
        assert not runner.is_alive()
        assert results[0].exit_reason == 'error'
        assert not results[0].cleanup_confirmed
        _wait_group_gone(group_pids[0], timeout=10)
        cleaned = True
    finally:
        if group_pids and not cleaned:
            try:
                os.killpg(group_pids[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
            _wait_group_gone(group_pids[0])
        runner.join(timeout=10)


def test_watchdog_crash_during_descendant_drain_still_cleans_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / 'descendant-ready'
    child = f'import pathlib,time; pathlib.Path({str(ready)!r}).touch(); time.sleep(60)'
    target = (
        'import pathlib,subprocess,sys,time; '
        f'subprocess.Popen([sys.executable,"-c",{child!r}]); '
        f'p=pathlib.Path({str(ready)!r}); '
        'exec("while not p.exists():\\n time.sleep(0.01)")'
    )
    context = _context(tmp_path)
    context.runtime.graceful_timeout = 2
    watchers = []
    groups = []
    results = []
    original_popen = subprocess.Popen

    def track_watcher(*args: object, **kwargs: object) -> subprocess.Popen:
        proc = original_popen(*args, **kwargs)
        watchers.append(proc.pid)
        return proc

    monkeypatch.setattr(external.subprocess, 'Popen', track_watcher)
    runner = threading.Thread(target=lambda: results.append(run_external(
        context, _invocation(tmp_path, target), on_started=groups.append,
    )))
    runner.start()
    try:
        _wait_for(ready)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(groups[0], 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            raise AssertionError('group leader did not exit')
        os.kill(watchers[0], signal.SIGKILL)
        runner.join(timeout=10)
        assert not runner.is_alive()
        assert results[0].exit_reason == 'error' and not results[0].cleanup_confirmed
        _wait_group_gone(groups[0], timeout=10)
    finally:
        if groups:
            try:
                os.killpg(groups[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
        runner.join(timeout=10)


def test_guard_failure_causes_watchdog_to_stop_target(tmp_path: Path) -> None:
    ready = tmp_path / 'ready'
    target = f'import pathlib,time; pathlib.Path({str(ready)!r}).touch(); time.sleep(60)'
    group_pids = []
    results = []
    runner = threading.Thread(target=lambda: results.append(run_external(
        _context(tmp_path), _invocation(tmp_path, target), on_started=group_pids.append,
    )))
    runner.start()
    try:
        _wait_for(ready)
        process_list = subprocess.check_output(['ps', '-ax', '-o', 'pid=,ppid=,pgid='], text=True)
        members = [tuple(map(int, line.split())) for line in process_list.splitlines() if len(line.split()) == 3]
        guards = [pid for pid, parent, group in members if parent == group_pids[0] and group == group_pids[0]]
        assert len(guards) == 1
        os.kill(guards[0], signal.SIGKILL)
        runner.join(timeout=10)
        assert not runner.is_alive()
        assert results[0].exit_reason == 'error'
        assert not results[0].cleanup_confirmed
        _wait_group_gone(group_pids[0])
    finally:
        if runner.is_alive() and group_pids:
            try:
                os.killpg(group_pids[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
        runner.join(timeout=10)
