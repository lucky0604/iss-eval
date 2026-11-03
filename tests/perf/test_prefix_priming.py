"""Primer barriers using a loopback-only inference fixture."""

import asyncio
import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from evalscope.perf.executors.arguments import AIPerfArguments
from evalscope.perf.experiments.prefix_workload import build_prefix_workload
from evalscope.perf.experiments.priming import prime_prefixes
from tests.perf.test_prefix_workload_plan import _arguments, _Tokenizer

SERVER = Path(__file__).parent / 'fixtures' / 'aiperf' / 'mock_inference_server.py'


@contextmanager
def _server(tmp_path: Path, mode: str) -> Iterator[tuple[str, Path]]:
    capture = tmp_path / 'requests.jsonl'
    process = subprocess.Popen([sys.executable, str(SERVER), '--capture', str(capture), '--mode', mode],
                               stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout is not None
        port = int(process.stdout.readline())
        yield f'http://127.0.0.1:{port}/v1', capture
    finally:
        process.terminate()
        process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()


def _config(url: str) -> AIPerfArguments:
    return AIPerfArguments(model='mock-model', url=url, api_key='fixture-key', tokenizer_path='/tokenizer',
                           load={'points': [{'concurrency': 1}], 'request_count': 6, 'output_token_limit': 4})


def test_all_primers_complete_before_ready(tmp_path: Path) -> None:
    workload = build_prefix_workload(_arguments(0.8), _Tokenizer(), list(range(200)))
    with _server(tmp_path, 'success') as (url, capture):
        result = asyncio.run(prime_prefixes(_config(url), workload))
    assert result.status == 'completed' and result.completed_prefix_ids == [0, 1]
    assert result.routing_coverage == 'unknown'
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert [request['body']['messages'][0]['content'] for request in requests] == [item.text for item in workload.primers]
    assert all(request['body']['max_completion_tokens'] == 1 for request in requests)
    assert all(request['fixture_authorization_matches'] for request in requests)


@pytest.mark.parametrize('mode,reason', [('http_error', 'http_error'), ('stream_error', 'invalid_stream'),
                                        ('unfinished', 'invalid_stream')])
def test_failure_stops_before_next_primer(tmp_path: Path, mode: str, reason: str) -> None:
    workload = build_prefix_workload(_arguments(0.8), _Tokenizer(), list(range(200)))
    with _server(tmp_path, mode) as (url, capture):
        result = asyncio.run(prime_prefixes(_config(url), workload))
    assert result.status == 'error' and result.reason == reason and result.failed_prefix_id == 0
    assert result.completed_prefix_ids == []
    assert len(capture.read_text().splitlines()) == 1


def test_cancellation_interrupts_primer_and_does_not_start_another(tmp_path: Path) -> None:
    workload = build_prefix_workload(_arguments(0.8), _Tokenizer(), list(range(200)))
    cancel = threading.Event()
    with _server(tmp_path, 'timeout') as (url, capture):
        def cancel_after_first_request() -> None:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not capture.exists():
                time.sleep(0.01)
            cancel.set()

        thread = threading.Thread(target=cancel_after_first_request)
        thread.start()
        result = asyncio.run(prime_prefixes(_config(url), workload, cancel_event=cancel))
        thread.join(timeout=5)
    assert result.status == 'cancelled' and not result.completed_prefix_ids
    assert len(capture.read_text().splitlines()) == 1


def test_zero_ratio_skips_priming_without_network() -> None:
    workload = build_prefix_workload(_arguments(0), _Tokenizer(), list(range(200)))
    result = asyncio.run(prime_prefixes(_config('http://127.0.0.1:1/v1'), workload))
    assert result.status == 'skipped' and result.routing_coverage == 'unknown'
