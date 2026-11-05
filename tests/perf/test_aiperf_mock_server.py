"""Offline checks for the AIPerf integration fixture."""

import json
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

SERVER = Path(__file__).parent / 'fixtures' / 'aiperf' / 'mock_inference_server.py'
FIXTURES = SERVER.parent


def test_recorded_aiperf_exports_keep_phase_and_availability() -> None:
    summary = json.loads((FIXTURES / 'profile_export_aiperf.json').read_text())
    manifest = json.loads((FIXTURES / 'phase_manifest.json').read_text())
    server_metrics = json.loads((FIXTURES / 'server_metrics_export.json').read_text())
    records = [json.loads(line) for line in (FIXTURES / 'profile_export.jsonl').read_text().splitlines()]
    missing = [json.loads(line) for line in (FIXTURES / 'missing_usage_profile_export.jsonl').read_text().splitlines()]

    assert (summary['aiperf_version'], summary['schema_version']) == ('0.13.0', '1.4')
    assert (server_metrics['aiperf_version'], server_metrics['schema_version']) == ('0.13.0', '1.1')
    assert [(phase['phase_kind'], phase['successful_request_count']) for phase in manifest['phases']] == [
        ('warmup', 1), ('profiling', 2)
    ]
    assert [record['metadata']['phase_kind'] for record in records] == ['warmup', 'profiling', 'profiling']
    assert all('usage_prompt_tokens' in record['metrics'] for record in records)
    assert all('usage_prompt_tokens' not in record['metrics'] for record in missing)
    assert all('input_sequence_length' in record['metrics'] for record in missing)
    assert server_metrics['metrics_phase'] == 'profiling'
    assert server_metrics['metrics']['mock_requests']['type'] == 'counter'


def test_recorded_errors_and_partial_run_are_not_all_successful() -> None:
    failed = [json.loads(line) for line in (FIXTURES / 'http_error_profile_export.jsonl').read_text().splitlines()]
    partial = json.loads((FIXTURES / 'partial_profile_export_aiperf.json').read_text())
    records = [json.loads(line) for line in (FIXTURES / 'partial_profile_export.jsonl').read_text().splitlines()]
    assert len(failed) == 2 and all(record['error']['code'] == 503 for record in failed)
    assert partial['is_complete'] is True
    assert partial['completed_request_count']['avg'] == 2
    assert partial['request_count']['avg'] == 1
    assert partial['request_error_rate']['avg'] == 50
    assert sum('error' in record for record in records) == 1


def test_recorded_server_time_series_retains_each_source() -> None:
    samples = [json.loads(line) for line in (FIXTURES / 'multi_server_metrics_export.jsonl').read_text().splitlines()]
    assert {sample['endpoint_url'] for sample in samples} == {
        f'http://127.0.0.1:{port}/metrics' for port in (18080, 18081, 18082)
    }
    assert all(sample['timestamp_ns'] > 0 and 'mock_requests' in sample['metrics'] for sample in samples)


def test_recorded_native_multi_run_baseline_is_readable() -> None:
    from evalscope.perf.utils.report.perf_data import RunLoader

    runs = RunLoader.load_all(str(FIXTURES / 'native'), with_requests=True)
    assert {run.dir_name for run in runs} == {'parallel_1_number_2', 'parallel_2_number_2'}
    assert all(len(run.requests) == 2 for run in runs)


@pytest.mark.parametrize('mode', ['success', 'cache_usage', 'missing_usage', 'http_error', 'stream_error'])
def test_mock_server_captures_requests_and_serves_metrics(tmp_path: Path, mode: str) -> None:
    capture = tmp_path / 'requests.jsonl'
    process = subprocess.Popen(
        [sys.executable, str(SERVER), '--capture', str(capture), '--mode', mode],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        port = int(process.stdout.readline())
        base = f'http://127.0.0.1:{port}'
        body = {'model': 'fixture-model', 'messages': [{'role': 'user', 'content': 'fixture'}], 'stream': True}
        request = Request(
            base + '/v1/chat/completions', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}
        )
        if mode == 'http_error':
            with pytest.raises(HTTPError) as error:
                urlopen(request, timeout=5)
            assert error.value.code == 503
        else:
            with urlopen(request, timeout=5) as response:
                result = response.read().decode()
            assert ('"usage"' in result) == (mode in ('success', 'cache_usage'))
            if mode == 'cache_usage':
                assert '"cached_tokens": 6' in result
            assert ('fixture error' in result) == (mode == 'stream_error')
        assert json.loads(capture.read_text())['body'] == body
        with urlopen(base + '/metrics', timeout=5) as response:
            assert 'mock_requests_total{role="prefill"} 1' in response.read().decode()
    finally:
        process.terminate()
        process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()


@pytest.mark.parametrize('mode,expected', [
    ('normal', ('mock_requests_total', 'mock_requests_total')),
    ('missing', ('', '')),
    ('reset', ('} 100', '} 1')),
    ('delayed', ('', 'mock_requests_total')),
])
def test_mock_metrics_modes(tmp_path: Path, mode: str, expected: tuple[str, str]) -> None:
    process = subprocess.Popen(
        [sys.executable, str(SERVER), '--capture', str(tmp_path / 'requests'), '--metrics-mode', mode],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        url = f'http://127.0.0.1:{int(process.stdout.readline())}/metrics'
        for value in expected:
            with urlopen(url, timeout=5) as response:
                body = response.read().decode()
            assert (value in body) if value else (body == '')
    finally:
        process.terminate()
        process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
