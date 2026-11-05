"""Realtime client counters come only from matching pinned-AIPerf log records."""

from pathlib import Path

import pytest

from evalscope.perf.integrations.aiperf.client_progress import read_client_progress


def test_client_progress_parses_complete_matching_records_and_preserves_unknown(tmp_path: Path) -> None:
    log = tmp_path / 'raw' / 'aiperf' / 'logs' / 'aiperf.log'
    log.parent.mkdir(parents=True)
    header = '2026-11-03 20:27:50.638 - records_manager - INFO - [realtime 00:03 profiling]\n'
    row = ('2026-11-03 20:27:50.638 - records_manager - INFO -   rps=7.9 (avg 7.9)  '
           'tput_in=8/s  tput_out=16/s  done=24 ok=24 err=0\n')
    log.write_text(header + row + '2026-11-03 20:27:52.645 - records_manager - INFO - [realtime 00:05 profiling]\n')
    result = read_client_progress(tmp_path)
    assert result is not None and result.completed_records == 24 and result.rounded_requests_per_second == 7.9
    assert result.sent_requests is None and result.sent_at_ns is None
    with log.open('a') as file:
        file.write('2026-11-03 20:27:54.950 - PhaseRunner - NOTICE - Phase profiling (profiling) '
                   'sending complete | sent=60, completed=59, in_flight=1 | sessions: sent=60, completed=59\n')
    result = read_client_progress(tmp_path)
    assert result is not None and result.sent_requests == 60 and result.sent_at_ns > result.sampled_at_ns


def test_client_progress_rejects_ambiguous_or_redirected_logs(tmp_path: Path) -> None:
    log = tmp_path / 'raw' / 'aiperf' / 'logs' / 'aiperf.log'
    log.parent.mkdir(parents=True)
    log.write_text('2026-11-03 20:27:50.638 - records_manager - INFO - [realtime 00:03 profiling]\n'
                   '2026-11-03 20:27:50.639 - records_manager - INFO -   rps=7.9 (avg 7.9)  '
                   'tput_in=8/s  tput_out=16/s  done=24 ok=24 err=0\n')
    assert read_client_progress(tmp_path) is None
    log.unlink()
    log.symlink_to(tmp_path / 'outside')
    with pytest.raises(ValueError, match='redirected'):
        read_client_progress(tmp_path)
