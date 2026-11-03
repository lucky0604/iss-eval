"""Cache experiment capability and side-effect-free plan endpoints."""

import hashlib
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


class _GeometryTokenizer:
    def candidate_ids(self, limit: int) -> list[int]:
        return [1, 2][:limit]

    def decode(self, tokens: list[int]) -> str:
        return ''.join(chr(64 + value) for value in tokens)

    def render(self, text: str) -> list[int]:
        return [0, *map(ord, text), 0]


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
    class _Tokenizer(_GeometryTokenizer):
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


def test_preflight_rejects_ratio_that_cannot_fit_the_chat_template(client, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    class Tokenizer(_GeometryTokenizer):
        tokenizer_hash = 'tokenizer-hash'
        template_hash = 'template-hash'

        def __init__(self, path: str) -> None:
            pass

    monkeypatch.setattr('evalscope.service.blueprints.perf.HuggingFacePrefixTokenizer', Tokenizer)
    config = _config().model_dump(mode='json')
    config['cache_experiment'].update({'target_ratios': [.9], 'total_input_tokens': 4})
    response = client.post('/api/v1/perf/validate', json={'engine': 'aiperf', 'aiperf': config},
                           headers={'EvalScope-Task-Id': 'impossible'})
    assert response.status_code == 400 and 'ratio' in response.json['error']
    assert not (tmp_path / 'impossible').exists()


def test_plan_configuration_is_verified_and_legacy_plan_remains_readable(client, tmp_path: Path) -> None:
    from evalscope.perf.experiments.planner import plan_cache_experiment

    plan = plan_cache_experiment(_config(), 'identity', 'tokenizer', 'template')
    root = tmp_path / 'identity' / 'perf'
    root.mkdir(parents=True)
    plan_data = plan.model_dump(mode='json')
    state = {'schema_version': '1.0', 'plan_hash': plan.plan_hash, 'status': 'stopped', 'quality': 'partial',
             'completed_subruns': 0, 'total_subruns': len(plan.subruns),
             'subruns': [{'subrun_id': row.subrun_id, 'status': 'not_started'} for row in plan.subruns]}
    (root / 'experiment.json').write_text(json.dumps(state))
    (root / 'experiment_plan.json').write_text(json.dumps(plan_data))
    response = client.get('/api/v1/perf/experiments/identity')
    assert response.status_code == 200
    assert response.json['plan']['configuration']['cache']['total_input_tokens'] == 100
    assert 'secret' not in response.get_data(as_text=True)
    plan_data['configuration']['cache']['total_input_tokens'] = 101
    (root / 'experiment_plan.json').write_text(json.dumps(plan_data))
    assert client.get('/api/v1/perf/experiments/identity').status_code == 400
    plan_data.pop('configuration')
    (root / 'experiment_plan.json').write_text(json.dumps(plan_data))
    legacy = client.get('/api/v1/perf/experiments/identity')
    assert legacy.status_code == 200 and legacy.json['plan'].get('configuration') is None


def test_live_metrics_use_current_planned_subrun_and_reject_redirected_files(client, tmp_path: Path) -> None:
    from evalscope.perf.executors.arguments import TelemetryTarget
    from evalscope.perf.experiments.planner import plan_cache_experiment
    from evalscope.perf.integrations.aiperf.live import LiveRecorder

    plan = plan_cache_experiment(_config(), 'live-task', 'tokenizer', 'template')
    item = plan.subruns[0]
    root = tmp_path / 'live-task' / 'perf'
    run = root / 'runs' / item.subrun_id
    run.mkdir(parents=True)
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    state = {'schema_version': '1.0', 'plan_hash': plan.plan_hash, 'status': 'running', 'phase': 'profiling',
             'current_subrun': item.subrun_id, 'completed_subruns': 0, 'total_subruns': len(plan.subruns),
             'subruns': [{'subrun_id': row.subrun_id, 'status': 'running' if row == item else 'not_started'}
                         for row in plan.subruns]}
    (root / 'experiment.json').write_text(json.dumps(state))
    target = TelemetryTarget(node_id='p', role='prefill', engine='vllm', url='http://localhost/metrics')
    recorder = LiveRecorder(run / 'live_metrics.json', [target], 1)
    recorder.record(target, b'# TYPE vllm:num_requests_waiting gauge\nvllm:num_requests_waiting 0\n')
    response = client.get('/api/v1/perf/live/live-task')
    assert response.status_code == 200
    assert response.json['current_subrun'] == item.subrun_id
    assert response.json['target_ratio'] == item.target_ratio
    assert response.json['nodes'][0]['gauges'][0]['value'] == 0
    (run / 'live_metrics.json').unlink()
    (run / 'live_metrics.json').symlink_to(tmp_path / 'outside.json')
    assert client.get('/api/v1/perf/live/live-task').status_code == 400
    state['current_subrun'] = 'r999-l999-n999'
    (root / 'experiment.json').write_text(json.dumps(state))
    assert client.get('/api/v1/perf/live/live-task').status_code == 400


def test_html_streams_the_complete_matrix_with_node_provenance(client, tmp_path: Path) -> None:
    from evalscope.perf.experiments.planner import plan_cache_experiment
    from evalscope.perf.results.aiperf_import import import_aiperf_run
    from evalscope.perf.results.models import AIPerfImportArguments

    config = _config()
    config.load.repetitions = 17
    plan = plan_cache_experiment(config, 'full-report', 'tokenizer', 'template')
    root = tmp_path / 'full-report' / 'perf'
    first = plan.subruns[0]
    run = root / 'runs' / first.subrun_id
    run.mkdir(parents=True)
    raw = run / 'raw'
    raw.mkdir()
    fixture = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf' / 'profile_export.jsonl'
    template = json.loads(fixture.read_text().splitlines()[1])
    records = []
    for cached, latency in ((0, 1), (2, 5)):
        row = json.loads(json.dumps(template))
        row['metrics']['usage_prompt_cache_read_tokens'] = {'unit': 'tokens', 'value': cached}
        row['metrics']['request_latency']['value'] = latency
        records.append(row)
    (raw / 'profile_export.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in records))
    shutil.copyfile(fixture.parent / 'profile_export_aiperf.json', raw / 'profile_export_aiperf.json')
    import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(run / 'normalized')))
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    state = {'schema_version': '1.0', 'plan_hash': plan.plan_hash, 'status': 'completed',
             'completed_subruns': len(plan.subruns), 'total_subruns': len(plan.subruns), 'quality': 'partial',
             'subruns': [{'subrun_id': row.subrun_id, 'status': 'completed',
                          'scenario': f'runs/{row.subrun_id}/scenario.json' if row == first else None}
                         for row in plan.subruns]}
    (root / 'experiment.json').write_text(json.dumps(state))
    (run / 'scenario.json').write_text(json.dumps({'status': 'completed', 'target_ratio': first.target_ratio,
        'workload': {'total_input_tokens': 100, 'requests': [{'input_tokens': 100, 'shared_tokens': 0,
                                                              'sha256': 'a' * 64}]},
        'normalized': 'normalized', 'node_analysis': 'node_analysis.json',
        'quality_reasons': ['test-window']}))
    (run / 'node_analysis.json').write_text(json.dumps({'availability': 'partial', 'max_gap_ns': 10,
        'window_source': 'observed_request_bounds', 'cache_ratios': [{'node_id': 'p', 'role': 'prefill',
        'labels': {}, 'availability': 'available', 'hit_tokens': 8, 'lookup_tokens': 10, 'ratio': .8}]}))
    api = client.get(f'/api/v1/perf/experiments/full-report/runs/{first.subrun_id}')
    assert api.status_code == 200 and api.json['node_analysis']['cache_ratios'][0]['ratio'] == .8
    assert api.json['input_tokens_per_request'] == 100
    assert api.json['request_hashes_sha256'] == hashlib.sha256(('a' * 64).encode()).hexdigest()
    repetitions = client.get(f'/api/v1/perf/experiments/full-report/runs/{first.subrun_id}/repetitions')
    assert repetitions.status_code == 200 and repetitions.json['total'] == 17
    actual = next(item for item in repetitions.json['repetitions'] if item['subrun_id'] == first.subrun_id)
    expected = next(metric for metric in api.json['result']['metrics'] if metric['key'] == 'time_to_first_token')
    assert actual['metric'] == expected
    groups = client.get(f'/api/v1/perf/experiments/full-report/runs/{first.subrun_id}/request-cache-groups')
    assert groups.status_code == 200 and groups.json['groups'][1]['latency']['p99'] == .005
    report = client.get('/api/v1/perf/experiments/full-report/report')
    assert report.status_code == 200 and report.is_streamed
    html = report.get_data(as_text=True)
    assert len(plan.subruns) == 102 and plan.subruns[-1].subrun_id in html
    assert 'lookup-token hit ratio:' in html and '0.8' in html and 'observed_request_bounds' in html
    assert 'some_cached_tokens' in html and '0.005' in html and 'exact nearest-rank' in html
    assert 'Request records (all phases)' in html and template['metadata']['x_request_id'] in html
    assert api.json['request_hashes_sha256'] in html
    assert 'Showing the first' not in html and 'not per-request P/D timings' in html


