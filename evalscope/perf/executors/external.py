"""Controlled POSIX external process execution for performance runners."""

import json
import multiprocessing
import os
import selectors
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Protocol

from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome, ExternalInvocation

_ENV_ALLOWLIST = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
_WATCHDOG = Path(__file__).with_name('_external_watchdog.py')


class Cancellation(Protocol):
    """Minimal interface shared by thread and multiprocessing events."""

    def is_set(self) -> bool:
        """Return whether cancellation was requested."""


def _send_control(fd: int, value: bytes) -> bool:
    try:
        os.write(fd, value)
        return True
    except BrokenPipeError:
        # A completed result takes precedence over a racing cancellation.
        return False


def supports_external_runner() -> bool:
    """Return whether the platform has an implemented process-group runner."""
    return os.name == 'posix' and sys.platform in ('darwin', 'linux') and os.access('/bin/ps', os.X_OK)


def run_external(
    context: ExecutionContext,
    invocation: ExternalInvocation,
    cancel_event: Optional[Cancellation] = None,
    on_started: Optional[Callable[[int], None]] = None,
    parent_liveness_fd: Optional[int] = None,
) -> ExecutionOutcome:
    """Run a prepared command with a watchdog and a process-group launch gate.

    The caller owns the supplied liveness descriptor. A multiprocessing worker
    automatically forwards its parent sentinel when one exists. This function
    is internal: neither the runtime nor the invocation is a Web request.
    """
    if not supports_external_runner():
        raise RuntimeError('External runner requires Linux or macOS and an executable /bin/ps')
    if context.runtime is None:
        raise ValueError('External runtime is required')
    parent = multiprocessing.parent_process()
    if parent_liveness_fd is None and parent is not None:
        parent_liveness_fd = parent.sentinel

    runtime = context.runtime
    env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    env.update({key: value.get_secret_value() for key, value in invocation.environment.items()})
    secrets = [value.get_secret_value() for value in invocation.environment.values()]
    secrets.extend(value.get_secret_value() for value in invocation.redactions)
    if any(len(value.encode()) > 4096 for value in secrets):
        raise ValueError('Redaction value exceeds the supported size')
    payload = {
        'command': {'executable': runtime.executable, 'arguments': invocation.arguments, 'environment': env},
        'working_dir': invocation.working_dir,
        'log_file': invocation.log_file,
        'max_log_bytes': invocation.max_log_bytes,
        'redactions': secrets,
        'graceful_timeout': runtime.graceful_timeout,
        'terminate_timeout': runtime.terminate_timeout,
        'kill_timeout': runtime.kill_timeout,
    }
    read_fd, write_fd = os.pipe()
    descriptors = [read_fd]
    if parent_liveness_fd is not None:
        descriptors.append(parent_liveness_fd)
    command = [sys.executable, str(_WATCHDOG), str(read_fd)]
    if parent_liveness_fd is not None:
        command.append(str(parent_liveness_fd))
    watcher = None
    pid = None
    try:
        watcher = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            pass_fds=tuple(descriptors),
            close_fds=True,
        )
        os.close(read_fd)
        read_fd = -1
        assert watcher.stdin is not None and watcher.stdout is not None
        watcher.stdin.write((json.dumps(payload) + '\n').encode())
        watcher.stdin.close()
        selector = selectors.DefaultSelector()
        selector.register(watcher.stdout, selectors.EVENT_READ)
        cancellation_sent = False
        result = None
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set() and not cancellation_sent:
                    _send_control(write_fd, b'C')
                    cancellation_sent = True
                if selector.select(timeout=0.1):
                    line = watcher.stdout.readline()
                    if not line:
                        break
                    message = json.loads(line)
                    if message.get('event') == 'started':
                        pid = int(message['pid'])
                        if on_started is not None:
                            on_started(pid)
                        if cancel_event is not None and cancel_event.is_set() and not cancellation_sent:
                            _send_control(write_fd, b'C')
                            cancellation_sent = True
                        if not cancellation_sent:
                            _send_control(write_fd, b'G')
                    elif message.get('event') == 'exited':
                        result = message
                        break
                    elif message.get('event') == 'error':
                        break
                if watcher.poll() is not None and not selector.select(timeout=0):
                    break
        finally:
            selector.close()
        watcher.wait()
        if result is None or result.get('cleanup_failed') or watcher.returncode != 0:
            return ExecutionOutcome(
                exit_reason='error', exit_code=result.get('exit_code') if result else None, artifact_status='missing'
            )
        reason = 'cancelled' if result['cancelled'] else ('completed' if result['exit_code'] == 0 else 'error')
        return ExecutionOutcome(
            exit_reason=reason, exit_code=result['exit_code'], artifact_status='missing', cleanup_confirmed=True
        )
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        os.close(write_fd)
        if watcher is not None and watcher.poll() is None:
            try:
                watcher.wait(timeout=runtime.graceful_timeout + runtime.terminate_timeout + runtime.kill_timeout + 3)
            except subprocess.TimeoutExpired:
                # Never signal an unverified, potentially reused process-group ID.
                # Leave the watchdog alive to finish its cleanup and fail closed.
                raise RuntimeError('External process cleanup could not be confirmed') from None
