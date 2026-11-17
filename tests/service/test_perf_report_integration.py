"""Phase 08 integration: external roots, snapshot pinning and duplicate exports.

These tests compose the real service routes, the real export worker and real
archive layouts. Only the renderer is replaced, and only where the case is
about the lifecycle rather than the bytes.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from evalscope.service import perf_reports
from evalscope.service.app import create_app
from tests.service.test_perf_report_snapshot import _compiled, _record, _write_plan, _write_run

FIXTURES = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'perf_plan'
RUN_COUNT = 250
REQUEST_COUNT = 1201
runner_module = importlib.import_module('evalscope.service.perf_exports.runner')


@pytest.fixture(autouse=True)
def _outputs_root(tmp_path, monkeypatch):
    monkeypatch.setattr('evalscope.service.utils.log.OUTPUT_DIR', str(tmp_path))
    perf_reports._SNAPSHOT_CACHE.clear()
    perf_reports._REQUEST_INDEXES.clear()
    return tmp_path


def _client(root: Path):
    return create_app(outputs=str(root)).test_client()


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


def _start(client, kind: str, **payload) -> dict:
    response = client.post('/api/v1/perf/reports/exports', json={'kind': kind, 'locale': 'en', **payload})
    assert response.status_code in (200, 202), response.get_data(as_text=True)
    body = response.get_json()
    return {**body['export'], 'created': body['created']}


def _download(client, export: dict, name: str = 'html') -> bytes:
    url = next(artifact['url'] for artifact in export['artifacts'] if artifact['name'] == name)
    response = client.get(url)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.data


def _tree(root: Path) -> dict:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob('*'))
        if path.is_file()
    }


def _busy_plan(tmp_path: Path) -> dict:
    """A 250-tier matrix whose sixth run carries 1201 measured requests."""
    compiled = _compiled()
    base = compiled.runs[0]
    copied = [
        base.model_copy(update={'run_id': f'i1024-o128-r{index % 2}-l1-n1-{index}', 'order': index})
        for index in range(RUN_COUNT)
    ]
    large = compiled.model_copy(update={'runs': copied, 'run_count': len(copied)})
    busy_run = copied[5].run_id
    records = [
        _record(
            index,
            ttft_seconds=0.001 * (index + 1),
            latency_seconds=0.01 * (index + 1),
            aiperf_output_sequence_tokens=5,
            prompt_tokens=10 + index,
        )
        for index in range(REQUEST_COUNT)
    ]
    _write_plan(tmp_path, compiled=large, results={busy_run: records})
    snapshot = perf_reports.load_report_snapshot(outputs_root=str(tmp_path), task_id='t1')
    busy_key = next(run.run_key for run in snapshot.document.runs if run.run_id == busy_run)
    return {'busy_run': busy_run, 'busy_key': busy_key}


def test_external_root_with_the_same_task_id_is_read_in_place(tmp_path):
    service_root = tmp_path / 'service'
    external = tmp_path / 'external'

    service_plan = _compiled()
    _write_plan(
        service_root,
        compiled=service_plan,
        results={service_plan.runs[0].run_id: [_record(index, ttft_seconds=0.1) for index in range(3)]},
    )
    document = json.loads((FIXTURES / 'matrix-osl-load.json').read_text())
    document['workload']['input_lengths'] = [2048]
    document['workload']['target_ratios'] = [0.0]
    document['load']['output_token_limits'] = [256]
    document['load']['points'] = [{'concurrency': 2}]
    from evalscope.perf.plans import compile_plan_text

    external_compiled = compile_plan_text(json.dumps(document))
    external_run = external_compiled.runs[0].run_id
    _write_plan(
        external,
        compiled=external_compiled,
        results={external_run: [_record(index, ttft_seconds=0.7) for index in range(5)]},
    )
    external_before = _tree(external)

    client = _client(service_root)
    service_summary = client.get('/api/v1/perf/reports', query_string={'task_id': 't1'}).get_json()
    external_summary = client.get(
        '/api/v1/perf/reports', query_string={'task_id': 't1', 'root_path': str(external)}
    ).get_json()

    assert service_summary['total_runs'] == 4 and external_summary['total_runs'] == 1
    assert service_summary['report']['counts']['observed_requests'] == 3
    assert external_summary['report']['counts']['observed_requests'] == 5
    assert external_summary['report']['snapshot_id'] != service_summary['report']['snapshot_id']

    runs = client.get(
        '/api/v1/perf/reports/runs', query_string={'task_id': 't1', 'root_path': str(external)}
    ).get_json()
    external_key = next(row['run_key'] for row in runs['rows'] if row['run_id'] == external_run)

    export = _wait(
        client,
        _start(client, 'html', task_id='t1', root_path=str(external))['export_id'],
    )
    assert export['status'] == 'completed'
    assert export['snapshot_id'] == external_summary['report']['snapshot_id']
    assert export['run_count'] == 1 and export['request_count'] == 5
    html = _download(client, export).decode('utf-8')
    assert external_run in html and '2048' in html

    requests = client.get(
        '/api/v1/perf/reports/requests',
        query_string={'task_id': 't1', 'root_path': str(external), 'run_key': external_key, 'limit': 50},
    ).get_json()
    assert requests['total'] == 5 and requests['run_id'] == external_run

    # The export lived with the archive it describes, never with the service root,
    # and the archive's own files were left exactly as they were.
    assert (external / 't1' / 'perf' / 'exports' / export['export_id']).is_dir()
    assert not (service_root / 't1' / 'perf' / 'exports').exists()
    after = {key: value for key, value in _tree(external).items() if not key.startswith('t1/perf/exports/')}
    assert after == external_before


def test_export_keeps_its_snapshot_when_the_archive_moves_on(tmp_path):
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    _write_plan(
        tmp_path,
        compiled=compiled,
        results={run_id: [_record(0, ttft_seconds=0.4, latency_seconds=0.8, aiperf_output_sequence_tokens=4)]},
    )
    client = _client(tmp_path)
    first = _wait(client, _start(client, 'html', task_id='t1')['export_id'])
    assert first['status'] == 'completed'
    first_html = _download(client, first)
    first_snapshot = first['snapshot_id']

    _write_run(
        tmp_path / 't1' / 'perf',
        run_id,
        [_record(0, ttft_seconds=0.9, latency_seconds=0.9, aiperf_output_sequence_tokens=4)],
        generation=hashlib.sha256(b'moved-on').hexdigest(),
    )

    # The completed export is immutable, and a new request binds the new snapshot.
    assert _download(client, first) == first_html
    second = _start(client, 'html', task_id='t1')
    assert second['export_id'] != first['export_id'] and second['created'] is True
    finished = _wait(client, second['export_id'])
    assert finished['snapshot_id'] != first_snapshot
    assert finished['status'] == 'completed'


def test_an_archive_change_after_queueing_renders_the_pinned_snapshot(tmp_path, monkeypatch):
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    _write_plan(
        tmp_path,
        compiled=compiled,
        results={run_id: [_record(0, ttft_seconds=0.4, latency_seconds=0.8, aiperf_output_sequence_tokens=4)]},
    )
    client = _client(tmp_path)
    pinned_id = client.get('/api/v1/perf/reports', query_string={'task_id': 't1'}).get_json()['report']['snapshot_id']
    original = runner_module.load_report_snapshot
    calls = {'count': 0}
    baseline = {}

    def moving(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        calls['count'] += 1
        if calls['count'] == 1:
            # Move the archive right after the export pinned its snapshot.
            _write_run(
                tmp_path / 't1' / 'perf',
                run_id,
                [_record(0, ttft_seconds=0.9, latency_seconds=0.9, aiperf_output_sequence_tokens=4)],
                generation=hashlib.sha256(b'while-queued').hexdigest(),
            )
            baseline.update(_tree(tmp_path / 't1' / 'perf'))
        return snapshot

    monkeypatch.setattr(runner_module, 'load_report_snapshot', moving)
    export = _wait(client, _start(client, 'html', task_id='t1')['export_id'])

    # The export renders the state it was queued for, never a mix with the move.
    assert export['status'] == 'completed', export['error']
    assert export['snapshot_id'] == pinned_id
    assert calls['count'] == 1
    html = _download(client, export).decode('utf-8')
    assert '0.4' in html or '400' in html
    assert '0.9' not in html and '900' not in html
    after = {key: value for key, value in _tree(tmp_path / 't1' / 'perf').items() if not key.startswith('exports/')}
    assert baseline and after == baseline


def test_concurrent_identical_exports_render_once(tmp_path):
    plan = _busy_plan(tmp_path)
    clients = [_client(tmp_path), _client(tmp_path)]
    barrier = threading.Barrier(2)

    def create(client):
        barrier.wait(timeout=30)
        return _start(client, 'html', task_id='t1')

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(create, clients))

    assert first['export_id'] == second['export_id']
    assert sorted([first['created'], second['created']]) == [False, True]

    finished = _wait(clients[0], first['export_id'])
    assert finished['status'] == 'completed'
    assert finished['attempts'] == 1
    export_dir = tmp_path / 't1' / 'perf' / 'exports' / first['export_id']
    assert len(list(export_dir.glob('perf-report-*.html'))) == 1
    assert plan['busy_run'] in _download(clients[0], finished).decode('utf-8')


def test_a_finished_tier_without_readable_requests_explains_instead_of_guessing(tmp_path):
    compiled = _compiled()
    missing = compiled.runs[0].run_id
    broken = compiled.runs[1].run_id
    _write_plan(tmp_path, compiled=compiled, statuses={missing: 'completed', broken: 'completed'})
    _write_run(tmp_path / 't1' / 'perf', broken, [_record(0, ttft_seconds=0.2)])
    run_dir = tmp_path / 't1' / 'perf' / 'runs' / broken / 'normalized'
    generation = json.loads((run_dir / 'current.json').read_text())['generation']
    (run_dir / 'generations' / generation / 'requests.db').unlink()

    snapshot = perf_reports.load_report_snapshot(outputs_root=str(tmp_path), task_id='t1')
    rows = {row.run_id: row for row in snapshot.document.runs}

    assert rows[missing].lifecycle == 'completed'
    assert rows[missing].ttft.availability == 'unavailable'
    assert rows[missing].ttft.reason == 'normalized_result_missing'
    assert rows[missing].ttft.mean is None and rows[missing].tpot.mean is None
    assert rows[missing].counts.total == 0

    assert rows[broken].lifecycle == 'completed'
    assert rows[broken].ttft.availability == 'unavailable'
    assert rows[broken].ttft.reason == 'invalid_normalized_result'
    assert rows[broken].ttft.mean is None and rows[broken].tpot.mean is None


def test_the_request_index_is_shared_safely_between_service_threads(tmp_path):
    plan = _busy_plan(tmp_path)
    snapshot = perf_reports.load_report_snapshot(outputs_root=str(tmp_path), task_id='t1')

    def read(offset: int) -> tuple:
        total, rows = snapshot.requests_page(run_key=plan['busy_key'], offset=offset, limit=50)
        return total, rows[0].index

    # The first worker builds the SQLite index; the others reuse it from their
    # own threads, exactly as export workers and Flask request threads do.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(read, range(0, 400, 50)))

    assert all(total == REQUEST_COUNT for total, _index in results)
    assert [index for _total, index in results] == [0, 50, 100, 150, 200, 250, 300, 350]
    assert snapshot.top_requests(run_key=plan['busy_key'], limit=2)[0].index == REQUEST_COUNT - 1


def test_the_last_tier_and_the_last_request_are_reachable_through_the_api(tmp_path):
    plan = _busy_plan(tmp_path)
    client = _client(tmp_path)

    runs = client.get(
        '/api/v1/perf/reports/runs', query_string={'task_id': 't1', 'offset': 200, 'limit': 100, 'sort': 'order'}
    ).get_json()
    assert runs['total'] == RUN_COUNT
    assert len(runs['rows']) == 50
    assert runs['rows'][-1]['order'] == RUN_COUNT - 1

    last = client.get(
        '/api/v1/perf/reports/requests',
        query_string={
            'task_id': 't1',
            'run_key': plan['busy_key'],
            'offset': REQUEST_COUNT - 1,
            'limit': 50,
            'sort': 'index',
        },
    ).get_json()
    assert last['total'] == REQUEST_COUNT
    assert [row['index'] for row in last['requests']] == [REQUEST_COUNT - 1]

    slowest = client.get(
        '/api/v1/perf/reports/requests',
        query_string={
            'task_id': 't1',
            'run_key': plan['busy_key'],
            'limit': 3,
            'sort': 'latency',
            'order': 'desc',
        },
    ).get_json()
    assert [row['index'] for row in slowest['requests']] == [REQUEST_COUNT - 1, REQUEST_COUNT - 2, REQUEST_COUNT - 3]
