"""Archive readers that project every performance result into one report snapshot.

The service resolves *where* the data is; :mod:`evalscope.perf.results.report`
owns *what* a metric means. Keeping the two apart is what lets the Web API, the
HTML/PDF exports and the CSV files describe one immutable snapshot instead of
three independent recomputations with rounding drift between them.

Sources, all read in place and never rewritten:

* ``plan``: ``perf/plan/{compiled,normalized,manifest}.json`` plus
  ``perf/runs/<run_id>/normalized`` written by the matrix executor.
* ``cache_experiment``: ``perf/experiment_plan.json`` + ``perf/experiment.json``
  plus ``perf/runs/<subrun_id>/normalized``. Existing run ids and their order are
  preserved, so old archives keep their identity.
* ``single``: ``perf/single_run.json`` plus ``perf/normalized``.
* ``native``: ``parallel_*`` / ``rate_*`` run directories with
  ``benchmark_summary.json`` + ``benchmark_percentile.json``. Only reported
  values are shown, labelled ``percentile_method='reported'``;
  a native archive is never disguised as an AIPerf one.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Dict, Iterator, List, Literal, Optional, Tuple

from ..perf.plans.execution import PerfPlanManifest
from ..perf.plans.provenance import (
    EffectiveIdentity,
    read_compiled_plan,
    read_effective_identity,
    resolve_plan_dir,
)
from ..perf.results.aiperf_import import current_generation, load_aiperf_run
from ..perf.results.report import (
    STATISTICS_VERSION,
    CachePairing,
    ObservationWindow,
    RateMetrics,
    ReportCapabilities,
    ReportCapability,
    ReportChartPoint,
    ReportChartSeries,
    ReportDocument,
    ReportHeader,
    ReportNodeCacheRatio,
    ReportNodeHistogram,
    ReportNodeMetric,
    ReportNodeStatistic,
    ReportRequestRow,
    ReportRun,
    ReportRunCounts,
    ReportRunDimensions,
    ReportRunNodes,
    TokenRate,
    TokenSummary,
    aggregate_requests,
    build_counts,
    build_groups,
    dimensions_of,
    empty_run_metrics,
    iter_normalized_requests,
    reported_distribution,
    request_row,
    snapshot_id_for,
)
from ..perf.results.single_run import load_single_manifest
from ..utils import get_logger
from .utils.log import validate_task_id

logger = get_logger()

PERF_DIR_NAME = 'perf'
PAGE_LIMIT = 500
SNAPSHOT_CACHE_LIMIT = 6
REQUEST_INDEX_CACHE_LIMIT = 4

_NATIVE_PARALLEL = 'parallel_'
_NATIVE_RATE = 'rate_'

RUN_SORT_KEYS = (
    'comparison',
    'order',
    'input_tokens',
    'output_token_limit',
    'target_ratio',
    'concurrency',
    'rate',
    'ttft_mean',
    'ttft_p90',
    'ttft_p95',
    'ttft_p99',
    'tpot_mean',
    'tpot_p90',
    'tpot_p95',
    'tpot_p99',
    'latency_mean',
    'latency_p90',
    'latency_p95',
    'latency_p99',
    'requests_per_minute',
    'success_rate',
)

REQUEST_SORT_KEYS = (
    'index',
    'latency',
    'ttft',
    'tpot',
    'prompt_tokens',
    'output_tokens',
    'start_ns',
)

_REQUEST_SORT_COLUMNS = {
    'index': 'idx',
    'latency': 'latency',
    'ttft': 'ttft',
    'tpot': 'tpot',
    'prompt_tokens': 'prompt',
    'output_tokens': 'output',
    'start_ns': 'start_ns',
}

CHART_METRICS = ('ttft', 'tpot', 'latency', 'input_tps', 'output_tps', 'rpm')
CHART_STATISTICS = ('mean', 'p50', 'p90', 'p95', 'p99', 'min', 'max')
TOP_REQUEST_METRICS = ('latency', 'ttft', 'tpot', 'prompt_tokens', 'output_tokens')


class PerfReportError(Exception):
    """An archive or a query the report cannot answer, with an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _perf_root(outputs_root: str, task_id: Optional[str], root_path: Optional[str], rel_path: Optional[str]) -> Path:
    """Resolve the directory that owns the archive, refusing any path escape."""
    if task_id is not None:
        try:
            validate_task_id(task_id)
        except ValueError as error:
            raise PerfReportError(str(error), 400) from None
        root = Path(outputs_root) / task_id / PERF_DIR_NAME
        if root.is_dir():
            return root.resolve()
        legacy = Path(outputs_root) / task_id
        if (legacy / 'plan' / 'compiled.json').is_file():
            return legacy.resolve()
        raise PerfReportError('Performance archive not found', 404)
    if not rel_path:
        raise PerfReportError('A task_id or an archive path is required', 400)
    try:
        root = Path(root_path or outputs_root).resolve(strict=True)
        target = (root / rel_path).resolve(strict=True)
    except (OSError, ValueError):
        raise PerfReportError('Performance archive not found', 404) from None
    if target != root and root not in target.parents:
        raise PerfReportError('Archive path escapes the outputs root', 400)
    if not target.is_dir():
        raise PerfReportError('Performance archive not found', 404)
    return target


def _detect_kind(root: Path) -> Literal['plan', 'cache_experiment', 'single', 'native']:
    directory, _legacy = resolve_plan_dir(_plan_task_dir(root))
    if (directory / 'compiled.json').is_file():
        return 'plan'
    if (root / 'experiment_plan.json').is_file():
        return 'cache_experiment'
    if os.path.lexists(root / 'single_run.json'):
        return 'single'
    return 'native'


