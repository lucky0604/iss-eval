"""Stable column dictionary shared by the Web table, CSV and print exports.

One definition per metric so the screen, the machine-readable CSV and the
rendered report cannot drift: the CSV header is the stable ASCII ``key``, the
print tables reuse the same accessor and only differ in formatting. Seconds are
exported as seconds (``*_s``) and milliseconds as milliseconds (``*_ms``); no
column mixes units and no cell is ever a nested object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Literal, Optional

from .report import Distribution, ReportRun

ColumnKind = Literal['number', 'integer', 'text']


@dataclass(frozen=True)
class ColumnSpec:
    """One stable column: identity, label and how to read its scalar value."""

    key: str
    header: str
    group: str
    unit: Optional[str]
    digits: int
    kind: ColumnKind
    value: Callable[[ReportRun], Optional[object]]
    label_key: Optional[str] = None


def _distribution(run: ReportRun, name: str) -> Distribution:
    return {'ttft': run.ttft, 'tpot': run.tpot, 'e2e': run.e2e}[name]


def _stat(name: str, field: str) -> Callable[[ReportRun], Optional[float]]:
    def read(run: ReportRun) -> Optional[float]:
        return getattr(_distribution(run, name), field)

    return read


def _scaled(name: str, field: str, scale: float) -> Callable[[ReportRun], Optional[float]]:
    read = _stat(name, field)

    def scaled(run: ReportRun) -> Optional[float]:
        value = read(run)
        return None if value is None else value * scale

    return scaled


def _seconds(name: str, field: str) -> Callable[[ReportRun], Optional[float]]:
    return _stat(name, field)


def _dimension(field: str) -> Callable[[ReportRun], Optional[object]]:
    def read(run: ReportRun) -> Optional[object]:
        return getattr(run.dimensions, field)

    return read


_LATENCY_STATS = (
    ('avg', 'mean'),
    ('p50', 'p50'),
    ('p90', 'p90'),
    ('p95', 'p95'),
    ('p99', 'p99'),
    ('min', 'min'),
    ('max', 'max'),
    ('std', 'std'),
)


def _latency_columns(name: str, label: str, group: str, key_prefix: str, in_ms: bool) -> List[ColumnSpec]:
    scale = 1000.0 if in_ms else 1.0
    unit = 'ms' if in_ms else 's'
    suffix = '_ms' if in_ms else '_s'
    columns = [
        ColumnSpec(
            key=f'{key_prefix}_{stat}{suffix}',
            header=f'{key_prefix}_{stat}{suffix}',
            group=group,
            unit=unit,
            digits=2,
            kind='number',
            value=_scaled(name, field, scale),
            label_key=f'perf.report.col.{"mean" if stat == "avg" else stat}',
        )
        for stat, field in _LATENCY_STATS
    ]
    columns.append(
        ColumnSpec(
            key=f'{key_prefix}_count',
            header=f'{key_prefix}_count',
            group=group,
            unit='requests',
            digits=0,
            kind='integer',
            value=lambda run: _distribution(run, name).count,
            label_key='perf.report.col.count',
        )
    )
    return columns


EXPORT_COLUMNS: List[ColumnSpec] = [
    ColumnSpec('run_id', 'run_id', 'identity', None, 0, 'text', lambda run: run.run_id),
    ColumnSpec('run_key', 'run_key', 'identity', None, 0, 'text', lambda run: run.run_key),
    ColumnSpec('order', 'order', 'identity', None, 0, 'integer', lambda run: run.order),
    ColumnSpec('deployment_mode', 'deployment_mode', 'dimensions', None, 0, 'text', _dimension('deployment_mode')),
    ColumnSpec('workload', 'workload', 'dimensions', None, 0, 'text', _dimension('workload')),
    ColumnSpec(
        'message_structure', 'message_structure', 'dimensions', None, 0, 'text', _dimension('message_structure')
    ),
    ColumnSpec('input_tokens', 'input_tokens', 'dimensions', 'tokens', 0, 'integer', _dimension('input_tokens')),
    ColumnSpec(
        'output_token_limit',
        'output_token_limit',
        'dimensions',
        'tokens',
        0,
        'integer',
        _dimension('output_token_limit'),
    ),
    ColumnSpec('target_ratio', 'target_ratio', 'dimensions', 'ratio', 4, 'number', _dimension('target_ratio')),
    ColumnSpec('load_mode', 'load_mode', 'dimensions', None, 0, 'text', _dimension('load_mode')),
    ColumnSpec('concurrency', 'concurrency', 'dimensions', 'concurrency', 0, 'integer', _dimension('concurrency')),
    ColumnSpec('rate', 'rate', 'dimensions', 'requests/s', 3, 'number', _dimension('rate')),
    ColumnSpec('repetition', 'repetition', 'dimensions', None, 0, 'integer', _dimension('repetition')),
    ColumnSpec('lifecycle', 'lifecycle', 'result', None, 0, 'text', lambda run: run.lifecycle),
    ColumnSpec('availability', 'availability', 'result', None, 0, 'text', lambda run: run.availability),
    ColumnSpec('quality', 'quality', 'result', None, 0, 'text', lambda run: run.quality),
    ColumnSpec(
        'planned_requests',
        'planned_requests',
        'result',
        'requests',
        0,
        'integer',
        lambda run: run.counts.planned_requests,
    ),
    ColumnSpec('total_requests', 'total_requests', 'result', 'requests', 0, 'integer', lambda run: run.counts.total),
    ColumnSpec(
        'success_requests', 'success_requests', 'result', 'requests', 0, 'integer', lambda run: run.counts.success
    ),
    ColumnSpec('error_requests', 'error_requests', 'result', 'requests', 0, 'integer', lambda run: run.counts.error),
    ColumnSpec(
        'cancelled_requests', 'cancelled_requests', 'result', 'requests', 0, 'integer', lambda run: run.counts.cancelled
    ),
    ColumnSpec(
        'incomplete_requests',
        'incomplete_requests',
        'result',
        'requests',
        0,
        'integer',
        lambda run: run.counts.incomplete,
    ),
    *_latency_columns('ttft', 'TTFT', 'ttft', 'ttft', in_ms=True),
    *_latency_columns('tpot', 'TPOT', 'tpot', 'tpot', in_ms=True),
    *_latency_columns('e2e', 'E2E', 'e2e', 'e2e', in_ms=False),
    ColumnSpec(
        'input_tokens_total',
        'input_tokens_total',
        'tokens',
        'tokens',
        0,
        'integer',
        lambda run: run.tokens.input_tokens,
    ),
    ColumnSpec(
        'output_tokens_total',
        'output_tokens_total',
        'tokens',
        'tokens',
        0,
        'integer',
        lambda run: run.tokens.output_tokens,
    ),
    ColumnSpec(
        'cached_tokens_total',
        'cached_tokens_total',
        'tokens',
        'tokens',
        0,
        'integer',
        lambda run: run.tokens.cached_tokens,
    ),
    ColumnSpec(
        'input_tokens_source', 'input_tokens_source', 'tokens', None, 0, 'text', lambda run: run.tokens.input_source
    ),
    ColumnSpec(
        'output_tokens_source', 'output_tokens_source', 'tokens', None, 0, 'text', lambda run: run.tokens.output_source
    ),
    ColumnSpec(
        'cached_tokens_source', 'cached_tokens_source', 'tokens', None, 0, 'text', lambda run: run.tokens.cached_source
    ),
    ColumnSpec(
        'input_tps', 'input_tps', 'throughput', 'tokens/s', 2, 'number', lambda run: run.rates.input.tokens_per_second
    ),
    ColumnSpec(
        'output_tps',
        'output_tps',
        'throughput',
        'tokens/s',
        2,
        'number',
        lambda run: run.rates.output.tokens_per_second,
    ),
    ColumnSpec(
        'input_tps_coverage',
        'input_tps_coverage',
        'throughput',
        None,
        4,
        'number',
        lambda run: run.rates.input.coverage,
    ),
    ColumnSpec(
        'output_tps_coverage',
        'output_tps_coverage',
        'throughput',
        None,
        4,
        'number',
        lambda run: run.rates.output.coverage,
    ),
    ColumnSpec(
        'input_tps_source', 'input_tps_source', 'throughput', None, 0, 'text', lambda run: run.rates.input.source
    ),
    ColumnSpec(
        'output_tps_source', 'output_tps_source', 'throughput', None, 0, 'text', lambda run: run.rates.output.source
    ),
    ColumnSpec(
        'requests_per_second',
        'requests_per_second',
        'throughput',
        'requests/s',
        3,
        'number',
        lambda run: run.rates.requests_per_second,
    ),
    ColumnSpec('rpm', 'rpm', 'throughput', 'requests/min', 2, 'number', lambda run: run.rates.requests_per_minute),
    ColumnSpec(
        'cache_pairing', 'cache_pairing', 'throughput', None, 0, 'text', lambda run: run.rates.cache.availability
    ),
    ColumnSpec(
        'cache_paired_requests',
        'cache_paired_requests',
        'throughput',
        'requests',
        0,
        'integer',
        lambda run: run.rates.cache.paired_requests,
    ),
    ColumnSpec('window_source', 'window_source', 'observation', None, 0, 'text', lambda run: run.rates.window_source),
    ColumnSpec(
        'window_authoritative',
        'window_authoritative',
        'observation',
        None,
        0,
        'text',
        lambda run: run.rates.window_authoritative,
    ),
    ColumnSpec(
        'window_seconds', 'window_seconds', 'observation', 's', 3, 'number', lambda run: run.observation.seconds
    ),
    ColumnSpec(
        'observed_concurrency',
        'observed_concurrency',
        'observation',
        'requests',
        3,
        'number',
        lambda run: run.observed_concurrency,
    ),
    ColumnSpec(
        'observed_concurrency_peak',
        'observed_concurrency_peak',
        'observation',
        'requests',
        0,
        'integer',
        lambda run: run.observed_concurrency_peak,
    ),
    ColumnSpec(
        'quality_reasons',
        'quality_reasons',
        'observation',
        None,
        0,
        'text',
        lambda run: ';'.join(run.quality_reasons) if run.quality_reasons else None,
    ),
]

COLUMNS_BY_KEY: Dict[str, ColumnSpec] = {column.key: column for column in EXPORT_COLUMNS}

# The two-level print table: one group per header band, in this order.
PRINT_GROUPS = ('ttft', 'tpot', 'e2e', 'throughput', 'result')


def column_value(run: ReportRun, column: ColumnSpec) -> Optional[object]:
    """Read one scalar cell; the accessor contract forbids returning objects."""
    value = column.value(run)
    if value is not None and isinstance(value, (dict, list, tuple, set)):
        raise TypeError(f'Column {column.key} must yield a scalar, got {type(value).__name__}')
    return value


def run_record(run: ReportRun) -> Dict[str, Optional[object]]:
    """One run as a flat ``{stable column key: scalar}`` mapping."""
    return {column.key: column_value(run, column) for column in EXPORT_COLUMNS}


def format_csv_cell(value: Optional[object], column: ColumnSpec) -> str:
    """Machine CSV: empty for missing, full numeric precision, never units."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return str(value)


def format_display_cell(value: Optional[object], column: ColumnSpec) -> str:
    """Human table: fixed decimals, a dash for missing, a real zero stays zero."""
    if value is None:
        return '—'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f'{value:.{column.digits}f}'
    return str(value)


def print_columns(group: str) -> List[ColumnSpec]:
    """Columns of one print band; TTFT/TPOT keep all four required statistics."""
    return [column for column in EXPORT_COLUMNS if column.group == group]
