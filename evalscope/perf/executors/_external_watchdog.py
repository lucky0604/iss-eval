"""Private POSIX watchdog for a single external process group.

The controller supplies invocation data on stdin and a separate liveness pipe.
The target first runs a gate, so no workload starts before registration is
acknowledged. This module intentionally has no EvalScope imports.
"""

import codecs
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_GATE = """import json, os, subprocess, sys
data = json.loads(sys.stdin.buffer.readline())
ready_r, ready_w = os.pipe()
guard = subprocess.Popen([sys.executable, data["guard_script"], str(data["guard_fd"]), str(ready_w),
                          str(data["graceful_timeout"]), str(data["terminate_timeout"])],
                         pass_fds=(data["guard_fd"], ready_w, data["guard_status_fd"]),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
os.write(data["guard_status_fd"], f"{guard.pid}\\n".encode())
os.close(ready_w)
os.close(data["guard_fd"])
os.close(data["guard_status_fd"])
if os.read(ready_r, 1) != b"R":
    sys.exit(1)
os.close(ready_r)
os.execvpe(data["executable"], [data["executable"], *data["arguments"]], data["environment"])
"""


class _LogWriter:
    def __init__(self, path: str, secrets: list[str], max_bytes: int) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.fchmod(fd, 0o600)
        self.file = os.fdopen(fd, 'ab', buffering=0)
        self.secrets = sorted({s.encode() for s in secrets if s}, key=len, reverse=True)
        self.pending = bytearray()
        self.width = max((len(s) for s in self.secrets), default=1)
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.remaining = max_bytes

    def write(self, data: bytes, final: bool = False) -> None:
        self.pending.extend(data)
        output = bytearray()
        offset = 0
        if not self.secrets:
            output.extend(self.pending)
            offset = len(self.pending)
        else:
            while offset < len(self.pending) and (final or len(self.pending) - offset >= self.width):
                secret = next((item for item in self.secrets if self.pending.startswith(item, offset)), None)
                if secret:
                    output.extend(b'[REDACTED]')
                    offset += len(secret)
                else:
                    output.append(self.pending[offset])
                    offset += 1
        del self.pending[:offset]
        text = self.decoder.decode(bytes(output), final=final).encode('utf-8')
        if self.remaining:
            chunk = text[: self.remaining].decode('utf-8', errors='ignore').encode('utf-8')
            self.file.write(chunk)
            self.remaining -= len(chunk)

    def close(self) -> None:
        try:
            self.write(b'', final=True)
        finally:
            self.file.close()


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        # A macOS group may briefly be visible but not signalable while its
        # last member is exiting; the loop still requires it to disappear.
        pass


def _group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _group_has_only_guard(group: int, guard: int) -> bool:
    """Check the group before releasing its last crash-cleanup anchor."""
    try:
        output = subprocess.run(
            ['/bin/ps', '-A', '-o', 'pid=', '-o', 'pgid='],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        ).stdout
        members = set()
        for line in output.splitlines():
            fields = line.split()
            if len(fields) != 2:
                raise ValueError('Unexpected process list')
            pid, pgid = map(int, fields)
            if pgid == group:
                members.add(pid)
        return members == {guard} and os.getpgid(guard) == group
    except (OSError, ValueError, subprocess.SubprocessError):
        raise RuntimeError('Cannot verify external process group membership') from None


def _send(message: dict) -> None:
    print(json.dumps(message), flush=True)


