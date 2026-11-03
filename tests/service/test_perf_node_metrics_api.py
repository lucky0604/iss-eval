"""Node queries are bounded by the immutable experiment and its run directory."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from evalscope.perf.experiments.planner import plan_cache_experiment
from evalscope.perf.integrations.aiperf.metrics import analyze_node_metrics
from evalscope.perf.results.aiperf_import import import_aiperf_run
from evalscope.perf.results.models import AIPerfImportArguments
from evalscope.service.app import create_app
from evalscope.service.blueprints import perf
from evalscope.service.utils import log
from tests.perf.test_experiment_planner import _config

FIXTURES = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf'


@pytest.fixture
def archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    plan = plan_cache_experiment(_config(), 'task', 'tokenizer', 'template')
    item = plan.subruns[0]
    root = tmp_path / 'task' / 'perf'
    run = root / 'runs' / item.subrun_id
    raw = run / 'raw' / 'aiperf'
    raw.mkdir(parents=True)
    for name in ('profile_export.jsonl', 'profile_export_aiperf.json', 'server_metrics_export.json', 'phase_manifest.json'):
        shutil.copyfile(FIXTURES / name, raw / name)
    shutil.copyfile(FIXTURES / 'multi_server_metrics_export.jsonl', raw / 'server_metrics_export.jsonl')
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(run / 'normalized'), targets=[
        {'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': 'http://127.0.0.1:18081/metrics'},
        {'node_id': 'd', 'role': 'decode', 'engine': 'sglang', 'url': 'http://127.0.0.1:18082/metrics'},
    ])
    import_aiperf_run(args)
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    (root / 'experiment.json').write_text(json.dumps({
        'schema_version': '1.0', 'plan_hash': plan.plan_hash, 'status': 'error', 'completed_subruns': 0,
        'total_subruns': len(plan.subruns), 'subruns': [
            {'subrun_id': row.subrun_id, 'status': 'error' if row == item else 'not_started',
             'scenario': f'runs/{row.subrun_id}/scenario.json' if row == item else None}
            for row in plan.subruns],
    }))
    (run / 'scenario.json').write_text(json.dumps({'status': 'error', 'target_ratio': item.target_ratio,
        'normalized': 'normalized', 'node_analysis': 'node_analysis.json', 'quality_reasons': ['partial']}))
    analysis = analyze_node_metrics(str(run / 'normalized'), 10**10)
    (run / 'node_analysis.json').write_text(analysis.model_dump_json())
    return create_app(outputs=str(tmp_path)).test_client(), root, run, item, plan


def test_scenario_analysis_and_bounded_dimensions(archive) -> None:
    client, root, _, item, _ = archive
    base = f'/api/v1/perf/experiments/task/runs/{item.subrun_id}'
    response = client.get(base)
    assert response.status_code == 200 and response.json['node_analysis']['availability'] == 'missing'
    response = client.get(base + '/metrics?node_id=p&metric_name=mock_requests&limit=1')
    assert response.status_code == 200 and response.json['total'] >= 1 and len(response.json['samples']) == 1
    assert response.json['aggregation'] == 'raw' and response.json['downsample'] == 'none'
    assert client.get(base + '/metrics?node_id=not-planned').status_code == 404
    assert client.get(base + '/metrics?metric_name=unknown').status_code == 404
    assert client.get(base + '/metrics?start_ns=999999999999999999999999999').status_code == 400
    assert client.get(base + '/metrics?offset=999999999999999999999999999').status_code == 400
    assert client.get(base + '/metrics?limit=2001').status_code == 400
    assert client.get(base + '/requests?offset=999999999999999999999999999').status_code == 400
    assert client.get('/api/v1/perf/experiments/task/runs/r999-l999-n999').status_code == 400
    query = {'root_path': str(root.parent.parent), 'path': 'task/perf', 'subrun_id': item.subrun_id, 'node_id': 'p'}
    assert client.get('/api/v1/perf/server-metrics', query_string=query).status_code == 200
    query['root_path'] = str(root)
    assert client.get('/api/v1/perf/server-metrics', query_string=query).status_code == 400
    query['root_path'] = str(root.parent.parent)
    query['path'] = '../perf'
    assert client.get('/api/v1/perf/server-metrics', query_string=query).status_code == 400


def test_tampered_plan_and_symlinks_fail_closed(archive, tmp_path: Path) -> None:
    client, root, run, item, _ = archive
    base = f'/api/v1/perf/experiments/task/runs/{item.subrun_id}'
    plan_file = root / 'experiment_plan.json'
    data = json.loads(plan_file.read_text())
    data['subruns'][0]['target_ratio'] = 0.33
    plan_file.write_text(json.dumps(data))
    assert client.get('/api/v1/perf/experiments/task').status_code == 400
    data['subruns'][0]['target_ratio'] = item.target_ratio
    plan_file.write_text(json.dumps(data))
    outside = root.parent / 'foreign'
    outside.mkdir()
    shutil.rmtree(run / 'normalized')
    (run / 'normalized').symlink_to(outside, target_is_directory=True)
    assert client.get(base + '/metrics').status_code == 400
    assert client.get(base).status_code == 400


def test_runs_directory_cannot_redirect_within_task(archive) -> None:
    client, root, _, item, _ = archive
    runs = root / 'runs'
    relocated = root.parent / 'other-runs'
    runs.rename(relocated)
    runs.symlink_to(relocated, target_is_directory=True)
    response = client.get(f'/api/v1/perf/experiments/task/runs/{item.subrun_id}/metrics')
    assert response.status_code == 400


def test_legacy_plan_without_new_budget_fields_remains_readable(archive) -> None:
    client, root, _, _, _ = archive
    plan_file = root / 'experiment_plan.json'
    plan = json.loads(plan_file.read_text())
    plan.pop('max_profiling_output_tokens')
    plan.pop('max_primer_output_tokens')
    for row in plan['subruns']:
        row.pop('output_token_limit')
    payload = {'config_hash': plan['configuration_hash'], 'subruns': plan['subruns']}
    plan['plan_hash'] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    plan_file.write_text(json.dumps(plan))
    state_file = root / 'experiment.json'
    state = json.loads(state_file.read_text())
    state['plan_hash'] = plan['plan_hash']
    state_file.write_text(json.dumps(state))
    response = client.get('/api/v1/perf/experiments/task')
    assert response.status_code == 200 and response.json['plan'].get('max_profiling_output_tokens') is None


def test_delete_requires_inactive_task(archive, monkeypatch: pytest.MonkeyPatch) -> None:
    client, root, _, _, _ = archive
    monkeypatch.setattr(perf, 'active_task_ids', lambda: {'task'})
    assert client.delete('/api/v1/perf/experiments/task').status_code == 409
    assert root.exists()
    monkeypatch.setattr(perf, 'active_task_ids', lambda: set())
    response = client.delete('/api/v1/perf/experiments/task')
    assert response.status_code == 200 and response.json['path'] == 'task/perf' and not root.exists()
