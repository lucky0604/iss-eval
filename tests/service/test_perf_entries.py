"""Unified performance entries: one list over native, AIPerf single and experiments."""

import json
import shutil
from pathlib import Path

import pytest

from evalscope.perf.experiments.planner import plan_cache_experiment
from evalscope.perf.results.aiperf_import import import_aiperf_run
from evalscope.perf.results.models import AIPerfImportArguments
from evalscope.perf.results.single_run import SingleRunManifest, write_single_manifest
from evalscope.service.app import create_app
from evalscope.service.blueprints import perf, tasks
from evalscope.service.utils import log
from tests.perf.test_experiment_planner import _config

FIXTURES = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf'


def _write_experiment(base: Path, task_id: str, template_id: str = 'template', status: str = 'completed') -> None:
    plan = plan_cache_experiment(_config(), task_id, 'tokenizer', template_id)
    root = base / task_id / 'perf'
    root.mkdir(parents=True)
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    (root / 'experiment.json').write_text(
        json.dumps(
            {
                'schema_version': '1.0',
                'plan_hash': plan.plan_hash,
                'status': status,
                'quality': 'complete' if status == 'completed' else None,
                'completed_subruns': len(plan.subruns) if status == 'completed' else 0,
                'total_subruns': len(plan.subruns),
                'subruns': [
                    {'subrun_id': row.subrun_id, 'status': 'completed' if status == 'completed' else 'not_started',
                     'scenario': None}
                    for row in plan.subruns
                ],
            }
        )
    )


def _write_single(base: Path) -> None:
    run = base / 'single' / 'perf'
    raw = run / 'raw' / 'aiperf'
    raw.mkdir(parents=True)
    for name in ('profile_export_aiperf.json', 'profile_export.jsonl', 'phase_manifest.json'):
        shutil.copyfile(FIXTURES / name, raw / name)
    result = import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(run / 'normalized')))
    write_single_manifest(
        str(run),
        SingleRunManifest(
            task_id='single',
            model='model-single',
            endpoint='host:8000',
            load_mode='closed_loop',
            concurrency=2,
            planned_requests=2,
            output_token_limit=8,
            input_sha256='a' * 64,
            status='completed',
            artifact_status='available',
            normalized='normalized',
            quality=result.quality,
            quality_reasons=result.quality_reasons,
        ),
    )
    (run.parent / 'task_info.json').write_text(json.dumps({'kind': 'perf', 'engine': 'aiperf', 'status': 'completed'}))


@pytest.fixture
def entries_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(tasks, 'OUTPUT_DIR', str(tmp_path))
    shutil.copytree(FIXTURES / 'native', tmp_path / 'native')
    _write_single(tmp_path)
    _write_experiment(tmp_path, 'experiment')
    # A container-level report marker must not surface the experiment's own
    # archive as a second, top-level run entry.
    (tmp_path / 'experiment' / 'perf' / 'perf_report.html').write_text('<html></html>')
    return create_app(outputs=str(tmp_path)).test_client(), tmp_path


def test_mixed_entries_are_one_row_per_result_and_container_is_not_duplicated(entries_archive) -> None:
    client, root = entries_archive
    response = client.get('/api/v1/perf/entries', query_string={'root_path': str(root)})
    assert response.status_code == 200
    payload = response.json
    assert payload['total'] == 3 and payload['known_total'] == 3
    assert payload['completeness'] == 'complete' and payload['skipped'] == 0
    by_path = {entry['path']: entry for entry in payload['entries']}
    assert set(by_path) == {'native', 'single/perf', 'experiment/perf'}
    assert by_path['native']['test_type'] == 'sweep' and by_path['native']['engine'] == 'evalscope'
    assert by_path['single/perf']['test_type'] == 'single' and by_path['single/perf']['engine'] == 'aiperf'
    assert by_path['single/perf']['execution_status'] == 'completed'
    experiment = by_path['experiment/perf']
    assert experiment['test_type'] == 'cache_experiment' and experiment['result_kind'] == 'experiment'
    assert experiment['best_rps'] is None and experiment['best_latency'] is None
    assert experiment['metric_scope'] == 'none' and experiment['num_runs'] == 18
    assert experiment['execution_status'] == 'completed' and experiment['quality'] == 'complete'
    assert set(experiment['actions']) == {'open', 'html', 'copy_config'}
    assert experiment['task_id'] == 'experiment'
    assert 'delete' in by_path['native']['actions']
    assert by_path['single/perf']['capabilities']['requests']['availability'] == 'available'
    assert by_path['single/perf']['capabilities']['node_metrics']['availability'] == 'unavailable'


def test_entries_filter_search_sort_and_pagination(entries_archive) -> None:
    client, root = entries_archive
    aiperf = client.get('/api/v1/perf/entries', query_string={'root_path': str(root), 'engine': 'aiperf'})
    assert aiperf.status_code == 200
    assert {entry['path'] for entry in aiperf.json['entries']} == {'single/perf', 'experiment/perf'}
    sweep = client.get('/api/v1/perf/entries', query_string={'root_path': str(root), 'test_type': 'sweep'})
    assert [entry['path'] for entry in sweep.json['entries']] == ['native']
    searched = client.get('/api/v1/perf/entries', query_string={'root_path': str(root), 'q': 'model-single'})
    assert [entry['path'] for entry in searched.json['entries']] == ['single/perf']
    page = client.get(
        '/api/v1/perf/entries', query_string={'root_path': str(root), 'sort': 'rps', 'offset': 1, 'limit': 1}
    )
    assert page.status_code == 200 and len(page.json['entries']) == 1 and page.json['total'] == 3
    assert page.json['entries'][0]['best_rps'] is None or page.json['entries'][0]['best_rps'] >= 0


