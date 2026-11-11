"""Task history index, list/summary/detail contracts and persistence semantics."""

import json
from pathlib import Path

import pytest

from evalscope.service import task_detail, task_history
from evalscope.service.app import create_app
from evalscope.service.utils import log


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from evalscope.service.blueprints import tasks

    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(tasks, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr('evalscope.service.utils.queue_health', lambda: {'backend': 'inprocess'})
    return tmp_path


@pytest.fixture
def client(root: Path):
    return create_app(outputs=str(root)).test_client()


def _manifest(root: Path, task_id: str, **fields) -> dict:
    manifest = {'task_id': task_id, **fields}
    log.write_task_manifest(task_id, manifest)
    return manifest


def _no_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_history, '_active_records', lambda: [])


# ---------------------------------------------------------------------------
# Pure semantics
# ---------------------------------------------------------------------------


def test_status_groups_and_percent_are_never_fabricated() -> None:
    assert task_history.status_group('queued') == 'queued'
    assert task_history.status_group('finalizing') == 'executing'
    assert task_history.status_group('error') == 'failed'
    assert task_history.status_group('not-a-status') == 'uncertain'
    assert task_history.clamp_percent(True) is None
    assert task_history.clamp_percent('99') is None
    assert task_history.clamp_percent(float('nan')) is None
    assert task_history.clamp_percent(150) == 100.0
    assert task_history.clamp_percent(-1) == 0.0


def test_time_basis_keeps_legacy_local_times_visible() -> None:
    aware, basis = task_history.parse_time('2026-11-09T10:00:00+08:00')
    assert basis == 'aware' and aware is not None and aware.utcoffset() is not None
    legacy, basis = task_history.parse_time('2026-11-09T10:00:00')
    assert basis == 'legacy' and legacy is not None and legacy.tzinfo is not None
    assert task_history.parse_time('yesterday') == (None, 'unknown')
    assert task_history.parse_time(None) == (None, 'unknown')


def test_error_summary_is_sanitized() -> None:
    message = task_history.sanitize_error('boom Authorization: Bearer secret-token https://u:p@host/x')
    assert message is not None
    assert 'secret-token' not in message and 'u:p@' not in message
    assert len(task_history.sanitize_error('x' * 10000)) == task_history.MAX_ERROR_LENGTH