def _run(control_fd: int, parent_fd: Optional[int]) -> None:
    config = json.loads(sys.stdin.buffer.readline())
    writer = _LogWriter(config['log_file'], config['redactions'], config['max_log_bytes'])
    proc = None
    selector = selectors.DefaultSelector()
    cancelled = False
    failure = False
    result = None
    guard_read, guard_write = os.pipe()
    status_read, status_write = os.pipe()
    gate_guard_fd, gate_status_fd = guard_read, status_write
    try:
        proc = subprocess.Popen(
            [sys.executable, '-c', _GATE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=config['working_dir'],
            start_new_session=True,
            close_fds=True,
            pass_fds=(guard_read, status_write),
        )
        os.close(guard_read)
        guard_read = -1
        os.close(status_write)
        status_write = -1
        assert proc.stdout is not None and proc.stdin is not None
        selector.register(proc.stdout, selectors.EVENT_READ, 'output')
        selector.register(control_fd, selectors.EVENT_READ, 'control')
        selector.register(status_read, selectors.EVENT_READ, 'guard')
        if parent_fd is not None:
            selector.register(parent_fd, selectors.EVENT_READ, 'parent')
        _send({'event': 'started', 'pid': proc.pid})
        released = False
        deadline = None
        stage = 0
        output_open = True
        guard_released = False
        guard_gone = False
        guard_pid = None
        guard_message = b''
        drain_deadline = None
        while True:
            for key, _ in selector.select(timeout=0.05):
                if key.data == 'output':
                    data = os.read(key.fileobj.fileno(), 65536)
                    if data:
                        writer.write(data)
                    else:
                        selector.unregister(key.fileobj)
                        output_open = False
                elif key.data == 'parent':
                    cancelled = True
                    selector.unregister(key.fileobj)
                elif key.data == 'guard':
                    data = os.read(status_read, 128)
                    if data:
                        guard_message += data
                        if len(guard_message) > 32 or b'\n' not in guard_message:
                            failure = True
                        else:
                            try:
                                guard_pid = int(guard_message.strip())
                            except ValueError:
                                failure = True
                    else:
                        selector.unregister(status_read)
                        guard_gone = True
                        if not guard_released and not cancelled:
                            failure = True
                else:
                    data = os.read(control_fd, 128)
                    if not data or b'C' in data:
                        cancelled = True
                        selector.unregister(control_fd)
                    elif b'G' in data and not released:
                        gate_config = dict(config['command'])
                        gate_config.update(
                            {
                                'guard_script': str(Path(__file__).with_name('_external_guard.py')),
                                'guard_fd': gate_guard_fd,
                                'guard_status_fd': gate_status_fd,
                                'graceful_timeout': config['graceful_timeout'],
                                'terminate_timeout': config['terminate_timeout'],
                            }
                        )
                        proc.stdin.write((json.dumps(gate_config) + '\n').encode())
                        proc.stdin.close()
                        released = True
            code = proc.poll()
            now = time.monotonic()
            if code is not None and drain_deadline is None:
                drain_deadline = now + config['graceful_timeout']
            if code is not None and not guard_released and not guard_gone and guard_pid is not None:
                try:
                    if _group_has_only_guard(proc.pid, guard_pid):
                        os.write(guard_write, b'D')
                        os.close(guard_write)
                        guard_write = -1
                        guard_released = True
                except BrokenPipeError:
                    failure = True
                except RuntimeError:
                    failure = True
            # A normally exited leader may have left descendants behind.
            if (
                code is not None
                and not guard_released
                and _group_exists(proc.pid)
                and not cancelled
                and now >= drain_deadline
            ):
                failure = True
            if (cancelled or failure) and stage == 0:
                _signal_group(proc.pid, signal.SIGINT if cancelled else signal.SIGTERM)
                deadline = now + (config['graceful_timeout'] if cancelled else config['terminate_timeout'])
                stage = 1 if cancelled else 2
            elif stage and deadline is not None and now >= deadline and _group_exists(proc.pid):
                if stage == 1:
                    _signal_group(proc.pid, signal.SIGTERM)
                    deadline = now + config['terminate_timeout']
                    stage = 2
                elif stage == 2:
                    _signal_group(proc.pid, signal.SIGKILL)
                    deadline = now + config['kill_timeout']
                    stage = 3
                else:
                    failure = True
                    break
            if code is not None and not _group_exists(proc.pid) and not output_open:
                break
        code = proc.wait(timeout=config['kill_timeout'])
        result = {'event': 'exited', 'exit_code': code, 'cancelled': cancelled, 'cleanup_failed': failure}
    finally:
        selector.close()
        for fd in (guard_read, guard_write, status_read, status_write):
            if fd >= 0:
                os.close(fd)
        if proc is not None and _group_exists(proc.pid):
            _signal_group(proc.pid, signal.SIGKILL)
            if proc.poll() is None:
                proc.wait()
        writer.close()
    if result is not None:
        _send(result)


def main() -> None:
    try:
        _run(int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else None)
    except BaseException:
        # Details can contain credentials from an upstream command or response.
        try:
            _send({'event': 'error'})
        except (BrokenPipeError, OSError):
            pass
        sys.exit(1)


if __name__ == '__main__':
    main()
