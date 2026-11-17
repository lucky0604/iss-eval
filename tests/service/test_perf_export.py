"""Independent report exports: every tier, every request, no parent-state change."""

from __future__ import annotations

import csv
import hashlib
import importlib
import io
import json
import time
import zipfile
from pathlib import Path

import pytest

from evalscope.service import perf_exports, perf_reports
from evalscope.service.app import create_app
from evalscope.service.perf_exports import ExportSpec, runner
from evalscope.service.perf_exports.pdf import PdfUnavailable
from tests.service.test_perf_report_snapshot import _compiled, _record, _write_plan, _write_run

RUN_COUNT = 250
REQUEST_COUNT = 1201
runner_module = importlib.import_module('evalscope.service.perf_exports.runner')


@pytest.fixture(autouse=True)
def _outputs_root(tmp_path, monkeypatch):
    monkeypatch.setattr('evalscope.service.utils.log.OUTPUT_DIR', str(tmp_path))
    perf_reports._SNAPSHOT_CACHE.clear()
    perf_reports._REQUEST_INDEXES.clear()
    return tmp_path


def _plan(tmp_path: Path, *, runs: int = RUN_COUNT, requests: int = REQUEST_COUNT) -> dict:
    """A 250-tier matrix whose sixth run carries ``requests`` measured requests."""
    compiled = _compiled()
    base = compiled.runs[0]
    copied = [
        base.model_copy(update={'run_id': f'i1024-o128-r{index % 2}-l1-n1-{index}', 'order': index})
        for index in range(runs)
    ]
    large = compiled.model_copy(update={'runs': copied, 'run_count': len(copied)})
    busy_run = copied[5].run_id
    zero_run = copied[6].run_id
    failed_run = copied[7].run_id
    records = [
        _record(
            index,
            ttft_seconds=0.001 * (index + 1),
            latency_seconds=0.01 * (index + 1),
            aiperf_output_sequence_tokens=5,
            prompt_tokens=10 + index,
            output_tokens=5,
        )
        for index in range(requests)
    ]
    records.append(_record(requests, status='error', status_reason='rate-limited', start_ns=1, end_ns=2))
    results = {
        busy_run: records,
        zero_run: [
            _record(index, ttft_seconds=0.0, latency_seconds=0.5, aiperf_output_sequence_tokens=5)
            for index in range(3)
        ],
        failed_run: [
            _record(index, status='error', status_reason='connect', start_ns=1, end_ns=2)
            for index in range(2)
        ],
    }
    _write_plan(tmp_path, compiled=large, results=results, statuses={failed_run: 'error'}, quality='partial')
    snapshot = perf_reports.load_report_snapshot(outputs_root=str(tmp_path), task_id='t1')
    busy_key = next(run.run_key for run in snapshot.document.runs if run.run_id == busy_run)
    return {
        'busy_run': busy_run,
        'busy_key': busy_key,
        'zero_run': zero_run,
        'failed_run': failed_run,
        'requests': requests,
        'location': {'root_path': str(tmp_path), 'path': 't1/perf'},
    }


def _client(tmp_path: Path):
    return create_app(outputs=str(tmp_path)).test_client()


def _wait(client, export_id: str, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f'/api/v1/perf/reports/exports/{export_id}')
        assert response.status_code == 200, response.get_data(as_text=True)
        export = response.get_json()['export']
        if export['status'] in ('completed', 'failed', 'cancelled', 'interrupted'):
            return export
        if time.monotonic() > deadline:
            raise AssertionError(f'Export {export_id} did not finish: {export}')
        time.sleep(0.05)


def _start(client, location: dict, kind: str, **overrides) -> dict:
    payload = {'kind': kind, 'locale': 'en', **location, **overrides}
    response = client.post('/api/v1/perf/reports/exports', json=payload)
    assert response.status_code in (200, 202), response.get_data(as_text=True)
    body = response.get_json()
    return {**body['export'], 'created': body['created']}


def _download(client, export: dict, name: str) -> bytes:
    url = next(artifact['url'] for artifact in export['artifacts'] if artifact['name'] == name)
    response = client.get(url)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.data


