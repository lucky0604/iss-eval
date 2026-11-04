"""Generic warmup traffic runs before the matrix and never enters its metrics."""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from evalscope.perf.executors.arguments import AIPerfArguments, WarmupConfiguration
from evalscope.perf.experiments.prefix_workload import PrefixWorkload, PrefixWorkloadArguments, build_prefix_workload
from evalscope.perf.experiments.warmup import run_warmup
from tests.perf.test_prefix_priming import _server
from tests.perf.test_prefix_workload_plan import _Tokenizer


def _config(url: str) -> AIPerfArguments:
    return AIPerfArguments(
        model='mock-model', url=url, api_key='fixture-key', tokenizer_path='/tokenizer',
        load={'points': [{'concurrency': 1}], 'request_count': 6, 'output_token_limit': 4},
    )


def _warmup(request_count: int = 6, concurrency: int = 2, output_limit: int = 3) -> WarmupConfiguration:
    return WarmupConfiguration(
        request_count=request_count,
        concurrency=concurrency,
        total_input_tokens=20,
        output_token_limit=output_limit,
    )


def _workload(request_count: int = 6) -> PrefixWorkload:
    return build_prefix_workload(
        PrefixWorkloadArguments(
            target_ratio=0,
            total_input_tokens=20,
            request_count=request_count,
            prefix_pool_size=1,
            seed=42,
            namespace='warmup:plan',
            tokenizer_id='fixture',
            template_id='fixture-chat',
        ),
        _Tokenizer(),
        list(range(200)),
    )


def test_warmup_completes_with_bounded_output(tmp_path: Path) -> None:
    workload = _workload()
    with _server(tmp_path, 'success') as (url, capture):
        result = asyncio.run(run_warmup(_config(url), _warmup(), workload))
    assert result.status == 'completed' and result.reason is None
    assert result.completed_requests == result.request_count == 6 and result.failed_requests == 0
    assert result.concurrency == 2 and result.total_input_tokens == 20 and result.output_token_limit == 3
    sent = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(sent) == 6
    assert {request['body']['max_completion_tokens'] for request in sent} == {3}
    assert all(request['fixture_authorization_matches'] for request in sent)
    assert {request['body']['messages'][0]['content'] for request in sent} == {item.text for item in workload.requests}


@pytest.mark.parametrize(
    'mode,reason',
    [('http_error', 'http_error'), ('stream_error', 'invalid_stream'), ('unfinished', 'invalid_stream')],
)
def test_warmup_failures_are_reported_not_scored(tmp_path: Path, mode: str, reason: str) -> None:
    with _server(tmp_path, mode) as (url, _):
        result = asyncio.run(run_warmup(_config(url), _warmup(), _workload()))
    assert result.status == 'failed' and result.reason == reason
    assert result.completed_requests == 0
    assert result.completed_requests + result.failed_requests == result.request_count


def test_cancellation_stops_warmup_without_waiting_for_every_request(tmp_path: Path) -> None:
    cancel = threading.Event()
    with _server(tmp_path, 'timeout') as (url, capture):

        def cancel_after_first_request() -> None:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not capture.exists():
                time.sleep(.01)
            cancel.set()

        thread = threading.Thread(target=cancel_after_first_request)
        thread.start()
        result = asyncio.run(run_warmup(_config(url), _warmup(), _workload(), cancel))
        thread.join(timeout=10)
    assert result.status == 'cancelled' and result.reason == 'cancelled'
    assert result.completed_requests == 0 and result.failed_requests < result.request_count


def test_invalid_workload_timeout_and_headers_are_rejected() -> None:
    config = _config('http://127.0.0.1:1/v1')
    with pytest.raises(ValueError, match='request count'):
        asyncio.run(run_warmup(config, _warmup(request_count=5), _workload(6)))
    for timeout in (0, -1, True, float('nan'), float('inf')):
        with pytest.raises(ValueError, match='timeout'):
            asyncio.run(run_warmup(config, _warmup(), _workload(), None, timeout))
    with_headers = AIPerfArguments(
        model='mock-model', url='http://127.0.0.1:1/v1', tokenizer_path='/tokenizer',
        load={'points': [{'concurrency': 1}], 'request_count': 6, 'output_token_limit': 4},
        headers={'X-Api-Key': 'secret'},
    )
    with pytest.raises(ValueError, match='headers'):
        asyncio.run(run_warmup(with_headers, _warmup(), _workload()))