def test_invalid_root_and_filters_are_rejected(entries_archive) -> None:
    client, root = entries_archive
    assert client.get('/api/v1/perf/entries', query_string={'root_path': str(root / 'missing')}).status_code == 400
    assert client.get('/api/v1/perf/entries', query_string={'root_path': str(root), 'test_type': 'bad'}).status_code == 400
    assert client.get('/api/v1/perf/entries', query_string={'root_path': str(root), 'engine': 'bad'}).status_code == 400
    assert client.get('/api/v1/perf/entries', query_string={'root_path': str(root), 'sort': 'bad'}).status_code == 400
    assert client.get('/api/v1/perf/entries', query_string={'root_path': str(root), 'limit': 0}).status_code == 400


def test_unreadable_experiment_is_reported_not_hidden(entries_archive) -> None:
    client, root = entries_archive
    broken = root / 'broken' / 'perf'
    broken.mkdir(parents=True)
    (broken / 'experiment_plan.json').write_text('{not json')
    response = client.get('/api/v1/perf/entries', query_string={'root_path': str(root)})
    assert response.status_code == 200
    assert response.json['completeness'] == 'partial' and response.json['skipped'] == 1
    entry = next(item for item in response.json['entries'] if item['path'] == 'broken/perf')
    assert entry['unreadable_reason'] == 'archive_unreadable'
    assert entry['capabilities']['overview']['availability'] == 'unavailable'
    assert entry['actions'] == []


def test_external_root_experiment_is_read_in_place_and_never_links_the_service_task(
    entries_archive, tmp_path: Path
) -> None:
    client, root = entries_archive
    external = tmp_path.parent / (tmp_path.name + '-external')
    try:
        _write_experiment(external, 'experiment', template_id='external-template')
        # The service root experiment and the external copy share the task id but
        # must resolve to their own plan and root.
        local = client.get('/api/v1/perf/experiments/experiment')
        foreign = client.get('/api/v1/perf/experiments/experiment', query_string={'root_path': str(external)})
        assert local.status_code == 200 and local.json['plan']['template_id'] == 'template'
        assert foreign.status_code == 200 and foreign.json['plan']['template_id'] == 'external-template'
        listing = client.get('/api/v1/perf/entries', query_string={'root_path': str(external)})
        assert listing.status_code == 200
        entry = next(item for item in listing.json['entries'] if item['result_kind'] == 'experiment')
        assert entry['task_id'] is None
        assert entry['root_path'] == str(external)
        assert entry['detail_path'].startswith('/perf-experiment/experiment?root_path=')
        from urllib.parse import quote

        assert quote(str(external), safe='') in entry['detail_path']
        assert '/tasks/experiment' not in (entry['detail_path'] or '')
        # The external archive is read-only: deleting is refused.
        assert client.delete('/api/v1/perf/experiments/experiment', query_string={'root_path': str(external)}).status_code == 400
        assert (external / 'experiment' / 'perf' / 'experiment_plan.json').is_file()
    finally:
        shutil.rmtree(external, ignore_errors=True)


def test_external_root_run_is_read_only_and_never_links_the_service_task(entries_archive, tmp_path: Path) -> None:
    client, root = entries_archive
    external = tmp_path.parent / (tmp_path.name + '-external-run')
    try:
        shutil.copytree(root / 'single', external / 'single')
        listing = client.get('/api/v1/perf/entries', query_string={'root_path': str(external)})
        assert listing.status_code == 200
        entry = next(item for item in listing.json['entries'] if item['path'] == 'single/perf')
        # Same directory name as the service run, but an external root owns no
        # service task and must not offer destructive actions.
        assert entry['task_id'] is None
        assert 'delete' not in entry['actions']
        assert (external / 'single' / 'perf' / 'single_run.json').is_file()
        denied = client.delete(
            '/api/v1/perf/run', query_string={'root_path': str(external), 'path': 'single/perf'}
        )
        assert denied.status_code == 400
        assert 'read-only' in denied.json['error']
        assert (external / 'single' / 'perf' / 'single_run.json').is_file()
    finally:
        shutil.rmtree(external, ignore_errors=True)


def test_experiment_container_dedupe_survives_symlinked_subrun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(tasks, 'OUTPUT_DIR', str(tmp_path))
    _write_experiment(tmp_path, 'experiment')
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.mkdir()
    runs = tmp_path / 'experiment' / 'perf' / 'runs'
    runs.mkdir()
    (runs / 'escape').symlink_to(outside, target_is_directory=True)
    try:
        client = create_app(outputs=str(tmp_path)).test_client()
        response = client.get('/api/v1/perf/entries', query_string={'root_path': str(tmp_path)})
        assert response.status_code == 200
        assert [entry['path'] for entry in response.json['entries']] == ['experiment/perf']
    finally:
        shutil.rmtree(outside, ignore_errors=True)
