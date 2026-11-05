"""Mixed native and AIPerf archive behavior using local recorded fixtures."""

import json
import shutil
from pathlib import Path

import pytest

from evalscope.perf.results.aiperf_import import import_aiperf_run
from evalscope.perf.results.models import AIPerfImportArguments
from evalscope.perf.results.single_run import SingleRunManifest, write_single_manifest
from evalscope.service.app import create_app
from evalscope.service.utils import log

FIXTURES = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf'


@pytest.fixture
def archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from evalscope.service.blueprints import perf, tasks

    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(tasks, 'OUTPUT_DIR', str(tmp_path))
    native = tmp_path / 'native'
    shutil.copytree(FIXTURES / 'native', native)
    run = tmp_path / 'task' / 'perf'
    raw = run / 'raw' / 'aiperf'
    raw.mkdir(parents=True)
    for name in ('profile_export_aiperf.json', 'profile_export.jsonl', 'phase_manifest.json'):
        shutil.copyfile(FIXTURES / name, raw / name)
    result = import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(run / 'normalized')))
    manifest = SingleRunManifest(task_id='task', model='<script>model</script>', endpoint='host:8000',
                                 load_mode='closed_loop', concurrency=2, planned_requests=2, output_token_limit=8,
                                 input_sha256='a' * 64, status='error', artifact_status='available',
                                 normalized='normalized', quality=result.quality, quality_reasons=result.quality_reasons)
    write_single_manifest(str(run), manifest)
    (run.parent / 'task_info.json').write_text(json.dumps({'kind': 'perf', 'engine': 'aiperf', 'status': 'error'}))
    return create_app(outputs=str(tmp_path)).test_client(), tmp_path, run


def test_mixed_list_detail_requests_and_partial_links(archive) -> None:
    client, root, _ = archive
    listing = client.get('/api/v1/perf/list', query_string={'root_path': str(root)})
    assert listing.status_code == 200
    runs = {row['path']: row for row in listing.json['runs']}
    assert set(runs) == {'native', 'task/perf'}
    assert runs['native']['num_runs'] == 2
    assert runs['task/perf']['engine'] == 'aiperf' and runs['task/perf']['total_requests'] == 2
    assert runs['task/perf']['quality'] == 'complete'
    params = {'root_path': str(root), 'path': 'task/perf'}
    detail = client.get('/api/v1/perf/detail', query_string=params)
    assert detail.status_code == 200 and detail.json['engine'] == 'aiperf'
    assert detail.json['basic_info']['Status'] == 'error'
    assert detail.json['basic_info']['Server Cache Reads'] == 'unavailable'
    assert detail.json['summary_rows'][0]['values']['avg_latency'] == pytest.approx(0.0006758125)
    items = client.get('/api/v1/perf/runs', query_string=params)
    assert items.status_code == 200 and items.json['runs'][0]['dir_name'] == 'single'
    response = client.get('/api/v1/perf/requests', query_string={**params, 'run': 'single', 'page_size': 1})
    assert response.status_code == 200 and response.json['total'] == 2
    assert len(response.json['rows']) == 1 and response.json['rows'][0]['Cached'] is None
    assert response.json['rows'][0]['Input source'] == 'server_usage'
    task = client.get('/api/v1/tasks/task')
    assert task.status_code == 200 and task.json['status'] == 'error'
    assert task.json['report_url'].startswith('/perf-report?')
    assert task.json['html_report_url'].startswith('/api/v1/perf/history/report?')


def test_html_and_charts_are_safe_and_unsupported_metrics_remain_unavailable(archive) -> None:
    client, root, _ = archive
    params = {'root_path': str(root), 'path': 'task/perf'}
    report = client.get('/api/v1/perf/history/report', query_string=params)
    assert report.status_code == 200 and b'&lt;script&gt;model&lt;/script&gt;' in report.data
    assert b'Node metrics unavailable.' in report.data
    assert b'<script>model</script>' not in report.data
    assert report.headers['Content-Security-Policy'] == "default-src 'none'"
    for chart in ('latency', 'ttft', 'rps', 'throughput', 'success', 'percentile_latency', 'req_latency', 'req_tokens'):
        response = client.get('/api/v1/perf/chart', query_string={**params, 'chart_type': chart, 'run': 'single'})
        assert response.status_code == 200, chart
    assert client.get('/api/v1/perf/chart', query_string={**params, 'chart_type': 'tpot'}).status_code == 404
    assert client.get('/api/v1/perf/compare/chart', query_string={'root_path': str(root), 'paths': 'native;task/perf'}).status_code == 400


