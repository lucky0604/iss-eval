"""Plan-wide cache-experiment summary stays independent of matrix pagination."""

import json
import shutil
from pathlib import Path

import pytest

from evalscope.perf.executors.arguments import LoadConfiguration, LoadPoint
from evalscope.perf.experiments.planner import plan_cache_experiment
from evalscope.perf.results.aiperf_import import import_aiperf_run, load_aiperf_run
from evalscope.perf.results.models import AIPerfImportArguments
from evalscope.service import perf_experiments
from evalscope.service.app import create_app
from evalscope.service.blueprints import perf
from evalscope.service.utils import log
from tests.perf.test_experiment_planner import _config

FIXTURES = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf'


def _state(root: Path, plan, statuses: dict, qualities: dict | None = None) -> None:
    rows = []
    for row in plan.subruns:
        status = statuses.get(row.subrun_id, 'not_started')
        rows.append({
            'subrun_id': row.subrun_id,
            'status': status,
            'quality': (qualities or {}).get(row.subrun_id),
            'scenario': f'runs/{row.subrun_id}/scenario.json' if status != 'not_started' else None,
        })
    (root / 'experiment.json').write_text(json.dumps({
        'schema_version': '1.0',
        'plan_hash': plan.plan_hash,
        'status': 'running',
        'completed_subruns': sum(1 for row in rows if row['status'] == 'completed'),
        'total_subruns': len(rows),
        'subruns': rows,
    }))


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    return create_app(outputs=str(tmp_path)).test_client()


def _import_run(raw: Path, normalized: Path) -> None:
    raw.mkdir(parents=True)
    for name in ('profile_export.jsonl', 'profile_export_aiperf.json', 'phase_manifest.json'):
        shutil.copyfile(FIXTURES / name, raw / name)
    import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(normalized)))


def test_summary_aggregates_whole_plan_and_keeps_request_counts_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(perf_experiments, 'active_task_ids', lambda: {'task'})
    plan = plan_cache_experiment(_config(), 'task', 'tokenizer', 'template')
    item = plan.subruns[0]
    root = tmp_path / 'task' / 'perf'
    run = root / 'runs' / item.subrun_id
    _import_run(run / 'raw' / 'aiperf', run / 'normalized')
    (run / 'scenario.json').write_text(json.dumps({
        'status': 'completed', 'target_ratio': item.target_ratio, 'normalized': 'normalized', 'quality_reasons': [],
    }))
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    _state(root, plan, {item.subrun_id: 'completed'}, {item.subrun_id: 'complete'})
    result = load_aiperf_run(str(run / 'normalized'))

    body = client.get('/api/v1/perf/experiments/task/summary').json
    assert body['total_planned_subruns'] == 18
    assert body['coverage'] == {'planned': 18, 'completed': 1, 'readable': 1, 'included': 1}
    assert body['missing_subruns'] == 17 and body['unreadable_subruns'] == 0
    assert body['counts_by_status'] == {'completed': 1, 'not_started': 17}
    assert body['counts_by_quality'] == {'complete': 1}
    assert len(body['groups']) == 6
    assert sum(group['planned_requests'] for group in body['groups']) == plan.profiling_requests == 180
    # Repetitions are members of one matrix cell, not separate groups.
    assert {group['total_subruns'] for group in body['groups']} == {3}
    assert {group['planned_requests'] for group in body['groups']} == {30}
    assert sum(group['observed_requests'] for group in body['groups']) == result.profiling_requests
    assert sum(group['successful_requests'] for group in body['groups']) == result.profiling_successes
    assert body['completeness'] == 'complete' and body['completeness_reason'] is None
    assert body['execution_status'] == 'running' and body['plan_hash'] == plan.plan_hash
    assert 'mean' not in body and 'p99' not in body


def test_summary_covers_more_than_sixty_groups_without_reading_unstarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    config = _config()
    assert config.cache_experiment is not None
    config.cache_experiment.input_token_values = [100, 200, 300, 400, 500]
    config.load = LoadConfiguration(
        points=[LoadPoint(concurrency=value) for value in (2, 4, 8, 16)],
        request_count=10,
        output_token_limit=5,
        repetitions=2,
    )
    plan = plan_cache_experiment(config, 'task', 'tokenizer', 'template')
    assert len(plan.subruns) == 120
    root = tmp_path / 'task' / 'perf'
    root.mkdir(parents=True)
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    _state(root, plan, {})

    body = client.get('/api/v1/perf/experiments/task/summary').json
    assert body['total_planned_subruns'] == 120 and len(body['groups']) == 60
    assert body['missing_subruns'] == 120 and body['readable_subruns'] == 0
    assert body['counts_by_status'] == {'not_started': 120}
    assert {group['planned_requests'] for group in body['groups']} == {20}
    assert body['completeness'] == 'complete'


