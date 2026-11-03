"""Actual task polling preserves external progress and restart status."""

import json
from pathlib import Path

import pytest

from evalscope.service.app import create_app
from evalscope.service.blueprints import perf, tasks
from evalscope.service.utils import log


@pytest.fixture
def task_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for module in (log, perf, tasks):
        monkeypatch.setattr(module, 'OUTPUT_DIR', str(tmp_path))
    return create_app(outputs=str(tmp_path)).test_client()


def _write_state(root: Path, status: str = 'running', phase: str | None = 'profiling') -> None:
    path = root / 'task' / 'perf'
    path.mkdir(parents=True, exist_ok=True)
    (root / 'task' / 'task_info.json').write_text(json.dumps({'kind': 'perf', 'engine': 'aiperf', 'status': status}))
    (path / 'experiment.json').write_text(json.dumps({
        'status': status, 'phase': phase, 'last_phase': 'importing', 'current_subrun': 'r000-l000-n000',
        'completed_subruns': 1, 'total_subruns': 2, 'quality': 'partial',
    }))


def test_active_task_and_progress_keep_phase_and_cancelling(task_client, tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    _write_state(tmp_path, phase='draining')
    record = {'task_id': 'task', 'kind': 'perf', 'engine': 'aiperf', 'status': 'cancelling'}
    monkeypatch.setattr(tasks, 'list_active_tasks', lambda: [record.copy()])
    monkeypatch.setattr(perf, 'list_active_tasks', lambda: [record.copy()])
    task = task_client.get('/api/v1/tasks/task')
    progress = task_client.get('/api/v1/perf/progress?task_id=task')
    assert task.status_code == progress.status_code == 200
    for response in (task, progress):
        assert response.json['status'] == 'cancelling'
        assert response.json['phase'] == 'draining' and response.json['percent'] == 50
        assert response.json['completed_subruns'] == 1 and response.json['total_subruns'] == 2


def test_unknown_preparing_progress_and_restart_are_not_zero_or_running(task_client, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / 'task').mkdir()
    (tmp_path / 'task' / 'task_info.json').write_text(json.dumps({'kind': 'perf', 'engine': 'aiperf', 'status': 'running'}))
    record = {'task_id': 'task', 'kind': 'perf', 'engine': 'aiperf', 'status': 'running'}
    monkeypatch.setattr(tasks, 'list_active_tasks', lambda: [record.copy()])
    monkeypatch.setattr(perf, 'list_active_tasks', lambda: [record.copy()])
    assert task_client.get('/api/v1/perf/progress?task_id=task').json['percent'] is None
    monkeypatch.setattr(tasks, 'list_active_tasks', lambda: [])
    monkeypatch.setattr(perf, 'list_active_tasks', lambda: [])
    assert task_client.get('/api/v1/tasks/task').json['status'] == 'unknown'
    assert task_client.get('/api/v1/perf/progress?task_id=task').json['status'] == 'unknown'


def test_stopped_task_preserves_last_phase_and_does_not_gain_report_without_results(task_client, tmp_path: Path,
                                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _write_state(tmp_path, status='stopped', phase=None)
    monkeypatch.setattr(tasks, 'list_active_tasks', lambda: [])
    monkeypatch.setattr(perf, 'list_active_tasks', lambda: [])
    task = task_client.get('/api/v1/tasks/task')
    progress = task_client.get('/api/v1/perf/progress?task_id=task')
    assert task.json['status'] == progress.json['status'] == 'stopped'
    assert task.json['phase'] == progress.json['phase'] == 'importing'
    assert task.json['report_url'] is None and task.json['html_report_url'] is None


def test_single_run_quality_is_exposed_in_task_and_progress(task_client, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.perf.results.single_run import SingleRunManifest, write_single_manifest

    root = tmp_path / 'task' / 'perf'
    root.mkdir(parents=True)
    (root.parent / 'task_info.json').write_text(json.dumps({'kind': 'perf', 'engine': 'aiperf', 'status': 'error'}))
    write_single_manifest(str(root), SingleRunManifest(
        task_id='task', model='model', load_mode='closed_loop', concurrency=1, planned_requests=1,
        output_token_limit=1, input_sha256='0' * 64, status='error', normalized='normalized', quality='invalid',
    ))
    monkeypatch.setattr(tasks, 'list_active_tasks', lambda: [])
    monkeypatch.setattr(perf, 'list_active_tasks', lambda: [])
    task = task_client.get('/api/v1/tasks/task')
    progress = task_client.get('/api/v1/perf/progress?task_id=task')
    assert task.json['quality'] == progress.json['quality'] == 'invalid'
    assert task.json['status'] == progress.json['status'] == 'error'