def test_single_html_includes_escaped_node_windows_and_missing_values(archive) -> None:
    from evalscope.perf.integrations.aiperf.metrics import MAPPINGS, NodeMetricsAnalysis, NodeMetricWindow
    from evalscope.perf.results.telemetry import HistogramWindow, WindowStatistic

    client, root, run = archive
    metric = next(item for item in MAPPINGS if item.key == 'waiting_requests' and item.engine == 'vllm')
    analysis = NodeMetricsAnalysis(
        availability='partial', max_gap_ns=100, window_source='aiperf_phase',
        series=[NodeMetricWindow(node_id='<node>', role='prefill', engine='vllm', labels={'model': '<script>'},
                                 metric=metric, window_source='aiperf_phase',
                                 statistic=WindowStatistic(availability='partial', value=2, sample_count=3,
                                                           reason='scrape_gap')),
                NodeMetricWindow(node_id='decode', role='decode', engine='sglang', labels={},
                                 metric=next(item for item in MAPPINGS if item.key == 'kv_transfer_latency'
                                             and item.kind == 'histogram'), window_source='aiperf_phase',
                                 histogram=HistogramWindow(availability='missing', metric_key='kv_transfer_latency',
                                                           unit='seconds', window_start_ns=10, window_end_ns=20,
                                                           reason='window_not_bracketed'))],
        cache_ratios=[{'node_id': '<node>', 'role': 'prefill', 'labels': {}, 'availability': 'available',
                       'ratio': 0.8, 'hit_tokens': 8, 'lookup_tokens': 10}],
    )
    (run / 'node_analysis.json').write_text(analysis.model_dump_json())
    report = client.get('/api/v1/perf/history/report', query_string={'root_path': str(root), 'path': 'task/perf'})
    assert report.status_code == 200
    assert b'&lt;node&gt;' in report.data and b'&lt;script&gt;' in report.data
    assert b'<script>' not in report.data
    assert b'vllm:num_requests_waiting' in report.data and b'0.8' in report.data
    assert b'scrape_gap' in report.data and b'unavailable' in report.data
    assert b'kv_transfer_latency' in report.data and b'window_not_bracketed' in report.data
    assert b'not per-request P/D timings' in report.data
    assert report.headers['Content-Security-Policy'] == "default-src 'none'"


def test_archive_identity_symlink_and_running_delete_are_checked(archive, monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.service.blueprints import perf

    client, root, run = archive
    params = {'root_path': str(root), 'path': 'task/perf'}
    assert client.get('/api/v1/perf/requests', query_string={**params, 'run': '../single'}).status_code == 404
    assert client.get('/api/v1/perf/detail', query_string={'root_path': str(root), 'path': '../outside'}).status_code == 400
    monkeypatch.setattr(perf, 'active_task_ids', lambda: {'task'})
    assert client.delete('/api/v1/perf/run', query_string=params).status_code == 409
    monkeypatch.setattr(perf, 'active_task_ids', lambda: set())
    outside = root.parent / (root.name + '-outside')
    outside.mkdir()
    shutil.rmtree(run / 'normalized')
    (run / 'normalized').symlink_to(outside, target_is_directory=True)
    assert client.get('/api/v1/perf/detail', query_string=params).status_code == 400
    assert client.delete('/api/v1/perf/run', query_string=params).status_code == 200
    assert outside.is_dir()


def test_single_node_analysis_and_metrics_use_manifest_identity(archive) -> None:
    from evalscope.perf.integrations.aiperf.metrics import NodeMetricsAnalysis
    from evalscope.perf.results.single_run import load_single_manifest

    client, root, run = archive
    analysis = NodeMetricsAnalysis(availability='missing', reason='no_samples', max_gap_ns=1_000_000_000)
    (run / 'node_analysis.json').write_text(analysis.model_dump_json())
    manifest = load_single_manifest(str(run)).model_copy(update={'quality': 'invalid',
                                                                  'quality_reasons': ['required_telemetry_missing']})
    write_single_manifest(str(run), manifest)
    params = {'root_path': str(root), 'path': 'task/perf'}
    detail = client.get('/api/v1/perf/detail', query_string=params)
    assert detail.status_code == 200 and detail.json['quality'] == 'invalid'
    assert detail.json['node_analysis']['availability'] == 'missing'
    assert 'required_telemetry_missing' in detail.json['quality_reasons']
    samples = client.get('/api/v1/perf/server-metrics', query_string=params)
    assert samples.status_code == 200 and samples.json['total'] == 0
    assert client.get('/api/v1/perf/server-metrics', query_string={**params, 'node_id': 'unknown'}).status_code == 404
    (run / 'node_analysis.json').unlink()
    outside = root.parent / 'foreign-single-analysis.json'
    outside.write_text(analysis.model_dump_json())
    (run / 'node_analysis.json').symlink_to(outside)
    assert client.get('/api/v1/perf/detail', query_string=params).status_code == 400


def test_single_node_series_uses_normalized_archive(archive) -> None:
    import sqlite3

    from evalscope.perf.results.models import NodeMetricSample

    client, root, run = archive
    normalized = run / 'normalized'
    identity = json.loads((normalized / 'current.json').read_text())['generation']
    database = normalized / 'generations' / identity / 'requests.db'
    sample = NodeMetricSample(timestamp_ns=123, node_id='p', role='prefill', engine='vllm', endpoint='host',
                              metric_name='queue', metric_type='gauge', labels={}, observation={'value': 0})
    with sqlite3.connect(database) as connection:
        connection.execute('INSERT INTO node_samples (node_id, metric_name, timestamp_ns, sample_json) '
                           'VALUES (?, ?, ?, ?)', ('p', 'queue', 123, sample.model_dump_json()))
    query = {'root_path': str(root), 'path': 'task/perf', 'downsample': 'uniform_index', 'node_id': 'p',
             'metric_name': 'queue', 'labels': '{}'}
    response = client.get('/api/v1/perf/server-metrics', query_string=query)
    assert response.status_code == 200
    assert response.json['series']['total'] == 1 and response.json['series']['downsample'] == 'none'
    assert response.json['series']['samples'][0]['observation']['value'] == 0