def test_html_streams_request_records_beyond_one_page(client, tmp_path: Path) -> None:
    from evalscope.perf.experiments.planner import plan_cache_experiment
    from evalscope.perf.results.aiperf_import import import_aiperf_run
    from evalscope.perf.results.models import AIPerfImportArguments

    config = _config()
    config.load.request_count = 502
    plan = plan_cache_experiment(config, 'report-pages', 'tokenizer', 'template')
    root = tmp_path / 'report-pages' / 'perf'
    item = plan.subruns[0]
    run = root / 'runs' / item.subrun_id
    raw = run / 'raw'
    raw.mkdir(parents=True)
    fixture = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf' / 'profile_export.jsonl'
    row = json.loads(fixture.read_text().splitlines()[1])
    with (raw / 'profile_export.jsonl').open('w') as file:
        for index in range(502):
            row['metadata']['x_request_id'] = f'page-record-{index}'
            file.write(json.dumps(row) + '\n')
    import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(run / 'normalized')))
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    (root / 'experiment.json').write_text(json.dumps({'schema_version': '1.0', 'plan_hash': plan.plan_hash,
        'status': 'stopped', 'completed_subruns': 0, 'total_subruns': len(plan.subruns), 'quality': 'partial',
        'subruns': [{'subrun_id': value.subrun_id, 'status': 'error' if value == item else 'not_started',
                     'scenario': f'runs/{value.subrun_id}/scenario.json' if value == item else None}
                    for value in plan.subruns]}))
    (run / 'scenario.json').write_text(json.dumps({'status': 'error', 'target_ratio': item.target_ratio,
                                                    'workload': {'requests': []}, 'normalized': 'normalized'}))
    report = client.get('/api/v1/perf/experiments/report-pages/report')
    html = report.get_data(as_text=True)
    assert report.status_code == 200 and 'page-record-0' in html and 'page-record-501' in html


