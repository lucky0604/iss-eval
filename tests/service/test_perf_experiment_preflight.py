"""Cache experiment capability and side-effect-free plan endpoints."""

import json
import os
import shutil
from pathlib import Path

import pytest

from evalscope.perf.executors.models import ExternalRuntime
from evalscope.perf.experiments.orchestrator import CacheExperimentOutcome
from evalscope.service.app import create_app
from evalscope.service.utils import log
from tests.perf.test_experiment_planner import _config


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    return create_app(outputs=str(tmp_path)).test_client()


def test_engine_capabilities_do_not_expose_runtime(client) -> None:
    response = client.get('/api/v1/perf/engines')
    assert response.status_code == 200
    engines = {item['engine']: item for item in response.json['engines']}
    assert engines['evalscope']['available']
    assert not engines['aiperf']['available'] and engines['aiperf']['version'] is None
    assert 'executable' not in response.get_data(as_text=True)


def test_preflight_uses_same_plan_without_network_or_secrets(client, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Tokenizer:
        tokenizer_hash = 'tokenizer-hash'
        template_hash = 'template-hash'

        def __init__(self, path: str) -> None:
            assert path == '/tokenizer'

    monkeypatch.setattr('evalscope.service.blueprints.perf.HuggingFacePrefixTokenizer', _Tokenizer)
    payload = {'engine': 'aiperf', 'aiperf': _config().model_dump(mode='json')}
    payload['aiperf']['api_key'] = 'secret'
    response = client.post('/api/v1/perf/validate', json=payload, headers={'EvalScope-Task-Id': 'plan-test'})
    assert response.status_code == 200
    assert response.json['plan']['profiling_requests'] == 180
    assert response.json['plan']['primer_requests'] == 24
    assert len(response.json['plan']['subruns']) == 18
    assert not response.json['engine_available']
    assert 'secret' not in response.get_data(as_text=True)


def test_preflight_rejects_bad_task_or_wrong_engine(client) -> None:
    payload = {'engine': 'evalscope', 'native': {'model': 'model'}}
    assert client.post('/api/v1/perf/validate', json=payload).status_code == 400
    assert client.post('/api/v1/perf/validate', json=payload,
                       headers={'EvalScope-Task-Id': '../escape'}).status_code == 400
    assert client.post('/api/v1/perf/validate', json=payload,
                       headers={'EvalScope-Task-Id': 'task'}).status_code == 400


def test_invoke_uses_validated_plan_and_rejects_stale_preview(client, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.service.blueprints import perf

    class _Tokenizer:
        tokenizer_hash = 'tokenizer-hash'
        template_hash = 'template-hash'

        def __init__(self, path: str) -> None:
            pass

    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'HuggingFacePrefixTokenizer', _Tokenizer)
    monkeypatch.setattr(perf, '_aiperf_runtime', lambda: ExternalRuntime(executable='/aiperf'))
    calls = []

    def execute(context: object, arguments: object, task_meta: object, finalize: object) -> CacheExperimentOutcome:
        calls.append((context, arguments, task_meta))
        outcome = CacheExperimentOutcome(status='completed', quality='partial', plan_hash=context.expected_plan_hash,
                                         completed_subruns=18, total_subruns=18, cleanup_confirmed=True)
        finalize(outcome)
        return outcome

    monkeypatch.setattr(perf, 'run_cache_experiment_in_subprocess', execute)
    payload = {'engine': 'aiperf', 'aiperf': _config().model_dump(mode='json')}
    headers = {'EvalScope-Task-Id': 'plan-test'}
    plan = client.post('/api/v1/perf/validate', json=payload, headers=headers).json['plan']
    headers['EvalScope-Plan-Hash'] = '0' * 64
    assert client.post('/api/v1/perf/invoke', json=payload, headers=headers).status_code == 409
    assert not calls and not (tmp_path / 'plan-test').exists()
    headers['EvalScope-Plan-Hash'] = plan['plan_hash']
    response = client.post('/api/v1/perf/invoke', json=payload, headers=headers)
    assert response.status_code == 200 and response.json['status'] == 'completed'
    assert calls[0][0].expected_plan_hash == plan['plan_hash']
    assert calls[0][2].engine == 'aiperf'
    assert json.loads((tmp_path / 'plan-test' / 'task_info.json').read_text())['status'] == 'completed'
    assert client.post('/api/v1/perf/invoke', json=payload, headers=headers).status_code == 409


@pytest.mark.parametrize('status', ['stopped', 'error'])
def test_partial_experiment_is_discoverable_after_stop_or_failure(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    from evalscope.perf.experiments.planner import plan_cache_experiment
    from evalscope.service.blueprints import tasks

    monkeypatch.setattr(tasks, 'OUTPUT_DIR', str(tmp_path))
    plan = plan_cache_experiment(_config(), 'partial-task', 'tokenizer', 'template')
    run = tmp_path / 'partial-task' / 'perf'
    item = plan.subruns[0]
    subrun = run / 'runs' / item.subrun_id
    subrun.mkdir(parents=True)
    (run / 'experiment_plan.json').write_text(plan.model_dump_json())
    state = {'schema_version': '1.0', 'plan_hash': plan.plan_hash, 'status': status,
             'completed_subruns': 0, 'total_subruns': len(plan.subruns), 'quality': 'invalid',
             'subruns': [{'subrun_id': row.subrun_id, 'status': 'error' if row == item else 'not_started',
                          'scenario': f'runs/{row.subrun_id}/scenario.json' if row == item else None}
                         for row in plan.subruns]}
    (run / 'experiment.json').write_text(json.dumps(state))
    (subrun / 'scenario.json').write_text(json.dumps({
        'status': 'error', 'target_ratio': item.target_ratio,
        'workload': {'requests': [{'shared_tokens': 4, 'input_tokens': 10}]},
        'quality_reasons': ['profiling_failed', '<script>alert(1)</script>'],
        'normalized': 'normalized',
    }))
    from evalscope.perf.results.aiperf_import import import_aiperf_run
    from evalscope.perf.results.models import AIPerfImportArguments

    raw = subrun / 'raw' / 'aiperf'
    raw.mkdir(parents=True)
    fixture = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf'
    shutil.copyfile(fixture / 'profile_export.jsonl', raw / 'profile_export.jsonl')
    import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(subrun / 'normalized')))
    (run.parent / 'task_info.json').write_text(json.dumps({'kind': 'perf', 'engine': 'aiperf', 'status': status}))
    listing = client.get('/api/v1/perf/experiments')
    assert listing.status_code == 200 and listing.json['experiments'][0]['status'] == status
    task = client.get('/api/v1/tasks/partial-task')
    assert task.status_code == 200 and task.json['report_url'] == '/perf-experiment/partial-task'
    assert task.json['html_report_url'] == '/api/v1/perf/experiments/partial-task/report'
    response = client.get(f'/api/v1/perf/experiments/partial-task/runs/{item.subrun_id}')
    assert response.status_code == 200
    assert response.json['generated_ratio'] == 0.4
    assert response.json['cache']['availability'] == 'unavailable' and response.json['cache'].get('ratio') is None
    report = client.get('/api/v1/perf/experiments/partial-task/report')
    assert report.status_code == 200 and b'0.4' in report.data and b'unavailable' in report.data
    assert b'<script>' not in report.data and b'&lt;script&gt;' in report.data
    assert report.headers['Content-Security-Policy'] == "default-src 'none'"
    assert client.get('/api/v1/perf/experiments/partial-task/runs/r999-l999-n999').status_code == 400