def _digest_files(*paths: Path) -> str:
    """One content stamp over every document the snapshot depends on."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode('utf-8'))
        try:
            data = path.read_bytes()
        except OSError:
            digest.update(b'absent')
            continue
        digest.update(hashlib.sha256(data).digest())
    digest.update(STATISTICS_VERSION.encode('ascii'))
    return digest.hexdigest()[:32]


def _run_generation(run_dir: Path) -> Path:
    return run_dir / 'normalized' / 'current.json'


def _run_sources(run_dir: Path) -> List[Path]:
    """Every persisted file a run's report row depends on, node analysis included."""
    return [_run_generation(run_dir), run_dir / 'node_analysis.json']


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@dataclass
class ReportSnapshot:
    """A fixed document plus the per-run generations its request pages read."""

    document: ReportDocument
    normalized_dirs: Dict[str, str] = field(default_factory=dict)
    generations: Dict[str, str] = field(default_factory=dict)
    stamp: str = ''

    def runs_page(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        group: Optional[str] = None,
        sort: str = 'comparison',
        order: Literal['asc', 'desc'] = 'asc',
    ) -> Tuple[List[ReportRun], int]:
        _check_page(offset, limit)
        rows = [run for run in self.document.runs if group is None or _group_of(run) == group]
        return _sort_runs(rows, sort, order)[offset : offset + limit], len(rows)

    def requests_page(
        self,
        *,
        run_key: str,
        offset: int = 0,
        limit: int = 100,
        status: Optional[str] = None,
        phase: Optional[str] = 'profiling',
        search: Optional[str] = None,
        sort: str = 'index',
        order: Literal['asc', 'desc'] = 'asc',
    ) -> Tuple[int, List[ReportRequestRow]]:
        _check_page(offset, limit)
        if sort not in REQUEST_SORT_KEYS:
            raise PerfReportError(f'Unsupported request sort key: {sort}', 400)
        if status not in (None, 'success', 'failed'):
            raise PerfReportError('Unsupported request status filter', 400)
        if phase not in (None, 'warmup', 'profiling'):
            raise PerfReportError('Unsupported request phase filter', 400)
        normalized = self.normalized_dirs.get(run_key)
        if normalized is None:
            raise PerfReportError('Request details are unavailable for this run', 404)
        return _query_request_index(
            snapshot_id=self.document.snapshot_id,
            run_key=run_key,
            normalized_dir=normalized,
            generation=self.generations.get(run_key),
            offset=offset,
            limit=limit,
            status=status,
            phase=phase,
            search=search,
            sort=sort,
            order=order,
        )

    def selected_runs(self, *, group: Optional[str] = None) -> List[ReportRun]:
        """Every run of the snapshot, in plan order, optionally one group."""
        return [run for run in self.document.runs if group is None or _group_of(run) == group]

    def iter_requests(
        self,
        *,
        run_key: str,
        status: Optional[str] = None,
        phase: Optional[str] = 'profiling',
        search: Optional[str] = None,
        sort: str = 'index',
        order: Literal['asc', 'desc'] = 'asc',
    ) -> Iterator[ReportRequestRow]:
        """Stream every matching request of one run in bounded batches.

        Exports must never be truncated to a page, so this walks the same
        server-side index the Web table pages over until the run is exhausted.
        """
        offset = 0
        while True:
            total, rows = self.requests_page(
                run_key=run_key,
                offset=offset,
                limit=PAGE_LIMIT,
                status=status,
                phase=phase,
                search=search,
                sort=sort,
                order=order,
            )
            for row in rows:
                yield row
            offset += len(rows)
            if not rows or offset >= total:
                return

    def top_requests(
        self,
        *,
        run_key: str,
        metric: str = 'latency',
        limit: int = 10,
        status: Optional[str] = 'success',
        phase: Optional[str] = 'profiling',
    ) -> List[ReportRequestRow]:
        """Requests ranked over the whole run, never over one page of it."""
        if metric not in TOP_REQUEST_METRICS:
            raise PerfReportError(f'Unsupported top-request metric: {metric}', 400)
        _check_page(0, limit)
        _total, rows = self.requests_page(
            run_key=run_key,
            offset=0,
            limit=limit,
            status=status,
            phase=phase,
            sort=metric,
            order='desc',
        )
        return rows

    def charts(self, *, metric: str = 'ttft', statistic: str = 'p95') -> List[ReportChartSeries]:
        if metric not in CHART_METRICS:
            raise PerfReportError('Unsupported chart metric', 400)
        if statistic not in CHART_STATISTICS:
            raise PerfReportError('Unsupported chart statistic', 400)
        series: Dict[Tuple[str, Optional[str], Optional[int]], ReportChartSeries] = {}
        for run in self.document.runs:
            facet = _facet_of(run)
            key = (facet, run.dimensions.load_mode, run.dimensions.repetition)
            item = series.get(key)
            if item is None:
                item = ReportChartSeries(
                    facet=facet,
                    facet_dimensions=run.dimensions.model_copy(
                        update={'load_mode': None, 'concurrency': None, 'rate': None, 'repetition': None}
                    ),
                    load_mode=run.dimensions.load_mode,
                    repetition=run.dimensions.repetition,
                )
                series[key] = item
            value: Optional[float]
            reason: Optional[str]
            if metric == 'rpm':
                value = run.rates.requests_per_minute
                reason = run.rates.reason
            elif metric in ('input_tps', 'output_tps'):
                direction = run.rates.input if metric == 'input_tps' else run.rates.output
                value = direction.tokens_per_second
                reason = direction.reason
            else:
                distribution = {'ttft': run.ttft, 'tpot': run.tpot, 'latency': run.e2e}[metric]
                value = getattr(distribution, statistic)
                reason = distribution.reason
            item.points.append(
                ReportChartPoint(
                    run_key=run.run_key,
                    run_id=run.run_id,
                    order=run.order,
                    dimensions=run.dimensions,
                    value=value,
                    availability='available' if value is not None else 'unavailable',
                    reason=reason if value is None else None,
                )
            )
        for item in series.values():
            # One flat comparison order for every chart: load point, then
            # repetition, so a repeated measurement never bends the curve.
            item.points.sort(
                key=lambda point: (
                    (point.dimensions.concurrency or 0),
                    (point.dimensions.rate or 0.0),
                    point.dimensions.repetition or 0,
                    point.order,
                )
            )
        return [
            series[key]
            for key in sorted(series, key=lambda key: (key[0], key[1] or '', -1 if key[2] is None else key[2]))
        ]

    def header(self) -> ReportHeader:
        return ReportHeader.model_validate(self.document.model_dump(exclude={'runs'}))

    #: File name an export stores its pinned snapshot under.
    PIN_NAME = 'snapshot.json'

    def persist(self, directory: Path) -> Path:
        """Write the pinned snapshot beside an export so a queued render
        survives the source archive being replaced."""
        payload = {
            'version': STATISTICS_VERSION,
            'document': self.document.model_dump(mode='json'),
            'normalized_dirs': self.normalized_dirs,
            'generations': self.generations,
            'stamp': self.stamp,
        }
        path = directory / self.PIN_NAME
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(payload), encoding='utf-8')
        os.replace(temporary, path)
        return path

    @classmethod
    def restore(cls, path: Path) -> 'ReportSnapshot':
        """Rebuild a snapshot exactly as the export was queued, not as the source is now."""
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or payload.get('version') != STATISTICS_VERSION:
            raise ValueError('pinned snapshot version mismatch')
        document = ReportDocument.model_validate(payload['document'])
        normalized_dirs = payload.get('normalized_dirs') or {}
        generations = payload.get('generations') or {}
        if not isinstance(normalized_dirs, dict) or not isinstance(generations, dict):
            raise ValueError('pinned snapshot is malformed')
        return cls(
            document=document,
            normalized_dirs=dict(normalized_dirs),
            generations=dict(generations),
            stamp=str(payload.get('stamp') or ''),
        )