def test_invoke_uses_validated_plan_and_rejects_stale_preview(client, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.service.blueprints import perf

    class _Tokenizer(_GeometryTokenizer):
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
def test_web_payload_reaches_real_three_ratio_aiperf_and_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    from evalscope.service.blueprints import perf
    from tests.perf.test_prefix_priming import _server

    project = Path(__file__).parents[2]
    tsx = project / 'evalscope' / 'web' / 'node_modules' / '.bin' / 'tsx'
    if not tsx.is_file():
        pytest.skip('requires installed Web dependencies')
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
        command = [str(tsx), '--tsconfig', str(project / 'evalscope/web/tsconfig.app.json'),
                   str(project / 'tests/perf/fixtures/aiperf/web_payload.ts'), url, str(tokenizer_path)]
        payload = json.loads(subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout)
        assert payload['aiperf']['cache_experiment']['target_ratios'] == [0, .8, .9]
        headers = {'EvalScope-Task-Id': 'web-matrix'}
        preview = client.post('/api/v1/perf/validate', json=payload, headers=headers)
        assert preview.status_code == 200 and preview.json['engine_available']
        assert len(preview.json['plan']['subruns']) == 3 and preview.json['plan']['profiling_requests'] == 6
        headers['EvalScope-Plan-Hash'] = preview.json['plan']['plan_hash']
        result = client.post('/api/v1/perf/invoke', json=payload, headers=headers)
    assert result.status_code == 200 and result.json['status'] == 'completed', result.json
    assert result.json['result']['plan_hash'] == preview.json['plan']['plan_hash']
    captured = [json.loads(line) for line in capture.read_text().splitlines()]
    cursor = 0
    base = '/api/v1/perf/experiments/web-matrix'
    experiment = client.get(base)
    assert experiment.status_code == 200 and experiment.json['state']['completed_subruns'] == 3
    for item in preview.json['plan']['subruns']:
        cursor += item['primer_requests']
        observed = captured[cursor:cursor + item['request_count']]
        cursor += item['request_count']
        document = json.loads((root / 'web-matrix' / 'perf' / 'runs' / item['subrun_id'] / 'scenario.json').read_text())
        actual = {hashlib.sha256(row['body']['messages'][0]['content'].encode()).hexdigest() for row in observed}
        assert actual == {row['sha256'] for row in document['workload']['requests']}
        assert document['workload']['total_input_tokens'] == 40
        detail = client.get(f"{base}/runs/{item['subrun_id']}")
        assert detail.status_code == 200 and detail.json['target_ratio'] == item['target_ratio']
        expected = sum(row['shared_tokens'] for row in document['workload']['requests']) / sum(
            row['input_tokens'] for row in document['workload']['requests']
        )
        assert detail.json['generated_ratio'] == expected
        assert detail.json['cache'].get('ratio') is None
    assert cursor == len(captured)
    report = client.get(base + '/report').get_data(as_text=True)
    assert all(item['subrun_id'] in report for item in preview.json['plan']['subruns'])


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

    class Tokenizer(_GeometryTokenizer):
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

    class Tokenizer(_GeometryTokenizer):
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


def test_repetition_pages_cover_the_full_matching_group(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.perf.experiments.planner import plan_cache_experiment
    from evalscope.perf.results.models import MetricStatistics, PerfRunResult

    config = _config()
    config.cache_experiment.target_ratios = [.0, .8]
    config.load.points = config.load.points[:1]
    config.load.repetitions = 61
    plan = plan_cache_experiment(config, 'repetitions', 'tokenizer', 'template')
    root = tmp_path / 'repetitions' / 'perf'
    root.mkdir(parents=True)
    states = []
    for row in plan.subruns:
        available = row.repetition != 60
        states.append({'subrun_id': row.subrun_id, 'status': 'completed' if available else 'error',
                       'scenario': f'runs/{row.subrun_id}/scenario.json' if available else None})
        if available:
            run = root / 'runs' / row.subrun_id
            (run / 'normalized').mkdir(parents=True)
            (run / 'scenario.json').write_text(json.dumps({'target_ratio': row.target_ratio, 'normalized': 'normalized'}))
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    (root / 'experiment.json').write_text(json.dumps({'schema_version': '1.0', 'plan_hash': plan.plan_hash,
        'status': 'completed', 'completed_subruns': len(states), 'total_subruns': len(states), 'subruns': states}))

    def load_result(path: str) -> PerfRunResult:
        repetition = int(Path(path).parent.name.split('-n')[1])
        return PerfRunResult(executor_version='0.13.0', quality='invalid' if repetition == 59 else 'complete',
                             profiling_requests=1, profiling_successes=1, profiling_errors=0,
                             metrics=[MetricStatistics(key='time_to_first_token', source='aiperf', scope='request',
                                                       unit='s', value=float(repetition), count=1)])

    damaged = next(row for row in plan.subruns if row.target_ratio == .8 and row.repetition == 58)
    (root / 'runs' / damaged.subrun_id / 'scenario.json').write_text('{invalid')
    monkeypatch.setattr('evalscope.service.perf_experiments.load_aiperf_run', load_result)
    row = next(row for row in plan.subruns if row.target_ratio == .8)
    url = f'/api/v1/perf/experiments/repetitions/runs/{row.subrun_id}/repetitions'
    first = client.get(url, query_string={'offset': 0, 'limit': 50}).get_json()
    second = client.get(url, query_string={'offset': 50, 'limit': 50}).get_json()
    assert first['total'] == second['total'] == 61
    assert [item['repetition'] for item in first['repetitions'] + second['repetitions']] == list(range(61))
    assert second['repetitions'][8]['unavailable_reason'] == 'result_unavailable'
    assert second['repetitions'][9]['quality'] == 'invalid'
    assert second['repetitions'][10].get('metric') is None
    assert second['repetitions'][10]['unavailable_reason'] == 'scenario_unavailable'
    assert client.get(url, query_string={'limit': 101}).status_code == 400
    assert client.get(url, query_string={'metric': 'unknown'}).status_code == 400
    assert client.get(url.replace(row.subrun_id, 'r999-l999-n999')).status_code == 400