def test_test_type_comes_from_markers_not_engine(tmp_path: Path) -> None:
    perf_dir = tmp_path / 'perf'
    perf_dir.mkdir()
    (perf_dir / 'single_run.json').write_text('{}')
    assert task_history.classify_test_type('perf', 'aiperf', {}, str(tmp_path)) == 'single'
    (perf_dir / 'experiment_plan.json').write_text('{}')
    assert task_history.classify_test_type('perf', 'aiperf', {}, str(tmp_path)) == 'cache_experiment'
    assert task_history.classify_test_type('perf', 'evalscope', {'parallel': [1, 2]}, None) == 'sweep'
    assert task_history.classify_test_type('perf', 'evalscope', {'parallel': 1}, None) == 'single'
    assert task_history.classify_test_type('eval', 'evalscope', {}, None) is None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_index_survives_restart_and_keeps_history(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _manifest(root, 'eval_done', kind='eval', status='completed', model='m', started_at='2026-11-09T09:00:00',
              finished_at='2026-11-09T09:05:00', config={'datasets': ['gsm8k'], 'limit': 5})
    # Simulate a process restart: no live queue, fresh module caches, same output root.
    task_history._SCHEMA_READY.clear()
    _no_active(monkeypatch)
    record = task_history.find_record('eval_done')
    assert record is not None and record['status'] == 'completed' and record['task_id'] == 'eval_done'
    result = task_history.query_records(view='history')
    assert [row['task_id'] for row in result['records']] == ['eval_done']


def test_stale_running_task_is_uncertain_and_counted_as_attention(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _manifest(root, 'orphan', kind='perf', status='running', engine='evalscope', model='m')
    _no_active(monkeypatch)
    summary = task_history.summarize()
    assert summary['executing'] == 0
    assert summary['queued'] == 0
    assert summary['attention'] == 1
    detail = task_history.find_record('orphan')
    assert detail is not None and detail['status'] == 'unknown'
    assert detail['last_known_status'] == 'running'


def test_backfill_discovers_manifest_directories(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_active(monkeypatch)
    task_dir = root / 'legacy_task'
    task_dir.mkdir()
    (task_dir / 'task_info.json').write_text(json.dumps({'kind': 'eval', 'status': 'completed'}))
    coverage = task_history.ensure_backfill(force=True)
    assert coverage['state'] == 'ready' and coverage['complete'] is True
    assert task_history.find_record('legacy_task')['status'] == 'completed'


# ---------------------------------------------------------------------------
# HTTP contracts
# ---------------------------------------------------------------------------


def test_list_api_paginates_and_sorts_newest_first(client, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_active(monkeypatch)
    _manifest(root, 'old', kind='eval', status='completed', model='m', finished_at='2026-11-08T10:00:00')
    _manifest(root, 'mid', kind='perf', status='error', model='m', finished_at='2026-11-09T10:00:00')
    _manifest(root, 'new', kind='perf', status='completed', model='m', finished_at='2026-11-09T12:00:00')
    page = client.get('/api/v1/tasks', query_string={'limit': 2, 'offset': 0})
    assert page.status_code == 200
    body = page.json
    assert body['total'] == 3 and body['known_total'] == 3
    assert [item['task_id'] for item in body['items']] == ['new', 'mid']
    assert body['items'][0]['status_group'] == 'completed'
    assert body['items'][1]['status_group'] == 'failed'
    assert body['indexing']['state'] == 'ready'
    second = client.get('/api/v1/tasks', query_string={'limit': 2, 'offset': 2})
    assert [item['task_id'] for item in second.json['items']] == ['old']

    summary = client.get('/api/v1/tasks/summary')
    assert summary.status_code == 200
    assert summary.json['executing'] == 0 and summary.json['attention'] == 1
    assert summary.json['completed_in_range'] == 2
    assert summary.json['queue'] == {'backend': 'inprocess', 'reachable': True, 'reason': None}


def test_list_api_filters_and_rejects_invalid_params(client, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_active(monkeypatch)
    _manifest(root, 'e1', kind='eval', status='completed', model='alpha', finished_at='2026-11-09T10:00:00')
    _manifest(root, 'p1', kind='perf', status='completed', model='beta', finished_at='2026-11-09T11:00:00')
    only_perf = client.get('/api/v1/tasks', query_string={'kind': 'perf'})
    assert [item['task_id'] for item in only_perf.json['items']] == ['p1']
    search = client.get('/api/v1/tasks', query_string={'q': 'alph'})
    assert [item['task_id'] for item in search.json['items']] == ['e1']
    ranged = client.get('/api/v1/tasks', query_string={'from': '2026-11-09T10:30:00'})
    assert [item['task_id'] for item in ranged.json['items']] == ['p1']
    assert client.get('/api/v1/tasks', query_string={'view': 'bogus'}).status_code == 400
    assert client.get('/api/v1/tasks', query_string={'limit': 'abc'}).status_code == 400
    assert client.get('/api/v1/tasks', query_string={'from': 'nope'}).status_code == 400


def test_list_api_returns_uncertain_when_index_unavailable(
    client, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _manifest(root, 'done', kind='eval', status='completed', model='m', finished_at='2026-11-09T10:00:00')
    monkeypatch.setattr(task_history, '_DISABLED_ROOTS', {str(root)})
    _no_active(monkeypatch)
    page = client.get('/api/v1/tasks')
    assert page.status_code == 200
    assert page.json['completeness'] == 'partial'
    assert page.json['indexing']['state'] == 'unavailable'


def test_detail_api_projects_eval_configuration(
    client, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_active(monkeypatch)
    _manifest(
        root,
        'eval_task',
        kind='eval',
        engine='evalscope',
        status='completed',
        model='Qwen/Qwen2.5-0.5B-Instruct',
        alias='smoke',
        endpoint='host:8000',
        started_at='2026-11-09T09:00:00',
        finished_at='2026-11-09T09:05:00',
        config={'datasets': ['gsm8k'], 'limit': 5, 'eval_batch_size': 2, 'temperature': 0.1},
    )
    response = client.get('/api/v1/tasks/eval_task/detail')
    assert response.status_code == 200
    body = response.json
    assert body['kind'] == 'eval' and body['status_group'] == 'completed'
    assert body['config_snapshot']['snapshot_type'] == 'eval'
    assert body['config_snapshot']['datasets'] == ['gsm8k']
    assert body['config_completeness'] == 'full'
    assert body['percent'] is None  # a finished task has no fabricated progress
    assert body['time_basis'] == 'legacy'
    assert body['capabilities']['sweep']['availability'] == 'unsupported'


def test_detail_api_config_snapshot_never_copies_nested_credentials(
    client, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_active(monkeypatch)
    sentinel = 'sentinel-secret-value'
    _manifest(
        root,
        'eval_secrets',
        kind='eval',
        engine='evalscope',
        status='completed',
        model='m',
        config={
            'datasets': ['gsm8k'],
            'api_key': sentinel,
            'headers': {'Authorization': f'Bearer {sentinel}'},
            'nested': {'token': sentinel},
        },
    )
    response = client.get('/api/v1/tasks/eval_secrets/detail')
    assert response.status_code == 200
    assert sentinel not in response.get_data(as_text=True)
    assert response.json['config_snapshot']['datasets'] == ['gsm8k']


def test_detail_api_projects_aiperf_single_and_capabilities(
    client, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_active(monkeypatch)
    perf_dir = root / 'perf_task' / 'perf'
    perf_dir.mkdir(parents=True)
    (perf_dir / 'single_run.json').write_text(
        json.dumps(
            {
                'task_id': 'perf_task',
                'model': 'Qwen/Qwen2.5-0.5B-Instruct',
                'load_mode': 'closed_loop',
                'concurrency': 4,
                'planned_requests': 8,
                'output_token_limit': 32,
                'input_sha256': 'a' * 64,
                'normalized': 'normalized',
                'quality': 'complete',
            }
        )
    )
    (perf_dir / 'normalized').mkdir()
    _manifest(root, 'perf_task', kind='perf', engine='aiperf', status='completed', model='m',
              finished_at='2026-11-09T10:00:00')
    response = client.get('/api/v1/tasks/perf_task/detail')
    assert response.status_code == 200
    body = response.json
    assert body['test_type'] == 'single'
    assert body['config_snapshot']['snapshot_type'] == 'perf_aiperf_single'
    assert body['config_snapshot']['concurrency'] == 4
    assert body['capabilities']['overview']['availability'] == 'available'
    assert body['capabilities']['requests']['availability'] == 'available'
    assert body['capabilities']['node_metrics']['availability'] == 'unavailable'
    assert body['capabilities']['sweep']['availability'] == 'unsupported'


def test_detail_api_unknown_task_is_404(client) -> None:
    assert client.get('/api/v1/tasks/missing/detail').status_code == 404
    assert client.get('/api/v1/tasks/summary/detail').status_code == 404


def test_detail_api_reports_queue_pending_results(client, monkeypatch: pytest.MonkeyPatch) -> None:
    live = {
        'task_id': 'queued_task',
        'kind': 'eval',
        'status': 'queued',
        'model': 'm',
        'enqueued_at': '2026-11-09T10:00:00+00:00',
        'queue_position': 1,
    }
    monkeypatch.setattr(task_history, '_active_records', lambda: [live])
    response = client.get('/api/v1/tasks/queued_task/detail')
    assert response.status_code == 200
    body = response.json
    assert body['status_group'] == 'queued' and body['queue_position'] == 1
    assert body['result_availability']['availability'] == 'pending'
    assert body['capabilities']['overview']['availability'] == 'pending'