def _check_page(offset: int, limit: int) -> None:
    if type(offset) is not int or not 0 <= offset <= 2**31 - 1:
        raise PerfReportError('Invalid offset', 400)
    if type(limit) is not int or not 1 <= limit <= PAGE_LIMIT:
        raise PerfReportError(f'limit must be between 1 and {PAGE_LIMIT}', 400)


def _group_of(run: ReportRun) -> str:
    from ..perf.results.report import group_key

    return group_key(run.dimensions)


def _facet_of(run: ReportRun) -> str:
    dimensions = run.dimensions
    return (
        f'deployment={dimensions.deployment_mode}|workload={dimensions.workload}'
        f'|structure={dimensions.message_structure}|isl={dimensions.input_tokens}'
        f'|osl={dimensions.output_token_limit}|ratio={dimensions.target_ratio}'
    )


def _run_sort_value(run: ReportRun, key: str) -> Optional[float]:
    if key in ('order', 'input_tokens', 'output_token_limit', 'target_ratio', 'concurrency', 'rate'):
        if key == 'order':
            return float(run.order)
        value = getattr(run.dimensions, key)
        return float(value) if value is not None else None
    if key == 'requests_per_minute':
        return run.rates.requests_per_minute
    if key == 'success_rate':
        return run.counts.success / run.counts.total if run.counts.total else None
    metric, statistic = key.rsplit('_', 1)
    distribution = {'ttft': run.ttft, 'tpot': run.tpot, 'latency': run.e2e}.get(metric)
    if distribution is None:
        return None
    return getattr(distribution, statistic)


def _comparison_key(run: ReportRun) -> tuple:
    """The reader's default order: scenario, sizes, load point, repetition."""
    dimensions = run.dimensions
    return (
        dimensions.deployment_mode or '',
        dimensions.workload or '',
        dimensions.message_structure or '',
        dimensions.case_id or '',
        dimensions.input_tokens if dimensions.input_tokens is not None else -1,
        dimensions.output_token_limit if dimensions.output_token_limit is not None else -1,
        dimensions.target_ratio if dimensions.target_ratio is not None else -1.0,
        dimensions.load_mode or '',
        dimensions.concurrency if dimensions.concurrency is not None else -1,
        dimensions.rate if dimensions.rate is not None else -1.0,
        dimensions.repetition if dimensions.repetition is not None else -1,
        run.order,
        run.run_key,
    )


def _sort_runs(runs: List[ReportRun], sort: str, order: Literal['asc', 'desc']) -> List[ReportRun]:
    if sort not in RUN_SORT_KEYS:
        raise PerfReportError(f'Unsupported sort key: {sort}', 400)
    if order not in ('asc', 'desc'):
        raise PerfReportError('Unsupported sort order', 400)
    if sort == 'comparison':
        # Comparison order is computed over the whole snapshot before any page
        # is cut, so consecutive pages never interleave.
        return sorted(runs, key=_comparison_key, reverse=order == 'desc')
    # Missing values stay last in both directions; ties fall back to the stable
    # run order and run key so two pages cannot overlap or skip a row.
    present = [run for run in runs if _run_sort_value(run, sort) is not None]
    missing = [run for run in runs if _run_sort_value(run, sort) is None]
    present.sort(key=lambda run: (run.order, run.run_key))
    present.sort(key=lambda run: _run_sort_value(run, sort), reverse=order == 'desc')  # type: ignore[arg-type,return-value]
    missing.sort(key=lambda run: (run.order, run.run_key))
    return present + missing


# ---------------------------------------------------------------------------
# Request index
# ---------------------------------------------------------------------------


_REQUEST_INDEXES: 'OrderedDict[Tuple[str, str, str], Tuple[str, sqlite3.Connection]]' = OrderedDict()
# Re-entrant: a query holds this guard while reading an index that was itself
# built (and cached) under the same guard. Flask serves each request on a
# worker thread and exports read the same cache from their own threads, so one
# SQLite connection is legitimately used by several threads; the guard keeps
# those uses serialized and keeps eviction from closing a connection mid-query.
_REQUEST_INDEX_LOCK = RLock()


def _request_index(
    snapshot_id: str, run_key: str, normalized_dir: str, generation: Optional[str]
) -> sqlite3.Connection:
    key = (snapshot_id, run_key, generation or '')
    with _REQUEST_INDEX_LOCK:
        cached = _REQUEST_INDEXES.get(key)
        if cached is not None:
            _REQUEST_INDEXES.move_to_end(key)
            return cached[1]
        directory = tempfile.mkdtemp(prefix='perf-report-requests-')
        database = sqlite3.connect(os.path.join(directory, 'index.db'), check_same_thread=False)
        database.execute(
            'CREATE TABLE requests (idx INTEGER PRIMARY KEY, phase TEXT, status TEXT, latency REAL, ttft REAL, '
            'tpot REAL, prompt INTEGER, output INTEGER, start_ns INTEGER, search TEXT, row_json TEXT)'
        )
        batch = []
        for record in iter_normalized_requests(normalized_dir, phase=None, generation=generation):
            row = request_row(record)
            batch.append(
                (
                    record.index,
                    record.phase,
                    record.status,
                    row.latency_seconds,
                    row.ttft_seconds,
                    row.tpot_seconds,
                    record.prompt_tokens,
                    record.output_tokens,
                    record.start_ns,
                    ' '.join(filter(None, [record.request_id, record.status_reason])).lower(),
                    row.model_dump_json(),
                )
            )
            if len(batch) >= 1000:
                database.executemany('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?)', batch)
                batch.clear()
        if batch:
            database.executemany('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?)', batch)
        database.execute('CREATE INDEX requests_status ON requests (status)')
        database.commit()
        _REQUEST_INDEXES[key] = (directory, database)
        while len(_REQUEST_INDEXES) > REQUEST_INDEX_CACHE_LIMIT:
            _, (old_directory, old_database) = _REQUEST_INDEXES.popitem(last=False)
            old_database.close()
            shutil.rmtree(old_directory, ignore_errors=True)
        return database