def test_unreadable_and_incomplete_artifacts_are_not_counted_as_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    plan = plan_cache_experiment(_config(), 'task', 'tokenizer', 'template')
    first, second, third = plan.subruns[:3]
    root = tmp_path / 'task' / 'perf'
    broken = root / 'runs' / first.subrun_id
    broken.mkdir(parents=True)
    (broken / 'scenario.json').write_text('{not json')
    third_dir = root / 'runs' / third.subrun_id
    third_dir.mkdir(parents=True)
    (third_dir / 'scenario.json').write_text(json.dumps({
        'status': 'running', 'target_ratio': third.target_ratio, 'quality_reasons': [],
    }))
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    # `second` completed without ever publishing a scenario reference: a defect,
    # distinct from a subrun that was never attempted.
    _state(root, plan, {first.subrun_id: 'completed', second.subrun_id: 'completed', third.subrun_id: 'running'})

    body = client.get('/api/v1/perf/experiments/task/summary').json
    assert body['unreadable_subruns'] == 2
    assert body['readable_subruns'] == 0 and body['coverage']['included'] == 0
    assert body['missing_subruns'] == 15
    assert body['counts_by_status'] == {'completed': 2, 'running': 1, 'not_started': 15}
    assert body['completeness'] == 'partial' and body['completeness_reason'] == 'unreadable_subruns'
    assert sum(group['observed_requests'] for group in body['groups']) == 0


def test_summary_cache_invalidates_when_state_advances(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    plan = plan_cache_experiment(_config(), 'task', 'tokenizer', 'template')
    item = plan.subruns[0]
    root = tmp_path / 'task' / 'perf'
    run = root / 'runs' / item.subrun_id
    run.mkdir(parents=True)
    (run / 'scenario.json').write_text(json.dumps({
        'status': 'running', 'target_ratio': item.target_ratio, 'quality_reasons': [],
    }))
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    _state(root, plan, {item.subrun_id: 'running'})
    before = client.get('/api/v1/perf/experiments/task/summary').json
    assert before['coverage']['completed'] == 0 and before['missing_subruns'] == 17

    _state(root, plan, {item.subrun_id: 'completed'})
    after = client.get('/api/v1/perf/experiments/task/summary').json
    assert after['coverage']['completed'] == 1 and after['missing_subruns'] == 17
    assert after['revision'] != before['revision']


def test_read_budget_marks_request_totals_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(perf_experiments, '_SUMMARY_READ_BUDGET', 1)
    plan = plan_cache_experiment(_config(), 'task', 'tokenizer', 'template')
    statuses = {}
    root = tmp_path / 'task' / 'perf'
    for item in plan.subruns[:2]:
        run = root / 'runs' / item.subrun_id
        run.mkdir(parents=True)
        (run / 'scenario.json').write_text(json.dumps({
            'status': 'running', 'target_ratio': item.target_ratio, 'quality_reasons': [],
        }))
        statuses[item.subrun_id] = 'running'
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    _state(root, plan, statuses)

    body = client.get('/api/v1/perf/experiments/task/summary').json
    assert body['completeness'] == 'partial' and body['completeness_reason'] == 'read_budget_exceeded'
    assert body['coverage']['readable'] == 0 and body['unreadable_subruns'] == 0


def test_summary_fails_closed_on_mismatch_and_keeps_roots_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    plan = plan_cache_experiment(_config(), 'task', 'tokenizer', 'template')
    root = tmp_path / 'task' / 'perf'
    root.mkdir(parents=True)
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    _state(root, plan, {})
    assert client.get('/api/v1/perf/experiments/task/summary').json['coverage']['completed'] == 0

    external = tmp_path / 'elsewhere'
    shutil.copytree(tmp_path / 'task', external / 'task')
    _state(external / 'task' / 'perf', plan, {plan.subruns[0].subrun_id: 'completed'})
    foreign = client.get('/api/v1/perf/experiments/task/summary', query_string={'root_path': str(external)})
    assert foreign.status_code == 200 and foreign.json['coverage']['completed'] == 1
    # The service root keeps its own truth even when a foreign archive reuses the ID.
    assert client.get('/api/v1/perf/experiments/task/summary').json['coverage']['completed'] == 0

    data = json.loads((root / 'experiment_plan.json').read_text())
    data['subruns'][0]['target_ratio'] = 0.123
    (root / 'experiment_plan.json').write_text(json.dumps(data))
    assert client.get('/api/v1/perf/experiments/task/summary').status_code == 400
