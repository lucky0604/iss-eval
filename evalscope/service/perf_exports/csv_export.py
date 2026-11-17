"""Machine-readable exports: summary CSV, every matching request, JSONL.

CSV keeps one stable ASCII header per column, full numeric precision and an
empty cell for a missing value -- never a localized string, never a unit glued
to a number. Text is guarded against spreadsheet formula injection; numbers are
written untouched so a consumer can compute on them.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from evalscope.perf.results.report import ReportRequestRow, ReportRun
from evalscope.perf.results.table import EXPORT_COLUMNS, ColumnSpec, format_csv_cell, run_record

from ..perf_reports import ReportSnapshot
from .collect import ExportDataset, request_rows
from .models import ExportCancelled

_INJECTION_PREFIXES = ('=', '+', '-', '@', '\t', '\r')

# The set the printed report shows; ``preset='all'`` widens the CSV to every
# stable column, including min/max/std and token provenance.
CORE_EXPORT_KEYS: Tuple[str, ...] = (
    'run_id',
    'run_key',
    'order',
    'deployment_mode',
    'workload',
    'message_structure',
    'input_tokens',
    'output_token_limit',
    'target_ratio',
    'load_mode',
    'concurrency',
    'rate',
    'repetition',
    'lifecycle',
    'availability',
    'quality',
    'total_requests',
    'success_requests',
    'error_requests',
    'ttft_avg_ms',
    'ttft_p90_ms',
    'ttft_p95_ms',
    'ttft_p99_ms',
    'ttft_count',
    'tpot_avg_ms',
    'tpot_p90_ms',
    'tpot_p95_ms',
    'tpot_p99_ms',
    'tpot_count',
    'e2e_avg_s',
    'e2e_p99_s',
    'e2e_count',
    'input_tokens_total',
    'output_tokens_total',
    'cached_tokens_total',
    'input_tps',
    'output_tps',
    'rpm',
    'window_seconds',
)


def summary_columns(preset: str = 'core') -> List[ColumnSpec]:
    if preset == 'all':
        return list(EXPORT_COLUMNS)
    keys = set(CORE_EXPORT_KEYS)
    return [column for column in EXPORT_COLUMNS if column.key in keys]


def _guard(value: str) -> str:
    """Prefix text a spreadsheet would otherwise evaluate as a formula."""
    return f"'{value}" if value.startswith(_INJECTION_PREFIXES) else value


def _cell(value: Optional[object], column: ColumnSpec) -> str:
    text = format_csv_cell(value, column)
    return _guard(text) if column.kind == 'text' else text


def write_summary_csv(path: Path, dataset: ExportDataset, *, preset: str = 'core') -> int:
    """Write one row per selected run; returns the row count."""
    columns = summary_columns(preset)
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow([column.header for column in columns])
        count = 0
        for run in dataset.runs:
            record = run_record(run)
            writer.writerow([_cell(record[column.key], column) for column in columns])
            count += 1
    return count


_NODE_METRIC_HEADERS: Tuple[str, ...] = (
    'run_id',
    'run_key',
    'node_id',
    'role',
    'engine',
    'metric_name',
    'metric_unit',
    'metric_kind',
    'labels',
    'window_source',
    'availability',
    'value',
    'sample_peak',
    'sample_count',
    'coverage',
    'boundary',
    'statistic_reason',
    'histogram_count',
    'histogram_mean',
    'histogram_p50',
    'histogram_p95',
    'histogram_p99',
    'histogram_unit',
    'histogram_boundary',
    'histogram_reason',
    'series_reason',
)

_NODE_CACHE_HEADERS: Tuple[str, ...] = (
    'run_id',
    'run_key',
    'node_id',
    'role',
    'availability',
    'hit_tokens',
    'lookup_tokens',
    'ratio',
    'semantics',
    'reason',
)


def _node_availability(statistic, histogram) -> Optional[str]:
    if statistic is not None:
        return statistic.availability
    if histogram is not None:
        return histogram.availability
    return None


def write_node_metrics_csv(path: Path, dataset: ExportDataset) -> int:
    """One row per run/node/metric/label series; a missing value stays empty."""
    count = 0
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(_NODE_METRIC_HEADERS)
        for run in dataset.runs:
            if run.nodes is None:
                continue
            for metric in run.nodes.metrics:
                statistic = metric.statistic
                histogram = metric.histogram
                writer.writerow(
                    [
                        _csv_value(run.run_id),
                        _csv_value(run.run_key),
                        _csv_value(metric.node_id),
                        _csv_value(metric.role),
                        _csv_value(metric.engine),
                        _csv_value(metric.metric_name),
                        _csv_value(metric.metric_unit),
                        _csv_value(metric.metric_kind),
                        _csv_value(json.dumps(dict(metric.labels), sort_keys=True, separators=(',', ':'))),
                        _csv_value(metric.window_source),
                        _csv_value(_node_availability(statistic, histogram)),
                        _csv_value(statistic.value if statistic is not None else None),
                        _csv_value(statistic.sample_peak if statistic is not None else None),
                        _csv_value(statistic.sample_count if statistic is not None else None),
                        _csv_value(statistic.coverage if statistic is not None else None),
                        _csv_value(statistic.boundary if statistic is not None else None),
                        _csv_value(statistic.reason if statistic is not None else None),
                        _csv_value(histogram.count if histogram is not None else None),
                        _csv_value(histogram.mean if histogram is not None else None),
                        _csv_value(histogram.p50 if histogram is not None else None),
                        _csv_value(histogram.p95 if histogram is not None else None),
                        _csv_value(histogram.p99 if histogram is not None else None),
                        _csv_value(histogram.unit if histogram is not None else None),
                        _csv_value(histogram.boundary if histogram is not None else None),
                        _csv_value(histogram.reason if histogram is not None else None),
                        _csv_value(metric.reason),
                    ]
                )
                count += 1
    return count


def write_node_cache_csv(path: Path, dataset: ExportDataset) -> int:
    """Prefix cache hit ratios, kept apart from response cache usage."""
    count = 0
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(_NODE_CACHE_HEADERS)
        for run in dataset.runs:
            if run.nodes is None:
                continue
            for ratio in run.nodes.cache_ratios:
                writer.writerow(
                    [
                        _csv_value(run.run_id),
                        _csv_value(run.run_key),
                        _csv_value(ratio.node_id),
                        _csv_value(ratio.role),
                        _csv_value(ratio.availability),
                        _csv_value(ratio.hit_tokens),
                        _csv_value(ratio.lookup_tokens),
                        _csv_value(ratio.ratio),
                        _csv_value(ratio.semantics),
                        _csv_value(ratio.reason),
                    ]
                )
                count += 1
    return count


def _request_columns() -> List[Tuple[str, str]]:
    return [
        ('run_id', 'run_id'),
        ('run_key', 'run_key'),
        ('index', 'index'),
        ('request_id', 'request_id'),
        ('phase', 'phase'),
        ('status', 'status'),
        ('prompt_tokens', 'prompt_tokens'),
        ('prompt_tokens_source', 'prompt_tokens_source'),
        ('output_tokens', 'output_tokens'),
        ('output_tokens_source', 'output_tokens_source'),
        ('cached_tokens', 'cached_tokens'),
        ('cached_tokens_source', 'cached_tokens_source'),
        ('ttft_ms', 'ttft_ms'),
        ('tpot_ms', 'tpot_ms'),
        ('tpot_source', 'tpot_source'),
        ('tpot_method', 'tpot_method'),
        ('latency_s', 'latency_s'),
        ('http_total_s', 'http_total_s'),
        ('http_duration_s', 'http_duration_s'),
        ('error_code', 'error_code'),
        ('status_reason', 'status_reason'),
    ]


def _request_values(run: ReportRun, row: ReportRequestRow) -> List[object]:
    return [
        run.run_id,
        run.run_key,
        row.index,
        row.request_id,
        row.phase,
        row.status,
        row.prompt_tokens,
        row.prompt_tokens_source,
        row.output_tokens,
        row.output_tokens_source,
        row.cached_tokens,
        row.cached_tokens_source,
        None if row.ttft_seconds is None else row.ttft_seconds * 1000.0,
        None if row.tpot_seconds is None else row.tpot_seconds * 1000.0,
        row.tpot_source,
        row.tpot_method,
        row.latency_seconds,
        row.http_total_seconds,
        row.http_duration_seconds,
        row.error_code,
        row.status_reason,
    ]


def _csv_value(value: object) -> object:
    if value is None:
        return ''
    if isinstance(value, str):
        return _guard(value)
    return value


def write_requests_csv(
    path: Path,
    snapshot: ReportSnapshot,
    *,
    group: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    run_key: Optional[str] = None,
    phase: Optional[str] = 'profiling',
    should_stop: Optional[Callable[[int], bool]] = None,
) -> Tuple[int, int]:
    """Stream every matching request of every selected run to CSV."""
    run_count, rows = request_rows(snapshot, group=group, status=status, search=search, run_key=run_key, phase=phase)
    headers = [header for header, _key in _request_columns()]
    count = 0
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(headers)
        for run, row in rows:
            if should_stop is not None and count % 200 == 0 and should_stop(count):
                raise ExportCancelled()
            writer.writerow([_csv_value(value) for value in _request_values(run, row)])
            count += 1
    return count, run_count


def write_requests_jsonl(
    path: Path,
    snapshot: ReportSnapshot,
    *,
    group: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    run_key: Optional[str] = None,
    phase: Optional[str] = 'profiling',
    should_stop: Optional[Callable[[int], bool]] = None,
) -> int:
    """Raw exchange format: one JSON object per matching request, nothing localised."""
    _run_count, rows = request_rows(snapshot, group=group, status=status, search=search, run_key=run_key, phase=phase)
    count = 0
    with open(path, 'w', encoding='utf-8') as handle:
        for run, row in rows:
            if should_stop is not None and count % 200 == 0 and should_stop(count):
                raise ExportCancelled()
            payload = row.model_dump(mode='json')
            payload['run_id'] = run.run_id
            payload['run_key'] = run.run_key
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
            handle.write('\n')
            count += 1
    return count


def iter_summary_rows(dataset: ExportDataset, *, preset: str = 'core') -> Iterable[Tuple[ColumnSpec, object]]:
    """Kept for small callers; large exports always stream through the writers above."""
    columns = summary_columns(preset)
    for run in dataset.runs:
        record = run_record(run)
        for column in columns:
            yield column, record[column.key]