def _query_request_index(
    *,
    snapshot_id: str,
    run_key: str,
    normalized_dir: str,
    offset: int,
    limit: int,
    status: Optional[str],
    phase: Optional[str],
    search: Optional[str],
    sort: str,
    order: Literal['asc', 'desc'],
    generation: Optional[str] = None,
) -> Tuple[int, List[ReportRequestRow]]:
    with _REQUEST_INDEX_LOCK:
        try:
            database = _request_index(snapshot_id, run_key, normalized_dir, generation)
            conditions: List[str] = []
            parameters: List[object] = []
            if phase is not None:
                conditions.append('phase = ?')
                parameters.append(phase)
            if status == 'success':
                conditions.append("status = 'success'")
            elif status == 'failed':
                conditions.append("status != 'success'")
            if search:
                conditions.append('search LIKE ?')
                parameters.append(f'%{search.strip().lower()}%')
            clause = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
            total = database.execute('SELECT COUNT(*) FROM requests' + clause, parameters).fetchone()[0]
            direction = 'DESC' if order == 'desc' else 'ASC'
            column = _REQUEST_SORT_COLUMNS[sort]
            rows = database.execute(
                f'SELECT row_json FROM requests{clause} '
                f'ORDER BY ({column} IS NULL), {column} {direction}, idx ASC LIMIT ? OFFSET ?',
                [*parameters, limit, offset],
            ).fetchall()
        except (OSError, ValueError, sqlite3.Error) as error:
            logger.warning(f'Cannot read requests for {run_key}: {error}')
            raise PerfReportError('Request details are unavailable for this run', 404) from None
    return int(total), [ReportRequestRow.model_validate_json(row[0]) for row in rows]


# ---------------------------------------------------------------------------
# Plan archives
# ---------------------------------------------------------------------------


def _run_lifecycle(status: str) -> Literal['not_started', 'running', 'completed', 'cancelled', 'error', 'unknown']:
    mapping = {
        'not_started': 'not_started',
        'running': 'running',
        'completed': 'completed',
        'cancelled': 'cancelled',
        'stopped': 'cancelled',
        'error': 'error',
    }
    return mapping.get(status, 'unknown')


