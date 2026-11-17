"""Unified report snapshot: exact percentiles, stable paging and every archive kind."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import sqlite3
from pathlib import Path

import pytest

from evalscope.perf.plans import compile_plan_text
from evalscope.perf.plans.execution import PerfPlanManifest, PlanRunState
from evalscope.perf.results.models import PerfRequestRecord
from evalscope.perf.utils.perf_models import BenchmarkSummary, PercentileResult, PercentileRow
from evalscope.service import perf_reports
from evalscope.service.app import create_app
from evalscope.service.perf_reports import PerfReportError, load_report_snapshot

FIXTURES = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'perf_plan'


@pytest.fixture(autouse=True)
def _outputs_root(tmp_path, monkeypatch):
    monkeypatch.setattr('evalscope.service.utils.log.OUTPUT_DIR', str(tmp_path))
    perf_reports._SNAPSHOT_CACHE.clear()
    perf_reports._REQUEST_INDEXES.clear()
    return tmp_path


def _record(index: int, *, phase: str = 'profiling', status: str = 'success', **fields) -> dict:
    record = PerfRequestRecord(
        index=index,
        request_id=f'req-{index}',
        phase=phase,
        status=status,  # type: ignore[arg-type]
        start_ns=fields.pop('start_ns', 1_000_000_000 + index * 10_000_000),
        end_ns=fields.pop('end_ns', 1_000_000_000 + index * 10_000_000 + 5_000_000),
        **fields,
    )
    return record.model_dump(mode='json')


def _write_run(perf_root: Path, run_id: str, records: list, generation: str | None = None) -> None:
    """Write one normalized generation with a valid 64-hex identity."""
    identity = generation or hashlib.sha256(run_id.encode('utf-8')).hexdigest()
    normalized = perf_root / 'runs' / run_id / 'normalized'
    generation_dir = normalized / 'generations' / identity
    generation_dir.mkdir(parents=True, exist_ok=True)
    (normalized / 'current.json').write_text(json.dumps({'generation': identity}), encoding='utf-8')
    (generation_dir / 'import.json').write_text(json.dumps({'input_hash': identity, 'files': {}}), encoding='utf-8')
    database = sqlite3.connect(generation_dir / 'requests.db')
    database.execute(
        'CREATE TABLE IF NOT EXISTS requests (idx INTEGER PRIMARY KEY, phase TEXT NOT NULL, '
        'status TEXT NOT NULL, record_json TEXT NOT NULL)'
    )
    database.executemany(
        'INSERT OR REPLACE INTO requests VALUES (?,?,?,?)',
        [(item['index'], item['phase'], item['status'], json.dumps(item)) for item in records],
    )
    database.commit()
    database.close()


def _compiled():
    document = json.loads((FIXTURES / 'matrix-osl-load.json').read_text())
    document['workload']['input_lengths'] = [1024]
    document['workload']['target_ratios'] = [0.0, 0.9]
    document['load']['output_token_limits'] = [128]
    document['load']['points'] = [{'concurrency': 1}, {'concurrency': 4}]
    return compile_plan_text(json.dumps(document))


def _write_plan(
    tmp_path: Path,
    *,
    compiled=None,
    results: dict | None = None,
    statuses: dict | None = None,
    reasons: dict | None = None,
    quality: str | None = 'complete',
) -> Path:
    compiled = compiled or _compiled()
    results = results or {}
    statuses = statuses or {}
    reasons = reasons or {}
    perf_root = tmp_path / 't1' / 'perf'
    (perf_root / 'plan').mkdir(parents=True, exist_ok=True)
    (perf_root / 'plan' / 'compiled.json').write_text(compiled.model_dump_json(), encoding='utf-8')
    (perf_root / 'plan' / 'normalized.json').write_text(
        json.dumps(compiled.normalized.model_dump(mode='json')), encoding='utf-8'
    )
    rows = []
    for planned in compiled.runs:
        status = statuses.get(planned.run_id, 'completed' if planned.run_id in results else 'not_started')
        rows.append(
            PlanRunState(
                run_id=planned.run_id,
                order=planned.order,
                workload_kind=planned.workload_kind,
                message_structure=planned.message_structure,
                load_mode=planned.load_mode,
                concurrency=planned.concurrency,
                rate=planned.rate,
                input_tokens=planned.input_tokens,
                output_token_limit=planned.output_token_limit,
                target_ratio=planned.target_ratio,
                repetition=planned.repetition,
                formal_requests=planned.request_count,
                primer_requests=planned.primer_requests,
                status=status,  # type: ignore[arg-type]
                quality='complete' if status == 'completed' else None,
                reason=reasons.get(planned.run_id),
            )
        )
    manifest = PerfPlanManifest(
        plan_hash=compiled.plan_hash,
        normalized_hash=compiled.normalized_hash,
        semantic_fingerprint=compiled.semantic_fingerprint,
        workload_semantics=compiled.workload_semantics,
        status='completed',
        phase='report',
        completed_runs=sum(1 for row in rows if row.status == 'completed'),
        total_runs=len(rows),
        quality=quality,  # type: ignore[arg-type]
        quality_reasons=[] if quality == 'complete' else ['partial_run_failure'],
        runs=rows,
        archive={'task_id': 't1'},
    )
    (perf_root / 'plan' / 'manifest.json').write_text(manifest.model_dump_json(), encoding='utf-8')
    for run_id, records in results.items():
        _write_run(perf_root, run_id, records)
    return perf_root


def _snapshot(tmp_path: Path, **kwargs):
    return load_report_snapshot(outputs_root=str(tmp_path), **kwargs)


def _nearest_rank(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def test_percentiles_are_exact_nearest_rank_not_interpolated(tmp_path):
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    ttfts = [0.1 * (index + 1) for index in range(10)]
    records = [_record(index, ttft_seconds=value, latency_seconds=value + 1.0,
                       aiperf_output_sequence_tokens=10) for index, value in enumerate(ttfts)]
    _write_plan(tmp_path, compiled=compiled, results={run_id: records})

    run = next(item for item in _snapshot(tmp_path, task_id='t1').document.runs if item.run_id == run_id)
    assert run.ttft.count == 10
    assert run.ttft.p50 == _nearest_rank(ttfts, 0.50) == 0.5
    assert run.ttft.p90 == _nearest_rank(ttfts, 0.90) == 0.9
    assert run.ttft.p95 == _nearest_rank(ttfts, 0.95) == 1.0
    assert run.ttft.p99 == 1.0
    assert run.ttft.percentile_method == 'nearest_rank'
    assert run.ttft.population == 'successful_profiling_requests'


def test_tpot_prefers_raw_itl_and_keeps_the_derivation_traceable(tmp_path):
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    records = [
        _record(0, ttft_seconds=0.1, latency_seconds=0.5, itl_seconds=0.05, itl_source='aiperf_raw',
                prompt_tokens=100, output_tokens=9, aiperf_output_sequence_tokens=9),
        _record(1, ttft_seconds=0.1, latency_seconds=0.5, aiperf_output_sequence_tokens=9,
                prompt_tokens=100, output_tokens=9),
        _record(2, status='error', status_reason='timeout', start_ns=1_000_000_000, end_ns=3_000_000_000),
    ]
    perf_root = _write_plan(tmp_path, compiled=compiled, results={run_id: records})
    snapshot = _snapshot(tmp_path, task_id='t1')
    run = next(item for item in snapshot.document.runs if item.run_id == run_id)

    assert run.tpot.count == 2
    assert run.tpot.mean == pytest.approx(0.05)
    assert run.tpot.sources == {'aiperf_raw': 1, 'derived': 1}
    assert run.counts.total == 3 and run.counts.success == 2 and run.counts.error == 1
    assert run.tokens.input_tokens == 200
    assert run.rates.requests_per_minute == pytest.approx(2 / 2.0 * 60)

    total, rows = snapshot.requests_page(run_key=run.run_key, sort='index')
    assert total == 3
    assert rows[0].tpot_source == 'aiperf_raw'
    assert rows[1].tpot_source == 'derived'
    assert rows[2].tpot_seconds is None and rows[2].status == 'error'
    assert str(perf_root).endswith('t1/perf')


def test_missing_values_are_null_and_a_measured_zero_stays_zero(tmp_path):
    compiled = _compiled()
    run_id = compiled.runs[1].run_id
    records = [
        _record(0, status='error', status_reason='connect', start_ns=1_000_000_000, end_ns=3_000_000_000),
        _record(1, status='error', status_reason='connect', start_ns=1_000_000_000, end_ns=3_000_000_000),
    ]
    _write_plan(tmp_path, compiled=compiled, results={run_id: records})
    run = next(item for item in _snapshot(tmp_path, task_id='t1').document.runs if item.run_id == run_id)

    assert run.ttft.availability == 'unavailable' and run.ttft.mean is None
    assert run.ttft.reason == 'no_successful_requests_with_ttft'
    assert run.tpot.mean is None
    assert run.tokens.input_tokens is None and run.tokens.output_tokens is None
    assert run.counts.success == 0
    assert run.rates.requests_per_minute == 0.0
    assert run.rates.window_seconds == pytest.approx(2.0)


def test_planned_rows_survive_not_started_and_failed_tiers(tmp_path):
    compiled = _compiled()
    completed = compiled.runs[0].run_id
    failed = compiled.runs[1].run_id
    skipped = compiled.runs[2].run_id
    results = {completed: [_record(0, ttft_seconds=0.2, latency_seconds=0.4, aiperf_output_sequence_tokens=4)]}
    _write_plan(
        tmp_path,
        compiled=compiled,
        results=results,
        statuses={completed: 'completed', failed: 'error', skipped: 'not_started'},
        quality='partial',
    )
    snapshot = _snapshot(tmp_path, task_id='t1')
    document = snapshot.document

    assert len(document.runs) == compiled.run_count
    assert document.counts.planned_runs == compiled.run_count
    assert document.counts.completed_runs == 1
    assert document.counts.failed_runs == 1
    assert document.counts.not_started_runs == compiled.run_count - 2
    assert document.partial is True and document.partial_reason == 'quality=partial'

    failed_row = next(item for item in document.runs if item.run_id == failed)
    assert failed_row.lifecycle == 'error'
    assert failed_row.ttft.availability == 'unavailable'
    assert failed_row.ttft.reason == 'run_error'
    assert failed_row.counts.planned_requests == 128

    skipped_row = next(item for item in document.runs if item.run_id == skipped)
    assert skipped_row.lifecycle == 'not_started'
    assert skipped_row.reason == 'run_not_started'
    assert skipped_row.availability == 'unavailable'
    assert any(skipped_row.run_key in group.run_keys for group in document.groups)


def test_runs_page_is_stable_across_pages_with_secondary_key(tmp_path):
    compiled = _compiled()
    base = compiled.runs[0]
    runs = [
        base.model_copy(update={'run_id': f'i1024-o128-r{index % 2}-l1-n1-{index}', 'order': index})
        for index in range(250)
    ]
    large = compiled.model_copy(update={'runs': runs, 'run_count': len(runs)})
    results = {}
    for index, value in ((3, 0.9), (11, 0.5), (150, 0.7)):
        results[runs[index].run_id] = [
            _record(position, ttft_seconds=value, latency_seconds=value + 1.0,
                    aiperf_output_sequence_tokens=5) for position in range(4)
        ]
    _write_plan(tmp_path, compiled=large, results=results, quality='partial')
    snapshot = _snapshot(tmp_path, task_id='t1')

    seen: list[str] = []
    ordered: list[str] = []
    for offset in (0, 100, 200):
        rows, total = snapshot.runs_page(offset=offset, limit=100, sort='ttft_p95', order='desc')
        assert total == 250
        seen.extend(row.run_key for row in rows)
        ordered.extend(row.run_id for row in rows)
    assert len(seen) == 250 and len(set(seen)) == 250
    assert ordered[:3] == [runs[3].run_id, runs[150].run_id, runs[11].run_id]
    assert ordered[3] == runs[0].run_id

    with pytest.raises(PerfReportError):
        snapshot.runs_page(sort='not_a_metric')
    with pytest.raises(PerfReportError):
        snapshot.runs_page(limit=501)


def test_request_pages_filter_sort_search_and_keep_the_full_total(tmp_path):
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    records = [
        _record(index, ttft_seconds=0.001 * (index + 1), latency_seconds=0.01 * (index + 1),
                aiperf_output_sequence_tokens=5, prompt_tokens=10 + index, output_tokens=5)
        for index in range(1200)
    ]
    records.append(_record(1200, status='error', status_reason='rate-limited', start_ns=1, end_ns=2))
    _write_plan(tmp_path, compiled=compiled, results={run_id: records})
    snapshot = _snapshot(tmp_path, task_id='t1')
    run = next(item for item in snapshot.document.runs if item.run_id == run_id)

    total, rows = snapshot.requests_page(run_key=run.run_key, offset=1200, limit=100)
    assert total == 1201 and len(rows) == 1
    assert rows[0].index == 1200

    total, rows = snapshot.requests_page(run_key=run.run_key, status='success', sort='ttft', order='desc')
    assert total == 1200
    assert rows[0].ttft_seconds == pytest.approx(1.2)

    total, rows = snapshot.requests_page(run_key=run.run_key, status='failed')
    assert total == 1 and rows[0].status_reason == 'rate-limited'

    total, rows = snapshot.requests_page(run_key=run.run_key, search='req-1199')
    assert total == 1 and rows[0].index == 1199

    total, rows = snapshot.requests_page(run_key=run.run_key, sort='prompt_tokens', order='desc', limit=5)
    assert total == 1201
    assert [row.prompt_tokens for row in rows] == [1209, 1208, 1207, 1206, 1205]

    slowest = snapshot.top_requests(run_key=run.run_key, metric='latency', limit=3)
    assert [row.index for row in slowest] == [1199, 1198, 1197]
    assert snapshot.top_requests(run_key=run.run_key, metric='prompt_tokens', limit=2)[0].prompt_tokens == 1209

    with pytest.raises(PerfReportError):
        snapshot.top_requests(run_key=run.run_key, metric='latency; DROP TABLE x')
    with pytest.raises(PerfReportError):
        snapshot.requests_page(run_key='missing:run')
    with pytest.raises(PerfReportError):
        snapshot.requests_page(run_key=run.run_key, sort='latency; DROP TABLE x')


def test_charts_cover_every_run_not_only_the_current_page(tmp_path):
    compiled = _compiled()
    results = {}
    for index, planned in enumerate(compiled.runs):
        if index < 3:
            results[planned.run_id] = [
                _record(position, ttft_seconds=0.1 * (index + 1), latency_seconds=1.0,
                        aiperf_output_sequence_tokens=5) for position in range(3)
            ]
    _write_plan(tmp_path, compiled=compiled, results=results, quality='partial')
    snapshot = _snapshot(tmp_path, task_id='t1')
    series = snapshot.charts(metric='ttft', statistic='p95')
    points = [point for item in series for point in item.points]
    assert len(points) == compiled.run_count
    assert sum(1 for point in points if point.value is not None) == 3
    assert {point.reason for point in points if point.value is None} == {'run_not_started'}

    with pytest.raises(PerfReportError):
        snapshot.charts(metric='ttft', statistic='median')

    by_run = {run.run_id: run for run in snapshot.document.runs}
    rpm_points = [point for item in snapshot.charts(metric='rpm') for point in item.points]
    assert len(rpm_points) == compiled.run_count
    for point in rpm_points:
        assert point.value == by_run[point.run_id].rates.requests_per_minute


def test_default_comparison_order_is_computed_over_the_whole_snapshot(tmp_path):
    document = json.loads((FIXTURES / 'matrix-osl-load.json').read_text())
    document['workload']['input_lengths'] = [1024]
    document['workload']['target_ratios'] = [0.0]
    document['load']['output_token_limits'] = [128, 256]
    document['load']['repetitions'] = 2
    document['load']['points'] = [{'concurrency': 1}, {'concurrency': 4}]
    compiled = compile_plan_text(json.dumps(document))
    _write_plan(tmp_path, compiled=compiled)
    snapshot = _snapshot(tmp_path, task_id='t1')

    expected = sorted(snapshot.document.runs, key=perf_reports._comparison_key)
    paged: list = []
    for offset in range(0, len(expected), 3):
        rows, total = snapshot.runs_page(offset=offset, limit=3, sort='comparison')
        assert total == len(expected)
        paged.extend(rows)
    assert [run.run_key for run in paged] == [run.run_key for run in expected]
    # The reader's order is scenario/size/load/repetition, not the executor's.
    executor_order = [run.run_id for run in sorted(snapshot.document.runs, key=lambda run: run.order)]
    assert [run.run_id for run in paged] != executor_order
    points = [
        (run.dimensions.output_token_limit, run.dimensions.concurrency, run.dimensions.repetition)
        for run in paged
    ]
    assert points == sorted(points)


def test_charts_split_output_limits_and_repetitions_without_joining_points(tmp_path):
    document = json.loads((FIXTURES / 'matrix-osl-load.json').read_text())
    document['workload']['input_lengths'] = [1024]
    document['workload']['target_ratios'] = [0.0]
    document['load']['output_token_limits'] = [128, 256]
    document['load']['repetitions'] = 2
    document['load']['points'] = [{'concurrency': 1}, {'concurrency': 4}]
    compiled = compile_plan_text(json.dumps(document))
    measured_id = compiled.runs[0].run_id
    _write_plan(
        tmp_path,
        compiled=compiled,
        results={measured_id: [
            _record(position, ttft_seconds=0.2, latency_seconds=1.0, aiperf_output_sequence_tokens=5)
            for position in range(2)
        ]},
        quality='partial',
    )
    snapshot = _snapshot(tmp_path, task_id='t1')
    series = snapshot.charts(metric='ttft', statistic='p95')

    assert len(series) == 4
    assert len({(item.facet, item.load_mode, item.repetition) for item in series}) == len(series)
    assert {item.repetition for item in series} == {0, 1}
    assert {item.facet_dimensions.output_token_limit for item in series} == {128, 256}
    for item in series:
        assert {point.dimensions.output_token_limit for point in item.points} == {
            item.facet_dimensions.output_token_limit
        }
        assert {point.dimensions.repetition for point in item.points} == {item.repetition}
        # Load points keep one order, so a chart never bends backwards.
        assert [point.dimensions.concurrency for point in item.points] == [1, 4]

    # A run without a measurement stays in its own series as an explicit gap.
    measured = [point for item in series for point in item.points if point.value is not None]
    gaps = [point for item in series for point in item.points if point.value is None]
    assert len(measured) == 1 and len(gaps) == 7
    assert {point.availability for point in gaps} == {'unavailable'}


def test_changing_a_run_generation_invalidates_the_snapshot(tmp_path):
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    _write_plan(tmp_path, compiled=compiled, results={
        run_id: [_record(0, ttft_seconds=0.4, latency_seconds=0.8, aiperf_output_sequence_tokens=4)]
    })
    first = _snapshot(tmp_path, task_id='t1')
    old_id = first.document.snapshot_id
    assert next(run for run in first.document.runs if run.run_id == run_id).ttft.mean == 0.4

    _write_run(
        tmp_path / 't1' / 'perf',
        run_id,
        [_record(0, ttft_seconds=0.9, latency_seconds=0.9, aiperf_output_sequence_tokens=4)],
        generation=hashlib.sha256(b'replaced').hexdigest(),
    )
    second = _snapshot(tmp_path, task_id='t1')
    assert second.document.snapshot_id != old_id
    assert next(run for run in second.document.runs if run.run_id == run_id).ttft.mean == 0.9


def _racing_plan_builder(races: int):
    """Wrap the plan builder so the source moves while the snapshot is read."""
    original = perf_reports._BUILDERS['plan']
    state = {'calls': 0, 'perf_root': None, 'run_id': None}

    def builder(root):
        snapshot = original(root)
        if state['calls'] < races:
            state['calls'] += 1
            _write_run(
                state['perf_root'],
                state['run_id'],
                [_record(0, ttft_seconds=0.9, latency_seconds=0.9, aiperf_output_sequence_tokens=4)],
                generation=hashlib.sha256(f'race-{state["calls"]}'.encode()).hexdigest(),
            )
        return snapshot

    return builder, state


def test_snapshot_rebuilds_once_when_the_source_moves_mid_read(tmp_path, monkeypatch):
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    perf_root = _write_plan(tmp_path, compiled=compiled, results={
        run_id: [_record(0, ttft_seconds=0.4, latency_seconds=0.8, aiperf_output_sequence_tokens=4)]
    })
    builder, state = _racing_plan_builder(races=1)
    state.update(perf_root=perf_root, run_id=run_id)
    monkeypatch.setitem(perf_reports._BUILDERS, 'plan', builder)

    snapshot = _snapshot(tmp_path, task_id='t1')
    assert state['calls'] == 1
    assert next(run for run in snapshot.document.runs if run.run_id == run_id).ttft.mean == 0.9
    assert snapshot.document.partial is False


def test_snapshot_is_partial_when_the_source_keeps_moving_during_the_retry(tmp_path, monkeypatch):
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    perf_root = _write_plan(tmp_path, compiled=compiled, results={
        run_id: [_record(0, ttft_seconds=0.4, latency_seconds=0.8, aiperf_output_sequence_tokens=4)]
    })
    builder, state = _racing_plan_builder(races=2)
    state.update(perf_root=perf_root, run_id=run_id)
    monkeypatch.setitem(perf_reports._BUILDERS, 'plan', builder)

    snapshot = _snapshot(tmp_path, task_id='t1')
    assert state['calls'] == 2
    assert snapshot.document.partial is True
    assert snapshot.document.partial_reason == 'source_changed_during_read'


def _write_cache_experiment(tmp_path: Path) -> Path:
    root = tmp_path / 'cache' / 'perf'
    root.mkdir(parents=True)
    plan_hash = 'a' * 64
    subrun = 'r050-l1024-n4'
    plan = {
        'schema_version': '1.0',
        'plan_hash': plan_hash,
        'configuration_hash': 'b' * 64,
        'tokenizer_id': 'tokenizer',
        'template_id': 'template',
        'subruns': [
            {
                'subrun_id': subrun,
                'order': 0,
                'target_ratio': 0.5,
                'load_mode': 'closed_loop',
                'load_point': {'concurrency': 4},
                'repetition': 0,
                'seed': 7,
                'namespace': 'ns',
                'request_count': 10,
                'primer_requests': 1,
                'output_token_limit': 128,
                'input_tokens': 1024,
            }
        ],
        'profiling_requests': 10,
        'primer_requests': 1,
    }
    state = {
        'schema_version': '1.0',
        'plan_hash': plan_hash,
        'status': 'completed',
        'completed_subruns': 1,
        'total_subruns': 1,
        'quality': 'complete',
        'subruns': [{'subrun_id': subrun, 'status': 'completed', 'quality': 'complete'}],
    }
    (root / 'experiment_plan.json').write_text(json.dumps(plan), encoding='utf-8')
    (root / 'experiment.json').write_text(json.dumps(state), encoding='utf-8')
    _write_run(root, subrun, [_record(0, ttft_seconds=0.3, latency_seconds=0.6, aiperf_output_sequence_tokens=4)])
    return root


def test_cache_experiment_archive_keeps_its_subrun_identity(tmp_path):
    _write_cache_experiment(tmp_path)
    snapshot = _snapshot(tmp_path, root_path=str(tmp_path), rel_path='cache/perf')
    document = snapshot.document
    assert document.source_kind == 'cache_experiment'
    assert document.runs[0].run_id == 'r050-l1024-n4'
    assert document.runs[0].dimensions.target_ratio == 0.5
    assert document.runs[0].dimensions.deployment_mode is None
    assert document.runs[0].ttft.mean == 0.3
    assert document.groups[0].run_count == 1


def test_single_archive_reports_one_row(tmp_path):
    root = tmp_path / 'single' / 'perf'
    root.mkdir(parents=True)
    manifest = {
        'schema_version': '1.0',
        'task_id': 'single',
        'model': 'served-model',
        'load_mode': 'closed_loop',
        'concurrency': 4,
        'planned_requests': 10,
        'output_token_limit': 128,
        'input_sha256': 'c' * 64,
        'status': 'completed',
        'artifact_status': 'available',
        'normalized': 'normalized',
        'quality': 'complete',
    }
    (root / 'single_run.json').write_text(json.dumps(manifest), encoding='utf-8')
    _write_run(root, 'single', [_record(0, ttft_seconds=0.2, latency_seconds=0.4, aiperf_output_sequence_tokens=4)])
    (root / 'normalized').symlink_to(root / 'runs' / 'single' / 'normalized')

    snapshot = _snapshot(tmp_path, root_path=str(tmp_path), rel_path='single/perf')
    document = snapshot.document
    assert document.source_kind == 'single'
    assert document.runs[0].run_id == 'single'
    assert document.runs[0].ttft.mean == 0.2
    assert document.capabilities.requests.availability == 'available'


def test_native_archive_keeps_reported_percentiles(tmp_path):
    run_dir = tmp_path / 'native' / 'perf' / 'parallel_4_number_1'
    run_dir.mkdir(parents=True)
    summary = BenchmarkSummary(
        time_taken=10.0,
        concurrency=4,
        total_requests=100,
        succeed_requests=98,
        failed_requests=2,
        request_throughput=9.8,
        avg_latency=0.5,
        avg_ttft=0.2,
        avg_tpot=0.02,
        output_token_throughput=200.0,
    )
    (run_dir / 'benchmark_summary.json').write_text(json.dumps(summary.to_dict()), encoding='utf-8')
    rows = [
        PercentileRow(percentile='50%', latency=0.4, ttft=0.18, tpot=0.02),
        PercentileRow(percentile='90%', latency=0.9, ttft=0.4, tpot=0.03),
        PercentileRow(percentile='95%', latency=1.1, ttft=0.5, tpot=0.04),
        PercentileRow(percentile='99%', latency=1.5, ttft=0.8, tpot=0.05),
    ]
    (run_dir / 'benchmark_percentile.json').write_text(
        json.dumps([row.to_dict() for row in rows]), encoding='utf-8'
    )
    (run_dir / 'benchmark_args.json').write_text(json.dumps({'model': 'served-model', 'max_tokens': 128}))

    snapshot = _snapshot(tmp_path, root_path=str(tmp_path), rel_path='native/perf')
    document = snapshot.document
    assert document.source_kind == 'native'
    run = document.runs[0]
    assert run.run_id == 'parallel_4_number_1'
    assert run.ttft.percentile_method == 'reported'
    assert (run.ttft.p90, run.ttft.p95, run.ttft.p99) == (0.4, 0.5, 0.8)
    assert run.tpot.percentile_method == 'reported'
    assert run.counts.success == 98 and run.counts.error == 2
    assert run.tokens.input_tokens is None
    assert run.rates.requests_per_minute == pytest.approx(9.8 * 60)
    assert document.capabilities.requests.availability == 'unsupported'


def test_api_endpoints_expose_the_snapshot(tmp_path):
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    _write_plan(tmp_path, compiled=compiled, results={
        run_id: [_record(index, ttft_seconds=0.1 * (index + 1), latency_seconds=1.0,
                         aiperf_output_sequence_tokens=5) for index in range(5)]
    })
    client = create_app(outputs=str(tmp_path)).test_client()
    location = {'root_path': str(tmp_path), 'path': 't1/perf'}

    summary = client.get('/api/v1/perf/reports', query_string=location)
    assert summary.status_code == 200
    body = summary.get_json()
    assert body['report']['source_kind'] == 'plan'
    assert body['total_runs'] == compiled.run_count

    runs = client.get('/api/v1/perf/reports/runs', query_string={**location, 'limit': 2, 'sort': 'ttft_mean'})
    assert runs.status_code == 200
    assert runs.get_json()['total'] == compiled.run_count

    run_key = next(
        row['run_key'] for row in client.get('/api/v1/perf/reports/runs', query_string=location).get_json()['rows']
        if row['run_id'] == run_id
    )
    requests = client.get(
        '/api/v1/perf/reports/requests',
        query_string={**location, 'run_key': run_key, 'sort': 'ttft', 'order': 'desc'},
    )
    assert requests.status_code == 200
    assert requests.get_json()['total'] == 5
    assert requests.get_json()['requests'][0]['ttft_seconds'] == pytest.approx(0.5)

    charts = client.get('/api/v1/perf/reports/charts', query_string={**location, 'statistic': 'p90'})
    assert charts.status_code == 200
    assert charts.get_json()['snapshot_id'] == body['report']['snapshot_id']

    assert client.get('/api/v1/perf/reports', query_string={**location, 'path': 'missing/perf'}).status_code == 404
    assert client.get(
        '/api/v1/perf/reports/runs', query_string={**location, 'limit': 501}
    ).status_code == 400
    assert client.get(
        '/api/v1/perf/reports/requests', query_string={**location, 'run_key': 'nope'}
    ).status_code == 404

    task_scoped = client.get('/api/v1/perf/reports', query_string={'task_id': 't1', 'root_path': str(tmp_path)})
    assert task_scoped.status_code == 200
    assert task_scoped.get_json()['report']['task_id'] == 't1'


def test_snapshot_pins_generation_across_endpoints(tmp_path):
    """Summary, requests, top requests and charts all describe one generation."""
    from evalscope.service.perf_exports.csv_export import write_requests_csv

    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    perf_root = _write_plan(tmp_path, compiled=compiled, results={
        run_id: [_record(0, ttft_seconds=0.4, latency_seconds=0.8, aiperf_output_sequence_tokens=4)]
    })
    first = _snapshot(tmp_path, task_id='t1')
    run = next(item for item in first.document.runs if item.run_id == run_id)
    pinned = json.loads((perf_root / 'runs' / run_id / 'normalized' / 'current.json').read_text())['generation']
    assert first.generations[run.run_key] == pinned

    _write_run(
        perf_root,
        run_id,
        [_record(0, ttft_seconds=0.9, latency_seconds=0.9, aiperf_output_sequence_tokens=4)],
        generation=hashlib.sha256(b'endpoint-move').hexdigest(),
    )

    total, rows = first.requests_page(run_key=run.run_key)
    assert total == 1 and rows[0].ttft_seconds == pytest.approx(0.4)
    assert first.top_requests(run_key=run.run_key)[0].ttft_seconds == pytest.approx(0.4)
    assert [row.ttft_seconds for row in first.iter_requests(run_key=run.run_key)] == [pytest.approx(0.4)]
    chart = next(
        point
        for series in first.charts(metric='ttft', statistic='mean')
        for point in series.points
        if point.run_key == run.run_key
    )
    assert chart.value == pytest.approx(0.4)

    target = tmp_path / 'pinned-requests.csv'
    write_requests_csv(target, first, run_key=run.run_key)
    exported = list(csv.DictReader(io.StringIO(target.read_text(encoding='utf-8-sig'))))
    assert exported[0]['ttft_ms'] == '400.0'


def test_snapshot_stays_pinned_beyond_the_snapshot_cache_limit(tmp_path):
    """A snapshot older than the cache window still serves its own generation."""
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    perf_root = _write_plan(tmp_path, compiled=compiled, results={
        run_id: [_record(0, ttft_seconds=0.4, latency_seconds=0.8, aiperf_output_sequence_tokens=4)]
    })
    first = _snapshot(tmp_path, task_id='t1')
    run_key = next(item.run_key for item in first.document.runs if item.run_id == run_id)
    for index in range(perf_reports.SNAPSHOT_CACHE_LIMIT + 2):
        _write_run(
            perf_root,
            run_id,
            [_record(0, ttft_seconds=0.5 + index / 100, latency_seconds=0.8, aiperf_output_sequence_tokens=4)],
            generation=hashlib.sha256(f'evict-{index}'.encode()).hexdigest(),
        )
        _snapshot(tmp_path, task_id='t1')
    total, rows = first.requests_page(run_key=run_key)
    assert total == 1 and rows[0].ttft_seconds == pytest.approx(0.4)


def test_api_pins_every_endpoint_to_the_requested_snapshot(tmp_path):
    compiled = _compiled()
    run_id = compiled.runs[0].run_id
    perf_root = _write_plan(tmp_path, compiled=compiled, results={
        run_id: [_record(0, ttft_seconds=0.4, latency_seconds=0.8, aiperf_output_sequence_tokens=4)]
    })
    client = create_app(outputs=str(tmp_path)).test_client()
    location = {'root_path': str(tmp_path), 'path': 't1/perf'}
    summary = client.get('/api/v1/perf/reports', query_string=location).get_json()
    snapshot_id = summary['report']['snapshot_id']
    run_key = next(
        row['run_key']
        for row in client.get('/api/v1/perf/reports/runs', query_string=location).get_json()['rows']
        if row['run_id'] == run_id
    )

    _write_run(
        perf_root,
        run_id,
        [_record(0, ttft_seconds=0.9, latency_seconds=0.9, aiperf_output_sequence_tokens=4)],
        generation=hashlib.sha256(b'api-move').hexdigest(),
    )

    for path in ('/api/v1/perf/reports', '/api/v1/perf/reports/runs', '/api/v1/perf/reports/charts'):
        response = client.get(path, query_string={**location, 'snapshot_id': snapshot_id})
        assert response.status_code == 409, (path, response.get_data(as_text=True))
        assert response.get_json()['snapshot_id'] != snapshot_id

    stale_requests = client.get(
        '/api/v1/perf/reports/requests', query_string={**location, 'run_key': run_key, 'snapshot_id': snapshot_id}
    )
    assert stale_requests.status_code == 409

    current = client.get('/api/v1/perf/reports', query_string=location).get_json()['report']['snapshot_id']
    pinned_requests = client.get(
        '/api/v1/perf/reports/requests', query_string={**location, 'run_key': run_key, 'snapshot_id': current}
    )
    assert pinned_requests.status_code == 200, pinned_requests.get_data(as_text=True)
    assert pinned_requests.get_json()['requests'][0]['ttft_seconds'] == pytest.approx(0.9)