@pytest.mark.skipif(not os.environ.get('EVALSCOPE_TEST_AIPERF'), reason='requires isolated AIPerf 0.13.0')
def test_http_invoke_reaches_real_aiperf_with_local_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    from evalscope.service.blueprints import perf
    from tests.perf.test_prefix_priming import _server

    root = tmp_path / 'outputs'
    root.mkdir()
    tokenizer_path = tmp_path / 'tokenizer'
    vocab = {'[UNK]': 0, 'header': 1, 'tail': 2, 'assistant': 3, **{f't{i}': i + 4 for i in range(200)}}
    backend = Tokenizer(models.WordLevel(vocab, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]')
    tokenizer.chat_template = "header {{ messages[0]['content'] }} tail assistant"
    tokenizer.save_pretrained(tokenizer_path)
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(root))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(root))
    app = create_app(outputs=str(root))
    app.config['AIPERF_EXECUTABLE'] = os.environ['EVALSCOPE_TEST_AIPERF']
    client = app.test_client()
    with _server(tmp_path, 'success') as (url, capture):
        payload = {'engine': 'aiperf', 'aiperf': {
            'model': 'mock-model', 'url': url, 'tokenizer_path': str(tokenizer_path), 'api_key': 'fixture-key',
            'cache_experiment': {'target_ratios': [0.8], 'total_input_tokens': 20, 'prefix_pool_size': 1},
            'load': {'points': [{'concurrency': 1}], 'request_count': 2, 'output_token_limit': 2},
        }}
        headers = {'EvalScope-Task-Id': 'real-api-task'}
        preview = client.post('/api/v1/perf/validate', json=payload, headers=headers)
        assert preview.status_code == 200 and preview.json['engine_available']
        headers['EvalScope-Plan-Hash'] = preview.json['plan']['plan_hash']
        response = client.post('/api/v1/perf/invoke', json=payload, headers=headers)
    assert response.status_code == 200, response.json
    assert response.json['status'] == 'completed' and response.json['result']['quality'] == 'partial'
    assert response.json['result']['plan_hash'] == preview.json['plan']['plan_hash']
    assert len(capture.read_text().splitlines()) == 3
    manifest = json.loads((root / 'real-api-task' / 'perf' / 'experiment.json').read_text())
    assert manifest['completed_subruns'] == 1 and manifest['status'] == 'completed'
    base = '/api/v1/perf/experiments/real-api-task'
    listing = client.get('/api/v1/perf/experiments?limit=1')
    assert listing.status_code == 200 and listing.json['total'] == 1
    assert listing.json['experiments'][0]['task_id'] == 'real-api-task'
    assert client.get('/api/v1/perf/experiments?limit=501').status_code == 400
    experiment = client.get(base)
    assert experiment.status_code == 200 and experiment.json['state']['completed_subruns'] == 1
    subrun = experiment.json['plan']['subruns'][0]['subrun_id']
    scenario = client.get(f'{base}/runs/{subrun}')
    assert scenario.status_code == 200, scenario.json
    assert scenario.json['target_ratio'] == 0.8 and scenario.json['generated_ratio'] == 0.8
    assert scenario.json['cache'].get('ratio') is None
    requests = client.get(f'{base}/runs/{subrun}/requests?limit=1')
    assert requests.status_code == 200 and requests.json['total'] == 2
    assert len(requests.json['requests']) == 1
    metrics = client.get(f'{base}/runs/{subrun}/metrics')
    assert metrics.status_code == 200
    assert client.get(f'{base}/runs/{subrun}/requests?limit=501').status_code == 400
    assert client.get(f'{base}/runs/r999-l999-n999').status_code == 400


