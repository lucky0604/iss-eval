"""Single-run API preserves payload and supervision without exposing private paths."""

import json
import os
import shutil
from pathlib import Path

import pytest

from evalscope.perf.executors.models import ExecutionOutcome, ExternalRuntime
from evalscope.service.app import create_app
from evalscope.service.utils import log


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from evalscope.service.blueprints import perf

    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, '_aiperf_runtime', lambda: ExternalRuntime(executable='/aiperf'))
    return create_app(outputs=str(tmp_path)).test_client()


def _payload() -> dict:
    return {'engine': 'aiperf', 'aiperf': {
        'model': 'model', 'url': 'http://localhost/v1', 'tokenizer_path': '/tokenizer', 'api_key': 'fake-secret',
        'input_prompts': ['first prompt', 'second prompt'],
        'load': {'points': [{'concurrency': 2}], 'request_count': 2, 'output_token_limit': 7},
    }}


def test_single_task_materializes_private_inputs_and_hides_runtime(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.service.blueprints import perf

    calls = []

    def execute(context, request, task_meta, finalize):
        path = Path(request.input_file)
        assert path.stat().st_mode & 0o077 == 0
        assert [json.loads(line) for line in path.read_text().splitlines()] == [
            {'text': 'first prompt', 'output_length': 7}, {'text': 'second prompt', 'output_length': 7},
        ]
        calls.append((context, request.arguments, task_meta))
        raw = Path(context.output_dir) / 'raw' / 'aiperf'
        raw.mkdir(parents=True)
        fixture = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf'
        for name in ('profile_export_aiperf.json', 'profile_export.jsonl', 'phase_manifest.json'):
            shutil.copyfile(fixture / name, raw / name)
        outcome = ExecutionOutcome(exit_reason='completed', exit_code=0, cleanup_confirmed=True,
                                   artifact_status='available', raw_artifact_dir=str(raw))
        finalize(outcome)
        return outcome

    monkeypatch.setattr(perf, 'run_single_aiperf_in_subprocess', execute)
    response = client.post('/api/v1/perf/invoke', json=_payload(), headers={'EvalScope-Task-Id': 'single-task'})
    assert response.status_code == 200 and response.json['status'] == 'completed'
    assert calls[0][1].model == 'model' and calls[0][2].engine == 'aiperf'
    assert 'fake-secret' not in response.get_data(as_text=True)
    assert calls[0][0].output_dir not in response.get_data(as_text=True)
    manifest = json.loads((Path(calls[0][0].output_dir) / 'single_run.json').read_text())
    assert manifest['normalized'] == 'normalized' and manifest['status'] == 'completed'
    assert 'fake-secret' not in json.dumps(manifest) and 'first prompt' not in json.dumps(manifest)
    assert client.post('/api/v1/perf/invoke', json=_payload(), headers={'EvalScope-Task-Id': 'single-task'}).status_code == 409


def test_single_task_rejects_missing_or_mismatched_inputs(client) -> None:
    payload = _payload()
    payload['aiperf'].pop('input_prompts')
    assert client.post('/api/v1/perf/invoke', json=payload, headers={'EvalScope-Task-Id': 'missing'}).status_code == 400
    payload['aiperf']['input_prompts'] = ['one']
    assert client.post('/api/v1/perf/invoke', json=payload, headers={'EvalScope-Task-Id': 'mismatch'}).status_code == 400


def test_missing_required_telemetry_does_not_report_completed(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.perf.integrations.aiperf.preflight import NodePreflight
    from evalscope.service.blueprints import perf

    def execute(context, request, task_meta, finalize):
        raw = Path(context.output_dir) / 'raw' / 'aiperf'
        raw.mkdir(parents=True)
        fixture = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf'
        for name in ('profile_export_aiperf.json', 'profile_export.jsonl', 'phase_manifest.json'):
            shutil.copyfile(fixture / name, raw / name)
        outcome = ExecutionOutcome(exit_reason='completed', cleanup_confirmed=True,
                                   artifact_status='available', raw_artifact_dir=str(raw))
        finalize(outcome)
        return outcome

    monkeypatch.setattr(perf, 'run_single_aiperf_in_subprocess', execute)
    monkeypatch.setattr(perf, 'probe_nodes', lambda targets: [NodePreflight(
        node_id='prefill', role='prefill', engine='vllm', status='available', elapsed_ms=0,
    )])
    payload = _payload()
    payload['aiperf']['telemetry'] = {'required': True, 'targets': [
        {'node_id': 'prefill', 'role': 'prefill', 'engine': 'vllm', 'url': 'http://localhost:9090/metrics'}
    ]}
    response = client.post('/api/v1/perf/invoke', json=payload, headers={'EvalScope-Task-Id': 'missing-metrics'})
    assert response.status_code == 200 and response.json['status'] == 'error'


@pytest.mark.parametrize(('reason', 'cleaned', 'expected'), [
    ('cancelled', True, 'stopped'), ('cancelled', False, 'error'), ('error', True, 'error'),
])
def test_single_task_does_not_report_unconfirmed_cleanup_as_stopped(
    client, monkeypatch: pytest.MonkeyPatch, reason: str, cleaned: bool, expected: str
) -> None:
    from evalscope.service.blueprints import perf

    def execute(*args, **kwargs):
        outcome = ExecutionOutcome(exit_reason=reason, artifact_status='partial', cleanup_confirmed=cleaned)
        kwargs['finalize'](outcome)
        return outcome

    monkeypatch.setattr(perf, 'run_single_aiperf_in_subprocess', execute)
    response = client.post('/api/v1/perf/invoke', json=_payload(), headers={'EvalScope-Task-Id': 'single-result'})
    assert response.status_code == 200 and response.json['status'] == expected


@pytest.mark.skipif(not os.environ.get('EVALSCOPE_TEST_AIPERF'), reason='requires isolated AIPerf 0.13.0')
def test_http_single_task_reaches_real_aiperf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    from evalscope.service.blueprints import perf

    root = tmp_path / 'outputs'
    root.mkdir()
    tokenizer_path = tmp_path / 'tokenizer'
    backend = Tokenizer(models.WordLevel({'[UNK]': 0, 'first': 1, 'prompt': 2, 'second': 3}, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]').save_pretrained(tokenizer_path)
    fixture = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf' / 'mock_inference_server.py'
    capture = tmp_path / 'capture.jsonl'
    server = subprocess.Popen([sys.executable, str(fixture), '--capture', str(capture)], stdout=subprocess.PIPE, text=True)
    try:
        assert server.stdout is not None
        port = int(server.stdout.readline())
        monkeypatch.setattr(log, 'OUTPUT_DIR', str(root))
        monkeypatch.setattr(perf, 'OUTPUT_DIR', str(root))
        app = create_app(outputs=str(root))
        app.config['AIPERF_EXECUTABLE'] = os.environ['EVALSCOPE_TEST_AIPERF']
        payload = _payload()
        payload['aiperf']['url'] = f'http://127.0.0.1:{port}/v1'
        payload['aiperf']['tokenizer_path'] = str(tokenizer_path)
        payload['aiperf']['api_key'] = 'fixture-key'
        response = app.test_client().post('/api/v1/perf/invoke', json=payload,
                                          headers={'EvalScope-Task-Id': 'real-single-task'})
        assert response.status_code == 200 and response.json['status'] == 'completed', response.json
        sent = [json.loads(line) for line in capture.read_text().splitlines()]
        assert len(sent) == 2
        assert {item['body']['messages'][0]['content'] for item in sent} == {'first prompt', 'second prompt'}
        assert all(item['fixture_authorization_matches'] for item in sent)
        assert (root / 'real-single-task' / 'perf' / 'raw' / 'aiperf' / 'profile_export.jsonl').is_file()
        assert 'fixture-key' not in response.get_data(as_text=True)
        task_log = app.test_client().get('/api/v1/perf/log?task_id=real-single-task')
        assert task_log.status_code == 200 and task_log.json['total_lines'] > 0
        assert 'fixture-key' not in task_log.get_data(as_text=True)
    finally:
        server.terminate()
        server.wait(timeout=5)
        if server.stdout:
            server.stdout.close()


@pytest.mark.skipif(not os.environ.get('EVALSCOPE_TEST_AIPERF'), reason='requires isolated AIPerf 0.13.0')
def test_http_single_run_exposes_authenticated_pd_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    from evalscope.service.blueprints import perf, tasks
    from tests.perf.test_prefix_priming import _server

    root = tmp_path / 'outputs'
    root.mkdir()
    tokenizer_path = tmp_path / 'tokenizer'
    backend = Tokenizer(models.WordLevel({'[UNK]': 0, 'first': 1, 'second': 2, 'prompt': 3}, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]').save_pretrained(tokenizer_path)
    for module in (log, perf, tasks):
        monkeypatch.setattr(module, 'OUTPUT_DIR', str(root))
    app = create_app(outputs=str(root))
    app.config['AIPERF_EXECUTABLE'] = os.environ['EVALSCOPE_TEST_AIPERF']
    with _server(tmp_path / 'inference', 'slow_success') as (url, _), \
         _server(tmp_path / 'p', 'success', 'pd_vllm', 'p-secret') as (prefill, _), \
         _server(tmp_path / 'd', 'success', 'pd_sglang', 'd-secret') as (decode, _):
        payload = _payload()
        payload['aiperf'].update({'url': url, 'tokenizer_path': str(tokenizer_path), 'api_key': 'fixture-key',
                                  'deployment_mode': 'pd', 'telemetry': {
            'required': True, 'sample_interval_seconds': 0.1, 'max_scrape_gap_seconds': 1,
            'targets': [
                {'node_id': 'p', 'role': 'prefill', 'engine': 'vllm',
                 'url': prefill.removesuffix('/v1') + '/metrics', 'headers': {'Authorization': 'Bearer p-secret'}},
                {'node_id': 'd', 'role': 'decode', 'engine': 'sglang',
                 'url': decode.removesuffix('/v1') + '/metrics', 'headers': {'Authorization': 'Bearer d-secret'}},
            ],
        }})
        client = app.test_client()
        result = client.post('/api/v1/perf/invoke', json=payload, headers={'EvalScope-Task-Id': 'single-pd'})
    assert result.status_code == 200 and result.json['status'] == 'completed', result.json
    params = {'root_path': str(root), 'path': 'single-pd/perf'}
    detail = client.get('/api/v1/perf/detail', query_string=params)
    assert detail.status_code == 200 and detail.json['quality'] == 'partial'
    assert {row['node_id'] for row in detail.json['node_analysis']['series']} == {'p', 'd'}
    samples = client.get('/api/v1/perf/server-metrics', query_string={**params, 'node_id': 'p'})
    assert samples.status_code == 200 and samples.json['total'] > 0
    assert 'p-secret' not in detail.get_data(as_text=True) and 'd-secret' not in detail.get_data(as_text=True)