def _read_manifest(directory: Path) -> Optional[PerfPlanManifest]:
    path = directory / 'manifest.json'
    if not path.is_file():
        return None
    try:
        return PerfPlanManifest.model_validate_json(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _aggregate_normalized(normalized_dir: Path, generation: Optional[str]) -> Dict[str, object]:
    """Read one pinned run generation, or explain why it is missing."""
    if generation is None or not (normalized_dir / 'current.json').is_file():
        return empty_run_metrics('normalized_result_missing')
    try:
        # The profiling phase is the authoritative statistics window. A run
        # that has requests but no reusable ``run.json`` still aggregates; it
        # then reports the request extent and labels the window non-authoritative.
        try:
            run = load_aiperf_run(str(normalized_dir), generation=generation)
            window = (
                (run.profiling_start_ns, run.profiling_end_ns)
                if run.profiling_start_ns is not None and run.profiling_end_ns is not None
                else None
            )
        except (OSError, ValueError, sqlite3.Error):
            window = None
        aggregation = aggregate_requests(
            iter_normalized_requests(str(normalized_dir), phase='profiling', generation=generation),
            phase_window=window,
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        logger.warning(f'Cannot aggregate {normalized_dir}: {error}')
        return empty_run_metrics('invalid_normalized_result')
    return {
        'ttft': aggregation.ttft,
        'tpot': aggregation.tpot,
        'e2e': aggregation.e2e,
        'tokens': aggregation.tokens,
        'rates': aggregation.rates,
        'observation': aggregation.observation,
        'observed_concurrency': aggregation.observed_concurrency,
        'observed_concurrency_peak': aggregation.observed_concurrency_peak,
        'availability': 'available',
        'reason': None,
        'request_counts': aggregation.counts,
    }


def _pinned_generation(normalized_dir: Path) -> Optional[str]:
    """The generation a snapshot pins for one run, or ``None`` when absent."""
    try:
        return current_generation(str(normalized_dir))
    except (OSError, ValueError):
        return None


def _node_observation(run_dir: Path) -> Optional[ReportRunNodes]:
    """Project an already-persisted ``node_analysis.json`` into the report.

    The report never runs node analysis itself and never invents a telemetry
    source: it only surfaces the analysis a completed run already wrote.
    """
    from ..perf.integrations.aiperf.metrics import NodeMetricsAnalysis

    path = run_dir / 'node_analysis.json'
    if not path.is_file():
        return None
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(run_dir.resolve(strict=True)) or not resolved.is_file():
            return None
        if resolved.stat().st_size > 16 * 1024 * 1024:
            return None
        analysis = NodeMetricsAnalysis.model_validate_json(resolved.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        logger.warning(f'Cannot read node analysis at {path}: {error}')
        return None
    availability = analysis.availability
    metrics: List[ReportNodeMetric] = []
    for row in analysis.series:
        statistic = None
        if row.statistic is not None:
            statistic = ReportNodeStatistic(
                availability=row.statistic.availability,
                value=row.statistic.value,
                sample_peak=row.statistic.sample_peak,
                coverage=row.statistic.coverage,
                sample_count=row.statistic.sample_count,
                reason=row.statistic.reason,
                boundary=row.statistic.boundary,
            )
        histogram = None
        if row.histogram is not None:
            histogram = ReportNodeHistogram(
                availability=row.histogram.availability,
                metric_key=row.histogram.metric_key,
                unit=row.histogram.unit,
                count=row.histogram.count,
                mean=row.histogram.mean,
                p50=row.histogram.p50,
                p95=row.histogram.p95,
                p99=row.histogram.p99,
                boundary=row.histogram.boundary,
                reason=row.histogram.reason,
            )
        metrics.append(
            ReportNodeMetric(
                node_id=row.node_id,
                role=row.role,
                engine=row.engine,
                labels=dict(row.labels),
                metric_name=row.metric.name,
                metric_unit=row.metric.unit,
                metric_kind=row.metric.kind,
                window_source=row.window_source,
                statistic=statistic,
                histogram=histogram,
                reason=row.reason,
            )
        )
    return ReportRunNodes(
        availability=availability,  # type: ignore[arg-type]
        reason=analysis.reason,
        quality_reasons=list(analysis.quality_reasons),
        window_source=analysis.window_source,
        start_ns=analysis.window.start_ns if analysis.window is not None else None,
        end_ns=analysis.window.end_ns if analysis.window is not None else None,
        timed_requests=analysis.timed_requests,
        total_requests=analysis.total_requests,
        max_gap_ns=analysis.max_gap_ns,
        metrics=metrics,
        cache_ratios=[
            ReportNodeCacheRatio(
                node_id=ratio.node_id,
                role=ratio.role,
                availability=ratio.availability,
                hit_tokens=ratio.hit_tokens,
                lookup_tokens=ratio.lookup_tokens,
                ratio=ratio.ratio,
                reason=ratio.reason,
                semantics=ratio.semantics,
            )
            for ratio in analysis.cache_ratios
        ],
    )


def _plan_task_dir(root: Path) -> Path:
    """Normalize a plan path to the task directory that owns ``plan/``."""
    if root.name == 'plan' and (root / 'compiled.json').is_file():
        root = root.parent
    return root if root.name != PERF_DIR_NAME else root.parent


def _plan_snapshot(root: Path) -> ReportSnapshot:
    task_dir = _plan_task_dir(root)
    directory, _legacy = resolve_plan_dir(task_dir)
    try:
        compiled = read_compiled_plan(directory.parent)
    except ValueError as error:
        raise PerfReportError(str(error), 400) from None
    manifest = _read_manifest(directory)
    effective: Optional[EffectiveIdentity] = read_effective_identity(directory.parent)
    document = compiled.normalized
    identity = (manifest.plan_hash if manifest else None) or compiled.plan_hash or f'path:{task_dir}'
    manifest_rows = {row.run_id: row for row in (manifest.runs if manifest else [])}
    deployment = document.deployment
    runs: List[ReportRun] = []
    normalized_dirs: Dict[str, str] = {}
    generations: Dict[str, str] = {}
    stamp_paths: List[Path] = [
        directory / 'compiled.json',
        directory / 'normalized.json',
        directory / 'manifest.json',
    ]
    for planned in compiled.runs:
        state_row = manifest_rows.get(planned.run_id)
        lifecycle = _run_lifecycle(state_row.status) if state_row is not None else 'not_started'
        reason = state_row.reason if state_row is not None else 'plan_manifest_missing'
        run_key = f'{identity}:{planned.run_id}'
        run_dir = directory.parent / 'runs' / planned.run_id
        normalized_dir = run_dir / 'normalized'
        stamp_paths.extend(_run_sources(run_dir))
        if lifecycle in ('not_started', 'running'):
            metrics = empty_run_metrics(reason or f'run_{lifecycle}')
        else:
            generation = _pinned_generation(normalized_dir)
            metrics = _aggregate_normalized(normalized_dir, generation)
            if metrics['reason'] == 'normalized_result_missing' and lifecycle != 'completed':
                metrics = empty_run_metrics(reason or f'run_{lifecycle}')
            if metrics['availability'] == 'available':
                normalized_dirs[run_key] = str(normalized_dir)
                if generation is not None:
                    generations[run_key] = generation
        metrics.pop('nodes', None)
        nodes = _node_observation(run_dir)
        request_counts = metrics.pop('request_counts', None)
        counts = ReportRunCounts(
            planned_requests=planned.request_count,
            primer_requests=planned.primer_requests,
            total=request_counts.total if request_counts else 0,
            success=request_counts.success if request_counts else 0,
            error=request_counts.error if request_counts else 0,
            cancelled=request_counts.cancelled if request_counts else 0,
            incomplete=request_counts.incomplete if request_counts else 0,
        )
        dims = _planned_dimensions(
            planned,
            deployment_mode=deployment.mode,
            workload=compiled.workload_semantics,
        )
        runs.append(
            ReportRun(
                run_key=run_key,
                run_id=planned.run_id,
                order=planned.order,
                archive_identity=identity,
                dimensions=dims,
                lifecycle=lifecycle,
                quality=state_row.quality if state_row is not None else None,
                counts=counts,
                quality_reasons=[],
                nodes=nodes,
                **metrics,  # type: ignore[arg-type]
            )
        )
    quality = manifest.quality if manifest else None
    header = dict(
        source_kind='plan',
        source_identity=identity,
        task_id=manifest.archive.get('task_id') if manifest and manifest.archive else None,
        model=document.connection.model,
        status=manifest.status if manifest else None,
        quality=quality,
        quality_reasons=list(manifest.quality_reasons) if manifest else [],
        phase=manifest.phase if manifest else None,
        plan_hash=compiled.plan_hash,
        normalized_hash=compiled.normalized_hash,
        compiler_version=compiled.compiler_version,
        contract_version=compiled.contract_version,
        workload_semantics=compiled.workload_semantics,
        deployment_mode=deployment.mode,
        deployment_evidence=effective.deployment_evidence if effective else deployment.evidence,
        declared_max_model_len=deployment.declared_max_model_len,
        partial=bool(quality and quality != 'complete'),
        partial_reason=('quality=' + quality) if quality and quality != 'complete' else None,
    )
    return _finish_snapshot(runs, header, normalized_dirs, stamp_paths, identity, generations)


def _planned_dimensions(planned, *, deployment_mode: Optional[str], workload: Optional[str]) -> ReportRunDimensions:
    return dimensions_of(
        deployment_mode=deployment_mode,
        workload=workload,
        message_structure=planned.message_structure,
        input_tokens=planned.input_tokens,
        output_token_limit=planned.output_token_limit,
        target_ratio=planned.target_ratio,
        load_mode=planned.load_mode,
        concurrency=planned.concurrency,
        rate=planned.rate,
        repetition=planned.repetition,
        case_id=planned.case_id,
    )


# ---------------------------------------------------------------------------
# Cache experiment archives
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Optional[dict]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _cache_snapshot(root: Path) -> ReportSnapshot:
    from ..perf.experiments.planner import CacheExperimentPlan
    from .api_models.perf import CacheExperimentState

    plan_value = _read_json(root / 'experiment_plan.json')
    if plan_value is None:
        raise PerfReportError('Experiment plan is unreadable', 400)
    try:
        plan = CacheExperimentPlan.model_validate(plan_value)
    except ValueError as error:
        raise PerfReportError(f'Invalid experiment plan: {error}', 400) from None
    state_value = _read_json(root / 'experiment.json') or {}
    try:
        state = CacheExperimentState.model_validate(state_value)
    except ValueError:
        state = None
    state_rows = {row.subrun_id: row for row in (state.subruns if state else [])}
    identity = plan.plan_hash or f'path:{root}'
    configuration = plan.configuration
    runs: List[ReportRun] = []
    normalized_dirs: Dict[str, str] = {}
    generations: Dict[str, str] = {}
    stamp_paths = [root / 'experiment_plan.json', root / 'experiment.json']
    for item in plan.subruns:
        state_row = state_rows.get(item.subrun_id)
        lifecycle = _run_lifecycle(state_row.status) if state_row is not None else 'not_started'
        reason = f'run_{lifecycle}'
        run_key = f'{identity}:{item.subrun_id}'
        run_dir = root / 'runs' / item.subrun_id
        normalized_dir = run_dir / 'normalized'
        stamp_paths.extend(_run_sources(run_dir))
        if lifecycle in ('not_started', 'running'):
            metrics = empty_run_metrics(reason)
        else:
            generation = _pinned_generation(normalized_dir)
            metrics = _aggregate_normalized(normalized_dir, generation)
            if metrics['reason'] == 'normalized_result_missing' and lifecycle != 'completed':
                metrics = empty_run_metrics(reason)
            if metrics['availability'] == 'available':
                normalized_dirs[run_key] = str(normalized_dir)
                if generation is not None:
                    generations[run_key] = generation
        metrics.pop('nodes', None)
        nodes = _node_observation(run_dir)
        request_counts = metrics.pop('request_counts', None)
        counts = ReportRunCounts(
            planned_requests=item.request_count,
            primer_requests=item.primer_requests,
            total=request_counts.total if request_counts else 0,
            success=request_counts.success if request_counts else 0,
            error=request_counts.error if request_counts else 0,
            cancelled=request_counts.cancelled if request_counts else 0,
            incomplete=request_counts.incomplete if request_counts else 0,
        )
        point = item.load_point
        runs.append(
            ReportRun(
                run_key=run_key,
                run_id=item.subrun_id,
                order=item.order,
                archive_identity=identity,
                dimensions=dimensions_of(
                    deployment_mode=configuration.deployment_mode if configuration else None,
                    workload='token_prefix_ratio',
                    input_tokens=item.input_tokens,
                    output_token_limit=item.output_token_limit,
                    target_ratio=item.target_ratio,
                    load_mode=item.load_mode,
                    concurrency=getattr(point, 'concurrency', None),
                    rate=getattr(point, 'rate', None),
                    repetition=item.repetition,
                ),
                lifecycle=lifecycle,
                quality=state_row.quality if state_row is not None else None,
                counts=counts,
                nodes=nodes,
                **metrics,  # type: ignore[arg-type]
            )
        )
    quality = state.quality if state else None
    header = dict(
        source_kind='cache_experiment',
        source_identity=identity,
        task_id=configuration.task_id if configuration else None,
        model=configuration.model if configuration else None,
        status=state.status if state else None,
        quality=quality,
        quality_reasons=[],
        partial=bool(state is None or (quality and quality != 'complete')),
        partial_reason='experiment_state_missing'
        if state is None
        else (('quality=' + quality) if quality and quality != 'complete' else None),
        plan_hash=plan.plan_hash,
        contract_version=plan.schema_version,
    )
    return _finish_snapshot(runs, header, normalized_dirs, stamp_paths, identity, generations)


# ---------------------------------------------------------------------------
# Single-run archive
# ---------------------------------------------------------------------------


def _single_snapshot(root: Path) -> ReportSnapshot:
    try:
        manifest = load_single_manifest(str(root))
    except (OSError, ValueError) as error:
        raise PerfReportError(f'Invalid single-run archive: {error}', 400) from None
    normalized_dir = root / 'normalized'
    identity = f'path:{root}'
    generation = _pinned_generation(normalized_dir)
    metrics = _aggregate_normalized(normalized_dir, generation)
    normalized_dirs: Dict[str, str] = {}
    generations: Dict[str, str] = {}
    if metrics['availability'] == 'available':
        normalized_dirs[f'{identity}:single'] = str(normalized_dir)
        if generation is not None:
            generations[f'{identity}:single'] = generation
    metrics.pop('nodes', None)
    nodes = _node_observation(root)
    request_counts = metrics.pop('request_counts', None)
    counts = ReportRunCounts(
        planned_requests=manifest.planned_requests,
        primer_requests=0,
        total=request_counts.total if request_counts else 0,
        success=request_counts.success if request_counts else 0,
        error=request_counts.error if request_counts else 0,
        cancelled=request_counts.cancelled if request_counts else 0,
        incomplete=request_counts.incomplete if request_counts else 0,
    )
    run = ReportRun(
        run_key=f'{identity}:single',
        run_id='single',
        order=0,
        archive_identity=identity,
        dimensions=dimensions_of(
            load_mode=manifest.load_mode,
            concurrency=manifest.concurrency,
            rate=manifest.rate,
            output_token_limit=manifest.output_token_limit,
        ),
        lifecycle=_run_lifecycle(manifest.status),
        quality=manifest.quality,
        counts=counts,
        quality_reasons=list(manifest.quality_reasons),
        nodes=nodes,
        **metrics,  # type: ignore[arg-type]
    )
    header = dict(
        source_kind='single',
        source_identity=identity,
        task_id=manifest.task_id,
        model=manifest.model,
        status=manifest.status,
        quality=manifest.quality,
        quality_reasons=list(manifest.quality_reasons),
        partial=bool(manifest.quality and manifest.quality != 'complete'),
        partial_reason=('quality=' + manifest.quality) if manifest.quality and manifest.quality != 'complete' else None,
    )
    return _finish_snapshot(
        [run], header, normalized_dirs, [root / 'single_run.json', *_run_sources(root)], identity, generations
    )


# ---------------------------------------------------------------------------
# Native archives
# ---------------------------------------------------------------------------


def _finite(value: Optional[float]) -> Optional[float]:
    """Native summaries use ``-1`` for 'not applicable'; keep that out of the report."""
    if value is None or not math.isfinite(value) or value < 0:
        return None
    return value


def _percentile_rows(percentiles) -> Dict[str, object]:
    return {row.percentile: row for row in (percentiles.rows if percentiles else [])}


def _reported_value(rows: Dict[str, object], label: str, field_name: str) -> Optional[float]:
    row = rows.get(label)
    if row is None:
        return None
    return _finite(getattr(row, field_name, None))


def _native_snapshot(root: Path) -> ReportSnapshot:
    from ..perf.utils.report.perf_data import RunLoader

    try:
        loaded = RunLoader.load_all(str(root), with_requests=False)
    except (OSError, ValueError) as error:
        raise PerfReportError(f'Unreadable native archive: {error}', 400) from None
    if not loaded and (root / 'benchmark_summary.json').is_file():
        single = RunLoader.load_run(str(root), root.name, with_requests=False)
        loaded = [single] if single is not None else []
    if not loaded:
        raise PerfReportError('Performance archive has no readable runs', 404)
    identity = f'path:{root}'
    runs: List[ReportRun] = []
    stamp_paths: List[Path] = []
    for item in loaded:
        run_dir = root / item.dir_name if item.dir_name != root.name else root
        stamp_paths.extend([run_dir / 'benchmark_summary.json', run_dir / 'benchmark_percentile.json'])
        summary = item.summary
        rows = _percentile_rows(item.percentiles)
        success = int(_finite(float(summary.succeed_requests)) or 0)
        ttft = reported_distribution(
            unit='seconds',
            source='run_summary',
            count=success,
            mean=_finite(summary.avg_ttft),
            p50=_reported_value(rows, '50%', 'ttft'),
            p90=_reported_value(rows, '90%', 'ttft'),
            p95=_reported_value(rows, '95%', 'ttft'),
            p99=_reported_value(rows, '99%', 'ttft'),
            reason='native_summary_metric_missing',
        )
        tpot = reported_distribution(
            unit='seconds',
            source='run_summary',
            count=success,
            mean=_finite(summary.avg_tpot),
            p50=_reported_value(rows, '50%', 'tpot'),
            p90=_reported_value(rows, '90%', 'tpot'),
            p95=_reported_value(rows, '95%', 'tpot'),
            p99=_reported_value(rows, '99%', 'tpot'),
            reason='native_summary_metric_missing',
        )
        latency = reported_distribution(
            unit='seconds',
            source='run_summary',
            count=success,
            mean=_finite(summary.avg_latency),
            p50=_reported_value(rows, '50%', 'latency'),
            p90=_reported_value(rows, '90%', 'latency'),
            p95=_reported_value(rows, '95%', 'latency'),
            p99=_reported_value(rows, '99%', 'latency'),
            reason='native_summary_metric_missing',
        )
        window = _finite(summary.time_taken)
        throughput = _finite(summary.request_throughput)
        output_throughput = _finite(summary.output_token_throughput)
        result_reason = 'native_summary_metric_missing'
        rates = RateMetrics(
            window_seconds=window,
            requests_per_second=throughput,
            requests_per_minute=throughput * 60.0 if throughput is not None else None,
            output_tokens_per_second=output_throughput,
            output=TokenRate(
                tokens_per_second=output_throughput,
                covered_requests=success if output_throughput is not None else 0,
                successful_requests=success,
                coverage=1.0 if output_throughput is not None else None,
                source='run_summary',
                availability='available' if output_throughput is not None else 'unavailable',
                reason=None if output_throughput is not None else result_reason,
            ),
            input=TokenRate(
                successful_requests=success,
                reason='native_summary_reports_output_tokens_only',
            ),
            cache=CachePairing(successful_requests=success, reason='native_archive_has_no_cache_usage'),
            source='run_summary',
            window_source='run_summary',
            window_authoritative=False,
            availability='available' if throughput is not None else 'unavailable',
            reason=None if throughput is not None else result_reason,
        )
        args = item.args or {}
        output_limit = args.get('max_tokens')
        runs.append(
            ReportRun(
                run_key=f'{identity}:{item.dir_name}',
                run_id=item.dir_name,
                order=len(runs),
                archive_identity=identity,
                dimensions=dimensions_of(
                    load_mode='open_loop' if item.dir_name.startswith(_NATIVE_RATE) else 'closed_loop',
                    concurrency=int(item.parallel) if item.parallel else None,
                    rate=item.rate,
                    output_token_limit=int(output_limit) if isinstance(output_limit, int) else None,
                ),
                lifecycle='completed' if summary.total_requests else 'unknown',
                quality='complete' if summary.failed_requests == 0 else 'partial',
                counts=ReportRunCounts(
                    planned_requests=int(summary.total_requests),
                    total=int(summary.total_requests),
                    success=success,
                    error=int(summary.failed_requests),
                ),
                ttft=ttft,
                tpot=tpot,
                e2e=latency,
                tokens=TokenSummary(successful_requests=success),
                rates=rates,
                observation=ObservationWindow(seconds=window, total_requests=int(summary.total_requests)),
                observed_concurrency=float(summary.concurrency) if summary.concurrency else None,
                availability='available'
                if (ttft.availability == 'available' or latency.availability == 'available')
                else 'unavailable',
                reason=None
                if (ttft.availability == 'available' or latency.availability == 'available')
                else 'native_summary_metric_missing',
                quality_reasons=[],
            )
        )
    first = loaded[0].args or {}
    header = dict(
        source_kind='native',
        source_identity=identity,
        model=first.get('model') or first.get('model_id'),
        status=None,
        quality=None,
        quality_reasons=[],
        deployment_mode=None,
        partial=False,
    )
    return _finish_snapshot(runs, header, {}, stamp_paths, identity)


# ---------------------------------------------------------------------------
# Assembly and caching
# ---------------------------------------------------------------------------


def _capabilities(runs: List[ReportRun], kind: str, normalized_dirs: Dict[str, str]) -> ReportCapabilities:
    available_rows = [run for run in runs if run.availability == 'available']
    node_rows = [run for run in runs if run.nodes is not None and run.nodes.availability in ('available', 'partial')]
    has_tokens = any(
        run.tokens.input_tokens is not None or run.tokens.output_tokens is not None for run in available_rows
    )
    has_cache = any(run.tokens.cached_tokens is not None for run in available_rows)
    if kind == 'native':
        return ReportCapabilities(
            client_metrics=ReportCapability(
                availability='available'
                if any(run.ttft.availability == 'available' for run in runs)
                else 'unavailable',
                reason='reported_percentiles_only' if runs else 'no_runs',
            ),
            token_usage=ReportCapability(availability='partial', reason='native_summary_reports_averages_only'),
            cache=ReportCapability(availability='unavailable', reason='native_archive_has_no_cache_usage'),
            node_metrics=ReportCapability(availability='unavailable', reason='native_archive_has_no_node_series'),
            requests=ReportCapability(availability='unsupported', reason='native_archive_uses_legacy_request_view'),
        )
    return ReportCapabilities(
        client_metrics=ReportCapability(
            availability='available' if available_rows else 'unavailable',
            reason=None if available_rows else 'no_readable_runs',
        ),
        token_usage=ReportCapability(
            availability='available' if has_tokens else 'unavailable',
            reason=None if has_tokens else 'no_token_usage_in_requests',
        ),
        cache=ReportCapability(
            availability='available' if has_cache else 'unavailable',
            reason=None if has_cache else 'no_cache_usage_recorded',
        ),
        node_metrics=ReportCapability(
            availability='available' if node_rows else 'unavailable',
            reason=None if node_rows else 'no_persisted_node_analysis',
        ),
        requests=ReportCapability(
            availability='available' if normalized_dirs else 'unavailable',
            reason=None if normalized_dirs else 'no_normalized_request_store',
        ),
    )


def _finish_snapshot(
    runs: List[ReportRun],
    header: dict,
    normalized_dirs: Dict[str, str],
    stamp_paths: List[Path],
    identity: str,
    generations: Optional[Dict[str, str]] = None,
) -> ReportSnapshot:
    stamp = _digest_files(*stamp_paths)
    document = ReportDocument(
        snapshot_id=snapshot_id_for(identity, stamp),
        created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        counts=build_counts(runs),
        groups=build_groups(runs),
        capabilities=_capabilities(runs, header['source_kind'], normalized_dirs),
        runs=runs,
        **header,
    )
    return ReportSnapshot(
        document=document, normalized_dirs=normalized_dirs, generations=dict(generations or {}), stamp=stamp
    )


_SNAPSHOT_CACHE: 'OrderedDict[Tuple[str, str, str], ReportSnapshot]' = OrderedDict()
_SNAPSHOT_LOCK = Lock()


def _stamp_for(root: Path, kind: str) -> str:
    """A cheap content stamp; the full snapshot is only rebuilt when it moves."""
    if kind == 'plan':
        directory, _legacy = resolve_plan_dir(_plan_task_dir(root))
        paths = [directory / 'compiled.json', directory / 'normalized.json', directory / 'manifest.json']
        runs_dir = directory.parent / 'runs'
        for path in sorted(runs_dir.glob('*')):
            if path.is_dir():
                paths.extend(_run_sources(path))
        return _digest_files(*paths)
    if kind == 'cache_experiment':
        paths = [root / 'experiment_plan.json', root / 'experiment.json']
        for path in sorted((root / 'runs').glob('*')):
            if path.is_dir():
                paths.extend(_run_sources(path))
        return _digest_files(*paths)
    if kind == 'single':
        return _digest_files(root / 'single_run.json', *_run_sources(root))
    paths = []
    for entry in sorted(root.iterdir()) if root.is_dir() else []:
        if entry.is_dir():
            paths.extend([entry / 'benchmark_summary.json', entry / 'benchmark_percentile.json'])
    paths.extend([root / 'benchmark_summary.json', root / 'benchmark_percentile.json'])
    return _digest_files(*paths)


_BUILDERS = {
    'plan': _plan_snapshot,
    'cache_experiment': _cache_snapshot,
    'single': _single_snapshot,
    'native': _native_snapshot,
}


def load_report_snapshot(
    *,
    outputs_root: str,
    task_id: Optional[str] = None,
    root_path: Optional[str] = None,
    rel_path: Optional[str] = None,
) -> ReportSnapshot:
    """Load (or reuse) the fixed snapshot for one archive."""
    root = _perf_root(outputs_root, task_id, root_path, rel_path)
    kind = _detect_kind(root)
    stamp = _stamp_for(root, kind)
    key = (str(root), task_id or '', stamp)
    with _SNAPSHOT_LOCK:
        cached = _SNAPSHOT_CACHE.get(key)
        if cached is not None:
            _SNAPSHOT_CACHE.move_to_end(key)
            return cached
    snapshot = _BUILDERS[kind](root)
    stamp_after = _stamp_for(root, kind)
    if stamp_after != stamp:
        # The source moved while it was read: rebuild once so the published
        # snapshot describes one consistent state instead of a half-old mix.
        stamp = stamp_after
        snapshot = _BUILDERS[kind](root)
        if _stamp_for(root, kind) != stamp:
            snapshot.document = snapshot.document.model_copy(
                update={'partial': True, 'partial_reason': 'source_changed_during_read'}
            )
        key = (str(root), task_id or '', stamp)
    if task_id and not snapshot.document.task_id:
        snapshot.document = snapshot.document.model_copy(update={'task_id': task_id})
    with _SNAPSHOT_LOCK:
        for stale in [key for key in _SNAPSHOT_CACHE if key[0] == str(root) and key[2] != stamp]:
            _SNAPSHOT_CACHE.pop(stale, None)
        _SNAPSHOT_CACHE[key] = snapshot
        while len(_SNAPSHOT_CACHE) > SNAPSHOT_CACHE_LIMIT:
            _SNAPSHOT_CACHE.popitem(last=False)
    return snapshot


def resolve_report_root(
    *,
    outputs_root: str,
    task_id: Optional[str] = None,
    root_path: Optional[str] = None,
    rel_path: Optional[str] = None,
) -> Path:
    """The archive directory a report request names, with the same path checks."""
    return _perf_root(outputs_root, task_id, root_path, rel_path)


__all__ = [
    'CHART_METRICS',
    'CHART_STATISTICS',
    'PAGE_LIMIT',
    'PerfReportError',
    'REQUEST_SORT_KEYS',
    'RUN_SORT_KEYS',
    'ReportSnapshot',
    'TOP_REQUEST_METRICS',
    'load_report_snapshot',
    'resolve_report_root',
]