def test_optional_metrics_preflight_is_read_only_and_filters_labels(client, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.service.blueprints import perf
    from tests.perf.test_prefix_priming import _server

    class Tokenizer:
        tokenizer_hash = 'tokenizer-hash'
        template_hash = 'template-hash'

        def __init__(self, path: str) -> None:
            pass

    monkeypatch.setattr(perf, 'HuggingFacePrefixTokenizer', Tokenizer)
    with _server(tmp_path / 'p', 'success', 'pd_vllm', 'p-only') as (url, inference_capture):
        config = _config().model_dump(mode='json')
        config['api_key'] = 'model-only'
        config['telemetry'] = {'required': True, 'targets': [{
            'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': url.removesuffix('/v1') + '/metrics',
            'labels': {'model': 'fixture'}, 'headers': {'Authorization': 'Bearer p-only'},
        }]}
        payload = {'engine': 'aiperf', 'aiperf': config}
        headers = {'EvalScope-Task-Id': 'probe-task'}
        preview = client.post('/api/v1/perf/validate?probe_metrics=1', json=payload, headers=headers)
        assert preview.status_code == 200, preview.json
        assert preview.json['metrics_probe'] == 'completed' and preview.json['nodes'][0]['status'] == 'available'
        assert 'vllm:request_prefill_time_seconds' in preview.json['nodes'][0]['metric_names']
        assert 'p-only' not in preview.get_data(as_text=True) and 'model-only' not in preview.get_data(as_text=True)
        assert not inference_capture.exists()
        metrics = [json.loads(line) for line in (tmp_path / 'p' / 'metrics.jsonl').read_text().splitlines()]
        assert metrics and all(row['authorization_matches'] for row in metrics)
        payload['aiperf']['telemetry']['targets'][0]['labels'] = {'model': 'wrong'}
        failed = client.post('/api/v1/perf/validate?probe_metrics=1', json=payload, headers=headers)
        assert failed.status_code == 200 and failed.json['nodes'][0]['status'] == 'missing'
        monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
        monkeypatch.setattr(perf, '_aiperf_runtime', lambda: ExternalRuntime(executable='/aiperf'))
        rejected = client.post('/api/v1/perf/invoke', json=payload, headers=headers)
        assert rejected.status_code == 400 and 'preflight failed' in rejected.json['error']
        assert not (tmp_path / 'probe-task').exists() and not inference_capture.exists()


def test_missing_required_node_is_rejected_before_task_creation(client, tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.service.blueprints import perf

    class Tokenizer:
        tokenizer_hash = 'tokenizer-hash'
        template_hash = 'template-hash'

        def __init__(self, path: str) -> None:
            pass

    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'HuggingFacePrefixTokenizer', Tokenizer)
    monkeypatch.setattr(perf, '_aiperf_runtime', lambda: ExternalRuntime(executable='/aiperf'))
    config = _config().model_dump(mode='json')
    config['telemetry'] = {'required': True, 'targets': [{
        'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': 'http://127.0.0.1:1/metrics',
    }]}
    response = client.post('/api/v1/perf/invoke', json={'engine': 'aiperf', 'aiperf': config},
                           headers={'EvalScope-Task-Id': 'missing-node'})
    assert response.status_code == 400 and response.json['nodes'][0]['status'] == 'unreachable'
    assert not (tmp_path / 'missing-node').exists()


def test_metrics_probe_distinguishes_malformed_from_unreachable(tmp_path: Path) -> None:
    from evalscope.perf.executors.arguments import TelemetryTarget
    from evalscope.perf.integrations.aiperf.preflight import probe_nodes
    from tests.perf.test_prefix_priming import _server

    with _server(tmp_path / 'malformed', 'success', 'malformed') as (url, _):
        nodes = probe_nodes([
            TelemetryTarget(node_id='bad', role='prefill', engine='vllm', url=url.removesuffix('/v1') + '/metrics'),
            TelemetryTarget(node_id='offline', role='decode', engine='sglang', url='http://127.0.0.1:1/metrics'),
        ])
    assert [node.status for node in nodes] == ['invalid', 'unreachable']
    assert nodes[0].reason == 'invalid_prometheus_payload' and nodes[1].reason == 'metrics_probe_failed'