def _tree(root: Path) -> dict:
    """Every file with its digest, for a before/after state comparison."""
    entries = {}
    for path in sorted(root.rglob('*')):
        if path.is_file():
            entries[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return entries


def _write_node_analysis(run_dir: Path) -> None:
    """The analysis a completed run already persisted; the report never runs it."""
    from evalscope.perf.integrations.aiperf.metrics import (
        MAPPINGS,
        MetricWindow,
        NodeCacheRatio,
        NodeMetricsAnalysis,
        NodeMetricWindow,
    )
    from evalscope.perf.results.telemetry import WindowStatistic

    mapping = next(item for item in MAPPINGS if item.engine == 'vllm' and item.name.endswith('kv_cache_usage_perc'))
    analysis = NodeMetricsAnalysis(
        availability='available',
        window_source='aiperf_phase',
        window=MetricWindow(start_ns=1_000_000_000, end_ns=3_000_000_000),
        timed_requests=200,
        total_requests=200,
        max_gap_ns=1_500_000_000,
        series=[
            NodeMetricWindow(
                node_id='prefill-0',
                role='prefill',
                engine='vllm',
                labels={'instance': 'vllm-0'},
                metric=mapping,
                window_source='aiperf_phase',
                statistic=WindowStatistic(
                    availability='available',
                    value=0.42,
                    sample_peak=0.87,
                    coverage=1.0,
                    sample_count=12,
                    boundary='step',
                ),
            )
        ],
        cache_ratios=[
            NodeCacheRatio(
                node_id='prefill-0',
                role='prefill',
                labels={'instance': 'vllm-0'},
                availability='available',
                hit_tokens=900,
                lookup_tokens=1000,
                ratio=0.9,
            )
        ],
    )
    (run_dir / 'node_analysis.json').write_text(analysis.model_dump_json(), encoding='utf-8')


def test_html_contains_every_tier_and_needs_no_network(tmp_path):
    plan = _plan(tmp_path)
    client = _client(tmp_path)
    export = _wait(client, _start(client, plan['location'], 'html', locale='zh')['export_id'])
    assert export['status'] == 'completed'
    assert export['run_count'] == RUN_COUNT
    html = _download(client, export, 'html').decode('utf-8')

    assert html.startswith('<!DOCTYPE html>') and 'lang="zh-CN"' in html
    # ISL and repetition are both identity cells, so two per tier row.
    assert html.count('class="num dim"') == 2 * RUN_COUNT
    assert export['snapshot_id'] in html
    assert '性能压测矩阵报告' in html
    assert '<svg' in html and 'xmlns="http://www.w3.org/2000/svg"' in html
    assert '<script' not in html and ' src=' not in html and ' href=' not in html
    assert "default-src 'none'" in html
    # TTFT and TPOT each expose four independent statistic columns.
    assert html.count('TTFT（毫秒）') >= 2 and html.count('TPOT（毫秒/token）') >= 2


def test_node_telemetry_reaches_the_print_report_and_a_flat_csv(tmp_path):
    plan = _plan(tmp_path, requests=5)
    _write_node_analysis(tmp_path / 't1' / 'perf' / 'runs' / plan['busy_run'])
    perf_reports._SNAPSHOT_CACHE.clear()
    client = _client(tmp_path)

    csv_export = _wait(client, _start(client, plan['location'], 'csv')['export_id'])
    assert csv_export['status'] == 'completed'
    rows = list(csv.DictReader(io.StringIO(_download(client, csv_export, 'node_metrics').decode('utf-8-sig'))))
    assert len(rows) == 1
    row = rows[0]
    assert (row['run_id'], row['node_id'], row['role'], row['metric_name']) == (
        plan['busy_run'], 'prefill-0', 'prefill', 'vllm:kv_cache_usage_perc'
    )
    assert float(row['value']) == 0.42 and float(row['sample_peak']) == 0.87
    assert row['coverage'] == '1.0' and row['sample_count'] == '12' and row['boundary'] == 'step'
    assert json.loads(row['labels']) == {'instance': 'vllm-0'}
    cache_rows = list(csv.DictReader(io.StringIO(_download(client, csv_export, 'node_cache').decode('utf-8-sig'))))
    assert cache_rows[0]['ratio'] == '0.9' and cache_rows[0]['semantics'] == 'vllm_prefix_lookup_tokens'

    html_export = _wait(client, _start(client, plan['location'], 'html')['export_id'])
    assert html_export['snapshot_id'] == csv_export['snapshot_id']
    html = _download(client, html_export, 'html').decode('utf-8')
    assert 'vllm:kv_cache_usage_perc' in html
    assert '0.4200' in html and '0.8700' in html
    assert 'vllm_prefix_lookup_tokens' in html


def test_print_report_states_absent_node_telemetry_instead_of_dropping_it(tmp_path):
    plan = _plan(tmp_path, requests=5)
    client = _client(tmp_path)
    export = _wait(client, _start(client, plan['location'], 'html')['export_id'])
    html = _download(client, export, 'html').decode('utf-8')
    assert 'no_persisted_node_analysis' in html
    assert 'Node metrics' in html or '节点监控' in html


def test_summary_csv_keeps_missing_empty_and_a_measured_zero_numeric(tmp_path):
    plan = _plan(tmp_path)
    client = _client(tmp_path)
    export = _wait(client, _start(client, plan['location'], 'csv', preset='all')['export_id'])
    assert export['status'] == 'completed'
    rows = list(csv.DictReader(io.StringIO(_download(client, export, 'summary').decode('utf-8-sig'))))

    assert len(rows) == RUN_COUNT
    assert rows[0]['run_id'] and rows[0]['run_key']
    assert all(header.isascii() for header in rows[0])
    zero = next(row for row in rows if row['run_id'] == plan['zero_run'])
    assert zero['ttft_avg_ms'] == '0.0'
    failed = next(row for row in rows if row['run_id'] == plan['failed_run'])
    assert failed['ttft_avg_ms'] == '' and failed['lifecycle'] == 'error'
    busy = next(row for row in rows if row['run_id'] == plan['busy_run'])
    assert float(busy['rpm']) > 0
    assert busy['tpot_p90_ms'] and busy['tpot_p99_ms']


def test_request_export_streams_every_request_beyond_the_page_limit(tmp_path):
    plan = _plan(tmp_path)
    client = _client(tmp_path)
    export = _wait(
        client,
        _start(client, plan['location'], 'requests', run_key=plan['busy_key'])['export_id'],
    )
    assert export['status'] == 'completed'
    rows = list(csv.DictReader(io.StringIO(_download(client, export, 'requests_csv').decode('utf-8-sig'))))

    assert len(rows) == plan['requests'] + 1
    assert rows[0]['run_id'] == plan['busy_run']
    assert [row['index'] for row in rows[:3]] == ['0', '1', '2']
    assert rows[-1]['status'] == 'error' and rows[-1]['ttft_ms'] == ''
    assert rows[-1]['status_reason'] == 'rate-limited'
    assert rows[100]['tpot_ms'] and float(rows[100]['tpot_ms']) > 0

    jsonl = _download(client, export, 'requests_jsonl').decode('utf-8').strip().splitlines()
    assert len(jsonl) == plan['requests'] + 1
    assert json.loads(jsonl[0])['run_key'] == plan['busy_key']

    filtered_export = _wait(
        client,
        _start(
            client, plan['location'], 'requests', run_key=plan['busy_key'], status='failed', phase='profiling'
        )['export_id'],
    )
    filtered = list(
        csv.DictReader(io.StringIO(_download(client, filtered_export, 'requests_csv').decode('utf-8-sig')))
    )
    assert len(filtered) == 1 and filtered[0]['status_reason'] == 'rate-limited'


def test_bundle_zip_has_manifest_hashes_and_skips_pdf_with_a_warning(tmp_path, monkeypatch):
    plan = _plan(tmp_path, requests=50)

    def unavailable(*_args, **_kwargs):
        raise PdfUnavailable('no renderer in this test')

    monkeypatch.setattr(runner_module, 'render_pdf', unavailable)
    client = _client(tmp_path)
    export = _wait(client, _start(client, plan['location'], 'bundle')['export_id'])
    assert export['status'] == 'completed'
    assert any('PDF skipped' in warning for warning in export['warnings'])

    archive = zipfile.ZipFile(io.BytesIO(_download(client, export, 'bundle')))
    assert set(archive.namelist()) == {
        'report.html', 'summary.csv', 'requests.csv', 'requests.jsonl', 'manifest.json'
    }
    manifest = json.loads(archive.read('manifest.json'))
    assert manifest['snapshot_id'] == export['snapshot_id']
    assert manifest['run_count'] == RUN_COUNT
    for entry in manifest['artifacts']:
        assert hashlib.sha256(archive.read(entry['path'])).hexdigest() == entry['sha256']


def test_pdf_only_export_fails_with_the_renderer_reason(tmp_path, monkeypatch):
    plan = _plan(tmp_path, requests=10)

    def unavailable(*_args, **_kwargs):
        raise PdfUnavailable('install playwright to render PDF')

    monkeypatch.setattr(runner_module, 'render_pdf', unavailable)
    client = _client(tmp_path)
    export = _wait(client, _start(client, plan['location'], 'pdf')['export_id'])
    assert export['status'] == 'failed'
    assert 'install playwright' in export['error']
    assert export['artifacts'] == []
    assert client.get(f'/api/v1/perf/reports/exports/{export["export_id"]}/download').status_code == 409


def test_export_does_not_touch_the_load_test_archive(tmp_path):
    plan = _plan(tmp_path, requests=20)
    perf_root = tmp_path / 't1' / 'perf'
    before = _tree(perf_root)
    client = _client(tmp_path)

    first = _start(client, plan['location'], 'html')
    second = _start(client, plan['location'], 'html')
    assert second['export_id'] == first['export_id'] and second['created'] is False
    _wait(client, first['export_id'])

    after = {key: value for key, value in _tree(perf_root).items() if not key.startswith('exports/')}
    assert after == before
    assert (perf_root / 'exports' / first['export_id']).is_dir()
    manifest = json.loads((perf_root / 'plan' / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'completed'


def test_cancel_then_retry_uses_the_same_independent_record(tmp_path):
    _plan(tmp_path, requests=20)
    client = _client(tmp_path)
    spec = ExportSpec(kind='html', task_id='t1', root_path=str(tmp_path))
    store, snapshot = runner.store_for(spec, outputs_root=str(tmp_path))
    record, created = store.create_or_get(
        spec,
        snapshot_id=snapshot.document.snapshot_id,
        snapshot_created_at=snapshot.document.created_at,
        source_kind=snapshot.document.source_kind,
        source_identity=snapshot.document.source_identity or 'test',
    )
    assert created is True

    cancelled = client.post(f'/api/v1/perf/reports/exports/{record.export_id}/cancel').get_json()['export']
    assert cancelled['status'] == 'cancelled'

    retried = client.post(f'/api/v1/perf/reports/exports/{record.export_id}/retry')
    assert retried.status_code == 202 and retried.get_json()['created'] is True
    assert _wait(client, record.export_id)['status'] == 'completed'

    # A running export is asked to stop through its own marker file.
    running, _ = store.create_or_get(
        ExportSpec(kind='csv', task_id='t1', root_path=str(tmp_path)),
        snapshot_id=snapshot.document.snapshot_id,
        snapshot_created_at=snapshot.document.created_at,
        source_kind=snapshot.document.source_kind,
        source_identity=snapshot.document.source_identity or 'test',
    )
    assert store.claim(running.export_id) is not None
    store.request_cancel(running.export_id)
    assert (store.base / running.export_id / 'cancel.requested').exists()


def test_interrupted_exports_recover_without_artifacts(tmp_path):
    plan = _plan(tmp_path, requests=10)
    client = _client(tmp_path)
    export = _wait(client, _start(client, plan['location'], 'html')['export_id'])
    assert export['status'] == 'completed'

    store = perf_exports.ExportStore(tmp_path / 't1' / 'perf' / 'exports')
    record = store.load(export['export_id'])
    assert record is not None
    record.status = 'running'
    record.artifacts = []
    store.save(record)
    (store.base / record.export_id / 'report.html').write_text('partial', encoding='utf-8')

    assert perf_exports.recover_exports(str(tmp_path)) >= 1
    recovered = store.load(record.export_id)
    assert recovered.status == 'interrupted' and recovered.artifacts == []
    assert not (store.base / record.export_id / 'report.html').exists()

    retried = client.post(f'/api/v1/perf/reports/exports/{record.export_id}/retry')
    assert retried.status_code == 202
    assert _wait(client, record.export_id)['status'] == 'completed'


def test_download_and_ids_never_accept_a_path(tmp_path):
    plan = _plan(tmp_path, requests=10)
    client = _client(tmp_path)
    export = _wait(client, _start(client, plan['location'], 'html')['export_id'])

    assert client.get(
        f'/api/v1/perf/reports/exports/{export["export_id"]}/download?file=../../etc/passwd'
    ).status_code == 404
    traversal = client.get('/api/v1/perf/reports/exports/../../etc/download')
    assert 'attachment' not in (traversal.headers.get('Content-Disposition') or '')
    assert client.get('/api/v1/perf/reports/exports/not-a-hex-id').status_code == 404
    assert client.get('/api/v1/perf/reports/exports/deadbeefdeadbeefdead/download').status_code == 404
    assert client.get(f'/api/v1/perf/reports/exports/{export["export_id"]}/download?file=html').status_code == 200


def test_capabilities_report_the_pdf_runtime(tmp_path):
    body = _client(tmp_path).get('/api/v1/perf/reports/export-capabilities').get_json()
    assert set(body['kinds']) == {'html', 'pdf', 'csv', 'requests', 'bundle'}
    assert isinstance(body['pdf_available'], bool)
    assert body['pdf_message'] is not None


def test_legacy_export_links_use_the_export_lifecycle(tmp_path):
    plan = _plan(tmp_path, requests=30)
    client = _client(tmp_path)
    query = {'root_path': str(tmp_path), 'path': 't1/perf'}

    response = client.get('/api/v1/perf/reports/export.html', query_string=query)
    assert response.status_code == 302
    location = response.headers['Location']
    assert '/exports/' in location and 'file=html' in location
    assert client.get(location).status_code == 200

    requests_response = client.get(
        '/api/v1/perf/reports/requests.csv',
        query_string={**query, 'run_key': plan['busy_key']},
    )
    assert requests_response.status_code == 302
    rows = list(
        csv.DictReader(
            io.StringIO(client.get(requests_response.headers['Location']).data.decode('utf-8-sig'))
        )
    )
    assert len(rows) == plan['requests'] + 1


def test_export_renders_pinned_snapshot_after_source_change(tmp_path, monkeypatch):
    """A queued export renders the generation it was queued for, not the latest."""
    plan = _plan(tmp_path, requests=5)
    deferred = []

    class _Deferred:
        def submit(self, function, *args):
            deferred.append((function, args))

    monkeypatch.setattr(runner_module.runner, '_executor_for', lambda: _Deferred())
    spec = ExportSpec(kind='requests', task_id='t1', root_path=str(tmp_path), run_key=plan['busy_key'])
    record, created = runner_module.runner.submit(spec, outputs_root=str(tmp_path))
    assert created is True
    assert record.snapshot_id

    _write_run(
        tmp_path / 't1' / 'perf',
        plan['busy_run'],
        [_record(0, ttft_seconds=9.0, latency_seconds=99.0, aiperf_output_sequence_tokens=5)],
        generation=hashlib.sha256(b'export-move').hexdigest(),
    )

    function, args = deferred[0]
    function(*args)
    done = runner_module.runner.get(record.export_id, outputs_root=str(tmp_path))
    assert done.status == 'completed', done.error
    assert done.snapshot_id == record.snapshot_id
    rows = list(
        csv.DictReader(
            io.StringIO(
                (
                    tmp_path
                    / 't1'
                    / 'perf'
                    / 'exports'
                    / record.export_id
                    / next(item.filename for item in done.artifacts if item.name == 'requests_csv')
                ).read_text(encoding='utf-8-sig')
            )
        )
    )
    assert len(rows) == plan['requests'] + 1
    assert float(rows[0]['latency_s']) < 1.0
    assert all(float(row['latency_s'] or 0) < 99.0 for row in rows)
