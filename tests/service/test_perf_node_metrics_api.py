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
from evalscope.service import perf_reports
from evalscope.service.app import create_app
from evalscope.service.blueprints import perf
from evalscope.service.perf_exports import ExportSpec
from evalscope.service.perf_exports.collect import collect
from evalscope.service.perf_exports.html import render_html
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


def test_series_sampling_matches_labels_and_full_window(archive, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from evalscope.perf.results.models import NodeMetricSample

    client, _, run, item, _ = archive
    normalized = run / 'normalized'
    identity = json.loads((normalized / 'current.json').read_text())['generation']
    database = normalized / 'generations' / identity / 'requests.db'
    with sqlite3.connect(database) as connection:
        for index in range(1200):
            for label in ('wanted', 'other'):
                sample = NodeMetricSample(timestamp_ns=index, node_id='p', role='prefill', engine='vllm',
                    endpoint='host', metric_name='test:queue', metric_type='gauge',
                    labels={'model': label, 'device': '0'}, observation={'value': index})
                connection.execute('INSERT INTO node_samples (node_id, metric_name, timestamp_ns, sample_json) '
                                   'VALUES (?, ?, ?, ?)', ('p', 'test:queue', index, sample.model_dump_json()))
    base = f'/api/v1/perf/experiments/task/runs/{item.subrun_id}/metrics/series'
    params = {'node_id': 'p', 'metric_name': 'test:queue', 'labels': json.dumps({'device': '0', 'model': 'wanted'}),
              'max_points': 13}
    response = client.get(base, query_string=params)
    assert response.status_code == 200
    series = response.json['series']
    assert series['total'] == 1200 and series['aggregation'] == 'raw' and series['downsample'] == 'uniform_index'
    assert len(series['samples']) == 13
    assert series['samples'][0]['timestamp_ns'] == 0 and series['samples'][-1]['timestamp_ns'] == 1199
    assert all(sample['labels']['model'] == 'wanted' for sample in series['samples'])
    server_params = {**params, 'root_path': str(run.parents[3]), 'path': 'task/perf',
                     'subrun_id': item.subrun_id, 'downsample': 'uniform_index'}
    server = client.get('/api/v1/perf/server-metrics', query_string=server_params)
    assert server.status_code == 200 and server.json['series'] == series
    assert client.get('/api/v1/perf/server-metrics', query_string={**server_params, 'downsample': 'bad'}).status_code == 400
    window = client.get(base, query_string={**params, 'start_ns': 200, 'end_ns': 800}).json['series']
    assert window['total'] == 601 and window['samples'][0]['timestamp_ns'] == 200
    assert window['samples'][-1]['timestamp_ns'] == 800
    exact = client.get(base, query_string={**params, 'start_ns': 200, 'end_ns': 202}).json['series']
    assert exact['downsample'] == 'none' and len(exact['samples']) == 3
    assert client.get(base, query_string={**params, 'max_points': 2001}).status_code == 400
    assert client.get(base, query_string={**params, 'start_ns': 10, 'end_ns': 9}).status_code == 400
    assert client.get(base, query_string={**params, 'labels': 'not-json'}).status_code == 400
    assert client.get(base, query_string={**params, 'node_id': 'unknown'}).status_code == 404
    monkeypatch.setattr('evalscope.perf.results.aiperf_import._MAX_NODE_SERIES_CANDIDATES', 2)
    budget = client.get(base, query_string=params)
    assert budget.status_code == 413 and 'narrow the time window' in budget.json['error']

@pytest.fixture
def node_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real cache-experiment archive whose node analysis came from a real import."""
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    plan = plan_cache_experiment(_config(), 'node-task', 'tokenizer', 'template')
    item = plan.subruns[0]
    root = tmp_path / 'node-task' / 'perf'
    run = root / 'runs' / item.subrun_id
    raw = run / 'raw' / 'aiperf'
    raw.mkdir(parents=True)
    for name in ('profile_export.jsonl', 'profile_export_aiperf.json', 'phase_manifest.json'):
        shutil.copyfile(FIXTURES / name, raw / name)
    phase = next(
        row for row in json.loads((raw / 'phase_manifest.json').read_text())['phases']
        if row['phase_kind'] == 'profiling'
    )
    start, end = phase['start_ns'], phase['end_ns']
    (raw / 'server_metrics_export.json').write_text(json.dumps({
        'schema_version': '1.1', 'aiperf_version': '0.13.0',
        'metrics': {
            'vllm:num_requests_waiting': {'type': 'gauge'},
            'vllm:prefix_cache_hits': {'type': 'counter'},
            'vllm:prefix_cache_queries': {'type': 'counter'},
        },
    }))
    events = []
    for endpoint, waiting, hits, queries in (
        ('http://127.0.0.1:18081/metrics', (2, 3), (10, 18), (20, 30)),
        ('http://127.0.0.1:18082/metrics', (7, 9), (40, 52), (80, 100)),
    ):
        # Scrapes before and after the phase bracket the counters; the two
        # inside it are the gauge samples the window mean uses.
        for index, timestamp in enumerate((
            start - 1_000_000, start + 1_000_000, end - 1_000_000, end + 1_000_000
        )):
            sample = 0 if index < 2 else 1
            events.append({
                'endpoint_url': endpoint,
                'timestamp_ns': timestamp,
                'metrics': {
                    'vllm:num_requests_waiting': [{'labels': {'model': 'fixture'}, 'value': waiting[sample]}],
                    'vllm:prefix_cache_hits': [{'labels': {'model': 'fixture'}, 'value': hits[sample]}],
                    'vllm:prefix_cache_queries': [{'labels': {'model': 'fixture'}, 'value': queries[sample]}],
                },
            })
    (raw / 'server_metrics_export.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
    import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(run / 'normalized'), targets=[
        {'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': 'http://127.0.0.1:18081/metrics',
         'labels': {'model': 'fixture'}},
        {'node_id': 'd', 'role': 'decode', 'engine': 'vllm', 'url': 'http://127.0.0.1:18082/metrics',
         'labels': {'model': 'fixture'}},
    ]))
    (root / 'experiment_plan.json').write_text(plan.model_dump_json())
    (root / 'experiment.json').write_text(json.dumps({
        'schema_version': '1.0', 'plan_hash': plan.plan_hash, 'status': 'completed', 'completed_subruns': 1,
        'total_subruns': len(plan.subruns), 'subruns': [
            {'subrun_id': row.subrun_id, 'status': 'completed' if row == item else 'not_started',
             'scenario': f'runs/{row.subrun_id}/scenario.json' if row == item else None}
            for row in plan.subruns],
    }))
    (run / 'scenario.json').write_text(json.dumps({'status': 'completed', 'target_ratio': item.target_ratio,
        'normalized': 'normalized', 'node_analysis': 'node_analysis.json'}))
    analysis = analyze_node_metrics(str(run / 'normalized'), max(end - start, 1) + 10)
    (run / 'node_analysis.json').write_text(analysis.model_dump_json())
    return root, run, item, analysis


def _snapshot(tmp_path: Path, root: Path) -> perf_reports.ReportSnapshot:
    return perf_reports.load_report_snapshot(
        outputs_root=str(tmp_path), root_path=str(root.parent.parent), rel_path=f'{root.parent.name}/perf'
    )


def test_persisted_node_analysis_reaches_the_report_and_the_print_export(node_archive, tmp_path: Path) -> None:
    root, _run, item, analysis = node_archive
    assert analysis.series, 'the import plus analysis must produce real series'
    snapshot = _snapshot(tmp_path, root)
    assert snapshot.document.capabilities.node_metrics.availability == 'available'
    row = next(row for row in snapshot.document.runs if row.run_id == item.subrun_id)
    assert row.nodes is not None
    assert row.nodes.window_source == 'aiperf_phase'
    assert row.nodes.max_gap_ns is not None and row.nodes.max_gap_ns > 0
    # Each planned node keeps its own series; no cross-node merge.
    assert {metric.node_id for metric in row.nodes.metrics} == {'p', 'd'}
    assert {metric.role for metric in row.nodes.metrics} == {'prefill', 'decode'}
    waiting = {
        metric.node_id: metric for metric in row.nodes.metrics
        if metric.metric_name.endswith('num_requests_waiting')
    }
    assert set(waiting) == {'p', 'd'}
    # A gauge window mean is time weighted between the two scrapes, so it sits
    # between the observed samples rather than repeating either one.
    assert 2 < waiting['p'].statistic.value < 3
    assert 7 < waiting['d'].statistic.value < 9
    for metric in row.nodes.metrics:
        assert metric.statistic is not None
        assert metric.statistic.availability in ('available', 'partial')
        assert metric.statistic.boundary is not None and metric.statistic.boundary != 'unknown'
        if metric.statistic.coverage is not None:
            assert 0 <= metric.statistic.coverage <= 1
        if metric.statistic.availability == 'partial':
            assert metric.statistic.reason
    assert waiting['p'].metric_unit and waiting['d'].metric_unit
    ratios = {ratio.node_id: ratio for ratio in row.nodes.cache_ratios}
    assert ratios['p'].ratio == pytest.approx(0.8) and ratios['d'].ratio == pytest.approx(0.6)

    dataset = collect(snapshot)
    spec = ExportSpec(kind='html', path=f'{root.parent.name}/perf', root_path=str(tmp_path), locale='zh')
    html = render_html(dataset, spec, generated_at='2026-11-17T00:00:00Z')
    assert snapshot.document.snapshot_id in html
    assert '节点监控' in html and 'vllm:num_requests_waiting' in html
    assert f'{waiting["p"].statistic.value:.4f}' in html
    assert f'{waiting["d"].statistic.value:.4f}' in html
    assert 'prefill' in html and 'decode' in html
    assert '80.00' in html and '60.00' in html


def test_changed_node_data_stamps_a_new_snapshot_and_leaves_the_old_one_intact(node_archive, tmp_path: Path) -> None:
    root, run, item, _analysis = node_archive
    first = _snapshot(tmp_path, root)
    first_id = first.document.snapshot_id
    first_row = next(row for row in first.document.runs if row.run_id == item.subrun_id)
    first_metric = next(metric for metric in first_row.nodes.metrics if metric.metric_name.endswith('num_requests_waiting'))
    first_value = first_metric.statistic.value

    data = json.loads((run / 'node_analysis.json').read_text())
    changed = False
    for series in data['series']:
        statistic = series.get('statistic')
        if statistic is not None and statistic.get('value') is not None:
            statistic['value'] = first_value + 1.0
            changed = True
            break
    assert changed
    (run / 'node_analysis.json').write_text(json.dumps(data))

    second = _snapshot(tmp_path, root)
    assert second.document.snapshot_id != first_id
    second_row = next(row for row in second.document.runs if row.run_id == item.subrun_id)
    second_value = next(
        metric.statistic.value
        for metric in second_row.nodes.metrics
        if metric.node_id == first_metric.node_id and metric.metric_name == first_metric.metric_name
        and metric.labels == first_metric.labels
    )
    assert second_value == first_value + 1.0
    # The old snapshot object still describes the state it pinned.
    assert first_row.nodes.metrics[0].statistic.value is not None
    assert next(
        metric.statistic.value for metric in first_row.nodes.metrics
        if metric.metric_name == first_metric.metric_name and metric.node_id == first_metric.node_id
    ) == first_value
