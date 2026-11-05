"""Fail-safe inside the target process group when its watchdog disappears."""

import os
import signal
import sys
import time


def main() -> None:
    control_fd, ready_fd = int(sys.argv[1]), int(sys.argv[2])
    graceful_timeout, terminate_timeout = float(sys.argv[3]), float(sys.argv[4])
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(ready_fd, b'R')
    os.close(ready_fd)
    command = os.read(control_fd, 1)
    if command == b'D':
        return
    # This process is a member of the target's dedicated session, so the
    # process-group ID cannot be reused until this guard itself has exited.
    group = os.getpgrp()
    os.killpg(group, signal.SIGINT)
    time.sleep(graceful_timeout)
    os.killpg(group, signal.SIGTERM)
    time.sleep(terminate_timeout)
    os.killpg(group, signal.SIGKILL)


if __name__ == '__main__':
    main()
