"""Bounded parsing of the pinned AIPerf 0.13.0 client realtime log."""

import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field

from evalscope.utils import BaseArgument

_STAMP = r'(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})'
_HEADER = re.compile(rf'^{_STAMP} - records_manager - INFO - \[realtime \d+:\d+ profiling\]$')
_SUMMARY = re.compile(
    rf'^{_STAMP} - records_manager - INFO -\s+rps=(?P<rps>-|\d+(?:\.\d+)?) '
    r'\(avg (?:-|\d+(?:\.\d+)?)\)\s+tput_in=(?:-|[\d,]+)/s\s+tput_out=(?:-|[\d,]+)/s\s+'
    r'done=(?P<done>[\d,]+) ok=(?P<ok>[\d,]+) err=(?P<err>[\d,]+)$'
)
_SENT = re.compile(
    rf'^{_STAMP} - PhaseRunner - NOTICE - '
    r'Phase profiling \(profiling\) sending complete \| sent=(?P<sent>[\d,]+), completed=[\d,]+, in_flight=[\d,]+'
)
_TAIL_BYTES = 128 * 1024


class ClientProgress(BaseArgument):
    """AIPerf client counters, distinct from all server node metrics."""

    source: Literal['aiperf_0.13.0_realtime_log'] = 'aiperf_0.13.0_realtime_log'
    sampled_at_ns: int = Field(ge=0)
    completed_records: int = Field(ge=0)
    successful_records: int = Field(ge=0)
    error_records: int = Field(ge=0)
    sent_requests: Optional[int] = Field(default=None, ge=0)
    sent_at_ns: Optional[int] = Field(default=None, ge=0)
    rounded_requests_per_second: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)


def _timestamp_ns(value: str) -> int:
    timestamp = datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f').astimezone().astimezone(timezone.utc)
    delta = timestamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def read_client_progress(run_dir: Path) -> Optional[ClientProgress]:
    """Read only a bounded tail of the run's private raw log; unknown remains unknown."""
    path = run_dir.resolve(strict=True) / 'raw' / 'aiperf' / 'logs' / 'aiperf.log'
    if path.parent.resolve() != path.parent or path.is_symlink():
        raise ValueError('AIPerf log path cannot be redirected')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('AIPerf log is not a regular file')
        start = max(0, info.st_size - _TAIL_BYTES)
        file.seek(start)
        data = file.read(_TAIL_BYTES)
    if not data.endswith(b'\n'):
        data = data.rsplit(b'\n', 1)[0] if b'\n' in data else b''
    lines = data.decode('utf-8', errors='replace').splitlines()
    if start and lines:
        lines = lines[1:]
    latest = None
    sent = sent_at = None
    for index, line in enumerate(lines):
        sent_match = _SENT.match(line)
        if sent_match:
            try:
                timestamp = _timestamp_ns(sent_match.group('stamp'))
                if timestamp >= 0:
                    sent = int(sent_match.group('sent').replace(',', ''))
                    sent_at = timestamp
            except (ValueError, OverflowError, OSError):
                pass
        header = _HEADER.fullmatch(line)
        if header is None or index + 1 >= len(lines):
            continue
        match = None
        for following in lines[index + 1 : index + 12]:
            if _HEADER.fullmatch(following):
                break
            candidate = _SUMMARY.fullmatch(following)
            if candidate is not None and candidate.group('stamp') == header.group('stamp'):
                match = candidate
                break
        if match is None:
            continue
        done, ok, err = (int(match.group(name).replace(',', '')) for name in ('done', 'ok', 'err'))
        if ok + err != done:
            continue
        try:
            timestamp = _timestamp_ns(match.group('stamp'))
        except (ValueError, OverflowError, OSError):
            continue
        if timestamp < 0:
            continue
        rate = None if match.group('rps') == '-' else float(match.group('rps'))
        latest = ClientProgress(
            sampled_at_ns=timestamp,
            completed_records=done,
            successful_records=ok,
            error_records=err,
            rounded_requests_per_second=rate,
        )
    if latest is not None:
        latest.sent_requests = sent
        latest.sent_at_ns = sent_at
    return latest
