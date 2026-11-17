"""Unified, provenance-aware report projection for performance archives.

Nothing here reads service state: the service resolves an archive and feeds
normalized runs (or reported native summaries) into these models, so the Web
API, the HTML/PDF/CSV exports and the CLI all describe one snapshot instead of
recomputing metrics in three places.

Percentiles are exact nearest-rank values over the declared population
(``x[ceil(q * n)]`` of the sorted samples), computed through a bounded
temporary SQLite value table so one large run never has to sit in Python
memory. Percentiles are never averaged: a merged group repeats per-run values,
and the report keeps one row per planned run.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from typing import Dict, Iterable, Iterator, List, Literal, Optional, Tuple

from pydantic import Field

from evalscope.utils import BaseArgument

from .aiperf_import import query_aiperf_requests
from .derivation import resolve_itl
from .models import PerfRequestRecord

STATISTICS_VERSION = '1'
PERCENTILE_METHOD: Literal['nearest_rank'] = 'nearest_rank'
REQUEST_PAGE_SIZE = 500

Quality = Literal['complete', 'partial', 'invalid']
RunLifecycle = Literal['not_started', 'running', 'completed', 'cancelled', 'error', 'unknown']
Availability = Literal['available', 'partial', 'unavailable']
PointAvailability = Literal['available', 'partial', 'unavailable']
SourceKind = Literal['plan', 'cache_experiment', 'single', 'native']


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------


class Distribution(BaseArgument):
    """Exact percentiles plus their population, source and availability.

    Every numeric field is ``None`` when the value is genuinely missing; a
    measured zero stays ``0``. ``percentile_method`` distinguishes a
    recomputed nearest-rank value from a percentile the native summary
    reported in its own table.
    """

    count: int = Field(ge=0)
    mean: Optional[float] = None
    p50: Optional[float] = None
    p90: Optional[float] = None
    p95: Optional[float] = None
    p99: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    std: Optional[float] = None
    unit: str
    source: str
    population: Literal['successful_profiling_requests', 'run_summary', 'server_usage']
    percentile_method: Literal['nearest_rank', 'reported']
    availability: Availability = 'available'
    reason: Optional[str] = None
    sources: Dict[str, int] = Field(default_factory=dict)


class RequestCounts(BaseArgument):
    """Status counts for the profiling requests a run actually produced."""

    total: int = Field(default=0, ge=0)
    success: int = Field(default=0, ge=0)
    error: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    incomplete: int = Field(default=0, ge=0)


class TokenSummary(BaseArgument):
    """Request token totals with their real coverage, never an implied zero."""

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    successful_requests: int = Field(default=0, ge=0)
    input_requests: int = Field(default=0, ge=0)
    output_requests: int = Field(default=0, ge=0)
    cached_requests: int = Field(default=0, ge=0)
    input_source: Optional[Literal['server_usage', 'aiperf_estimate', 'mixed']] = None
    output_source: Optional[Literal['server_usage', 'aiperf_estimate', 'mixed']] = None
    cached_source: Optional[Literal['server_usage']] = None


class TokenRate(BaseArgument):
    """One direction's token throughput with the coverage it was computed from.

    ``tokens_per_second`` is only set when the direction is fully covered by the
    successful requests and a statistics window exists. A fully covered zero is
    a measured ``0.0``; a partial or absent direction stays ``None`` with the
    reason, so a rate is never extrapolated from a subset of the requests.
    """

    tokens: Optional[int] = None
    tokens_per_second: Optional[float] = None
    covered_requests: int = Field(default=0, ge=0)
    successful_requests: int = Field(default=0, ge=0)
    coverage: Optional[float] = None
    source: Optional[Literal['server_usage', 'aiperf_estimate', 'mixed', 'run_summary']] = None
    availability: Availability = 'unavailable'
    reason: Optional[str] = None


class CachePairing(BaseArgument):
    """How many successful requests carry a verified prompt/cache token pair."""

    availability: Literal['available', 'partial', 'unknown'] = 'unknown'
    paired_requests: int = Field(default=0, ge=0)
    successful_requests: int = Field(default=0, ge=0)
    reason: Optional[str] = None


class RateMetrics(BaseArgument):
    """Client-measured rates; a configured target rate is never reported here."""

    window_seconds: Optional[float] = None
    requests_per_second: Optional[float] = None
    requests_per_minute: Optional[float] = None
    input_tokens_per_second: Optional[float] = None
    output_tokens_per_second: Optional[float] = None
    input: TokenRate = Field(default_factory=TokenRate)
    output: TokenRate = Field(default_factory=TokenRate)
    cache: CachePairing = Field(default_factory=CachePairing)
    source: Literal['measured_records', 'run_summary'] = 'measured_records'
    window_source: Optional[Literal['aiperf_phase', 'observed_request_bounds', 'run_summary']] = None
    window_authoritative: Optional[bool] = None
    availability: Availability = 'available'
    reason: Optional[str] = None


class ObservationWindow(BaseArgument):
    """Observed request time extent, distinct from an authoritative phase window."""

    start_ns: Optional[int] = None
    end_ns: Optional[int] = None
    seconds: Optional[float] = None
    timed_requests: int = Field(default=0, ge=0)
    total_requests: int = Field(default=0, ge=0)


class RequestAggregation(BaseArgument):
    """Everything one normalized run can say without re-reading its raw files."""

    counts: RequestCounts
    ttft: Distribution
    tpot: Distribution
    e2e: Distribution
    tokens: TokenSummary
    rates: RateMetrics
    observation: ObservationWindow
    observed_concurrency: Optional[float] = None
    observed_concurrency_peak: Optional[int] = None


# ---------------------------------------------------------------------------
# Exact percentile accumulation
# ---------------------------------------------------------------------------


class _ValueStore:
    """A bounded, exact sample table backed by a private temporary database."""

    def __init__(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix='perf-report-values-')
        self._database = sqlite3.connect(os.path.join(self._directory.name, 'values.db'))
        self._database.execute('CREATE TABLE samples (value REAL NOT NULL)')
        self._pending: List[tuple] = []
        self.count = 0
        self.total = 0.0
        self.squares = 0.0
        self.minimum: Optional[float] = None
        self.maximum: Optional[float] = None

    def add(self, value: Optional[float]) -> None:
        if value is None:
            return
        if not math.isfinite(value) or value < 0:
            raise ValueError('Invalid negative or non-finite performance sample')
        self._pending.append((value,))
        self.count += 1
        self.total += value
        self.squares += value * value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        if len(self._pending) >= 1000:
            self._flush()

    def _flush(self) -> None:
        if self._pending:
            self._database.executemany('INSERT INTO samples VALUES (?)', self._pending)
            self._pending.clear()

    def percentile(self, q: float) -> Optional[float]:
        """Nearest-rank percentile: the value at 1-based rank ``ceil(q * n)``."""
        if not self.count:
            return None
        self._flush()
        rank = max(1, math.ceil(q * self.count))
        row = self._database.execute(
            'SELECT value FROM samples ORDER BY value LIMIT 1 OFFSET ?', (rank - 1,)
        ).fetchone()
        return float(row[0]) if row is not None else None

    def mean(self) -> Optional[float]:
        return self.total / self.count if self.count else None

    def std(self) -> Optional[float]:
        if not self.count:
            return None
        mean = self.total / self.count
        return math.sqrt(max(0.0, self.squares / self.count - mean * mean))

    def close(self) -> None:
        try:
            self._database.close()
        finally:
            self._directory.cleanup()

    def __enter__(self) -> '_ValueStore':
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _finish(
    store: _ValueStore,
    *,
    unit: str,
    source: str,
    population: Literal['successful_profiling_requests', 'run_summary', 'server_usage'],
    empty_reason: str,
    sources: Optional[Dict[str, int]] = None,
) -> Distribution:
    if not store.count:
        return Distribution(
            count=0,
            unit=unit,
            source=source,
            population=population,
            percentile_method=PERCENTILE_METHOD,
            availability='unavailable',
            reason=empty_reason,
            sources=sources or {},
        )
    return Distribution(
        count=store.count,
        mean=store.mean(),
        p50=store.percentile(0.50),
        p90=store.percentile(0.90),
        p95=store.percentile(0.95),
        p99=store.percentile(0.99),
        min=store.minimum,
        max=store.maximum,
        std=store.std(),
        unit=unit,
        source=source,
        population=population,
        percentile_method=PERCENTILE_METHOD,
        availability='available',
        sources=sources or {},
    )


def unavailable_distribution(
    *,
    unit: str,
    source: str,
    population: Literal['successful_profiling_requests', 'run_summary', 'server_usage'],
    reason: str,
) -> Distribution:
    """One explicit missing metric, with the reason it is missing."""
    return Distribution(
        count=0,
        unit=unit,
        source=source,
        population=population,
        percentile_method=PERCENTILE_METHOD,
        availability='unavailable',
        reason=reason,
    )


def reported_distribution(
    *,
    unit: str,
    source: str,
    count: int = 0,
    mean: Optional[float] = None,
    p50: Optional[float] = None,
    p90: Optional[float] = None,
    p95: Optional[float] = None,
    p99: Optional[float] = None,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
    std: Optional[float] = None,
    reason: Optional[str] = None,
) -> Distribution:
    """A value a native summary already reported; never recomputed or averaged."""
    values = [value for value in (mean, p50, p90, p95, p99, minimum, maximum, std) if value is not None]
    available = bool(values)
    return Distribution(
        count=count,
        mean=mean,
        p50=p50,
        p90=p90,
        p95=p95,
        p99=p99,
        min=minimum,
        max=maximum,
        std=std,
        unit=unit,
        source=source,
        population='run_summary',
        percentile_method='reported',
        availability='available' if available else 'unavailable',
        reason=None if available else (reason or 'summary_metric_missing'),
    )


# ---------------------------------------------------------------------------
# Per-run aggregation
# ---------------------------------------------------------------------------


def iter_normalized_requests(
    normalized_dir: str, phase: Optional[str] = 'profiling', generation: Optional[str] = None
) -> Iterator[PerfRequestRecord]:
    """Stream a run's records page by page; never one task-wide materialization.

    ``generation`` pins the read to one published generation; leaving it out
    reads whatever ``current.json`` points at right now.
    """
    offset = 0
    while True:
        total, rows = query_aiperf_requests(
            normalized_dir, offset=offset, limit=REQUEST_PAGE_SIZE, phase=phase, generation=generation
        )
        yield from rows
        offset += len(rows)
        if not rows or offset >= total:
            return


def _token_source(estimated: int, used: int) -> Optional[Literal['server_usage', 'aiperf_estimate', 'mixed']]:
    if estimated and used:
        return 'mixed'
    if used:
        return 'server_usage'
    if estimated:
        return 'aiperf_estimate'
    return None


def _token_rate(
    *,
    tokens: int,
    covered: int,
    successful: int,
    seconds: Optional[float],
    source: Optional[Literal['server_usage', 'aiperf_estimate', 'mixed']],
) -> TokenRate:
    """One direction's rate: full coverage only, with measured zero kept."""
    coverage = covered / successful if successful else 0.0
    if not covered:
        return TokenRate(
            covered_requests=0,
            successful_requests=successful,
            coverage=coverage,
            source=source,
            availability='unavailable',
            reason='no_token_usage',
        )
    base = dict(
        tokens=tokens,
        covered_requests=covered,
        successful_requests=successful,
        coverage=coverage,
        source=source,
    )
    if covered < successful:
        return TokenRate(**base, availability='partial', reason='incomplete_token_coverage')
    if not seconds:
        return TokenRate(**base, availability='partial', reason='no_statistics_window')
    return TokenRate(**base, tokens_per_second=tokens / seconds, availability='available')


def _concurrency(
    intervals: List[Tuple[int, int]], *, window_start: int, window_end: int, seconds: float
) -> Tuple[Optional[float], Optional[int]]:
    """Time-average and peak in-flight requests over one statistics window.

    Every valid interval counts, whatever the request's final status: a failed
    or cancelled request still occupied the client and the server while it was
    running. Intervals are clipped to the window so a warm-up request that
    started earlier cannot inflate the concurrency of a short phase.
    """
    clipped: List[Tuple[int, int]] = []
    for start, end in intervals:
        left, right = max(start, window_start), min(end, window_end)
        if right > left:
            clipped.append((left, right))
    if not clipped:
        return 0.0, 0
    in_flight_seconds = sum(right - left for left, right in clipped) / 1e9
    events: List[Tuple[int, int]] = []
    for left, right in clipped:
        events.append((left, 1))
        events.append((right, -1))
    events.sort(key=lambda event: (event[0], event[1]))
    current = peak = 0
    for _timestamp, delta in events:
        current += delta
        peak = max(peak, current)
    return in_flight_seconds / seconds, peak


def _cache_pairing(*, paired: int, successful: int) -> CachePairing:
    if not paired:
        return CachePairing(
            paired_requests=0, successful_requests=successful, availability='unknown', reason='no_cache_usage_recorded'
        )
    if paired >= successful:
        return CachePairing(paired_requests=paired, successful_requests=successful, availability='available')
    return CachePairing(
        paired_requests=paired,
        successful_requests=successful,
        availability='partial',
        reason='incomplete_cache_coverage',
    )


def aggregate_requests(
    records: Iterable[PerfRequestRecord], *, phase_window: Optional[Tuple[int, int]] = None
) -> RequestAggregation:
    """Fold one run's profiling records into exact distributions and counts.

    ``phase_window`` is the AIPerf profiling phase from the run manifest when it
    exists. It is the authoritative rate window; rates are never labelled as an
    upstream summary value. Without it the observed request extent is used and
    explicitly marked non-authoritative.
    """
    if phase_window is not None:
        window_start, window_end = phase_window
        if (
            type(window_start) is not int
            or type(window_end) is not int
            or window_start < 0
            or window_end <= window_start
        ):
            raise ValueError('Invalid statistics window')
    counts = RequestCounts()
    ttft = _ValueStore()
    tpot = _ValueStore()
    e2e = _ValueStore()
    try:
        input_tokens = output_tokens = cached_tokens = 0
        input_known = output_known = cached_known = cache_paired = 0
        input_estimated = output_estimated = 0
        request_start: Optional[int] = None
        request_end: Optional[int] = None
        intervals: List[Tuple[int, int]] = []
        tpot_sources: Dict[str, int] = {}
        for record in records:
            counts.total += 1
            if record.status == 'success':
                counts.success += 1
            elif record.status == 'error':
                counts.error += 1
            elif record.status == 'cancelled':
                counts.cancelled += 1
            else:
                counts.incomplete += 1
            if record.start_ns is not None and record.end_ns is not None and record.end_ns >= record.start_ns:
                intervals.append((record.start_ns, record.end_ns))
                request_start = record.start_ns if request_start is None else min(request_start, record.start_ns)
                request_end = record.end_ns if request_end is None else max(request_end, record.end_ns)
            if record.status != 'success':
                continue
            ttft.add(record.ttft_seconds)
            e2e.add(record.latency_seconds)
            itl = resolve_itl(record)
            if itl.seconds is not None:
                tpot.add(itl.seconds)
                key = itl.source or 'unknown'
                tpot_sources[key] = tpot_sources.get(key, 0) + 1
            if record.prompt_tokens is not None:
                input_tokens += record.prompt_tokens
                input_known += 1
                if record.prompt_tokens_source == 'aiperf_estimate':
                    input_estimated += 1
            if record.output_tokens is not None:
                output_tokens += record.output_tokens
                output_known += 1
                if record.output_tokens_source == 'aiperf_estimate':
                    output_estimated += 1
            if record.cached_tokens is not None:
                cached_tokens += record.cached_tokens
                cached_known += 1
                if record.prompt_tokens is not None:
                    cache_paired += 1
        tokens = TokenSummary(
            input_tokens=input_tokens if input_known else None,
            output_tokens=output_tokens if output_known else None,
            cached_tokens=cached_tokens if cached_known else None,
            successful_requests=counts.success,
            input_requests=input_known,
            output_requests=output_known,
            cached_requests=cached_known,
            input_source=_token_source(input_estimated, input_known - input_estimated),
            output_source=_token_source(output_estimated, output_known - output_estimated),
            cached_source='server_usage' if cached_known else None,
        )
        extent_seconds: Optional[float] = None
        if request_start is not None and request_end is not None and request_end > request_start:
            extent_seconds = (request_end - request_start) / 1e9
        observation = ObservationWindow(
            start_ns=request_start,
            end_ns=request_end,
            seconds=extent_seconds,
            timed_requests=len(intervals),
            total_requests=counts.total,
        )
        if phase_window is not None:
            seconds: Optional[float] = (window_end - window_start) / 1e9
            window_source: Literal['aiperf_phase', 'observed_request_bounds'] = 'aiperf_phase'
            window_authoritative = True
            statistics_start, statistics_end = window_start, window_end
        else:
            seconds = extent_seconds
            window_source = 'observed_request_bounds'
            window_authoritative = False
            statistics_start, statistics_end = request_start, request_end
        input_rate = _token_rate(
            tokens=input_tokens,
            covered=input_known,
            successful=counts.success,
            seconds=seconds,
            source=tokens.input_source,
        )
        output_rate = _token_rate(
            tokens=output_tokens,
            covered=output_known,
            successful=counts.success,
            seconds=seconds,
            source=tokens.output_source,
        )
        if seconds:
            rates = RateMetrics(
                window_seconds=seconds,
                requests_per_second=counts.success / seconds,
                requests_per_minute=counts.success / seconds * 60.0,
                input_tokens_per_second=input_rate.tokens_per_second,
                output_tokens_per_second=output_rate.tokens_per_second,
                input=input_rate,
                output=output_rate,
                cache=_cache_pairing(paired=cache_paired, successful=counts.success),
                window_source=window_source,
                window_authoritative=window_authoritative,
            )
        else:
            rates = RateMetrics(
                input=input_rate,
                output=output_rate,
                cache=_cache_pairing(paired=cache_paired, successful=counts.success),
                window_source=None,
                window_authoritative=False,
                availability='unavailable',
                reason='no_timed_profiling_requests',
            )
        concurrency: Optional[float] = None
        peak: Optional[int] = None
        if seconds and statistics_start is not None and statistics_end is not None:
            concurrency, peak = _concurrency(
                intervals, window_start=statistics_start, window_end=statistics_end, seconds=seconds
            )
        return RequestAggregation(
            counts=counts,
            ttft=_finish(
                ttft,
                unit='seconds',
                source='aiperf_client_ttft',
                population='successful_profiling_requests',
                empty_reason='no_successful_requests_with_ttft',
            ),
            tpot=_finish(
                tpot,
                unit='seconds',
                source='per_request_itl',
                population='successful_profiling_requests',
                empty_reason='no_successful_requests_with_tpot',
                sources=tpot_sources,
            ),
            e2e=_finish(
                e2e,
                unit='seconds',
                source='aiperf_client_latency',
                population='successful_profiling_requests',
                empty_reason='no_successful_requests_with_latency',
            ),
            tokens=tokens,
            rates=rates,
            observation=observation,
            observed_concurrency=concurrency,
            observed_concurrency_peak=peak,
        )
    finally:
        ttft.close()
        tpot.close()
        e2e.close()


# ---------------------------------------------------------------------------
# Report rows and documents
# ---------------------------------------------------------------------------


class ReportRunDimensions(BaseArgument):
    """The matrix cell a run belongs to; repetitions share every value."""

    deployment_mode: Optional[str] = None
    workload: Optional[str] = None
    message_structure: Optional[str] = None
    input_tokens: Optional[int] = None
    output_token_limit: Optional[int] = None
    target_ratio: Optional[float] = None
    load_mode: Optional[str] = None
    concurrency: Optional[int] = None
    rate: Optional[float] = None
    repetition: Optional[int] = None
    case_id: Optional[str] = None


class ReportRunCounts(BaseArgument):
    """Planned versus observed request counts for one run."""

    planned_requests: int = Field(default=0, ge=0)
    primer_requests: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    success: int = Field(default=0, ge=0)
    error: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    incomplete: int = Field(default=0, ge=0)


class ReportNodeStatistic(BaseArgument):
    """One measured node-metric window value with its provenance boundary."""

    availability: Literal['available', 'partial', 'missing', 'invalid']
    value: Optional[float] = None
    sample_peak: Optional[float] = None
    coverage: Optional[float] = None
    sample_count: Optional[int] = None
    reason: Optional[str] = None
    boundary: Optional[Literal['exact', 'bracketed', 'step', 'unknown']] = None


class ReportNodeHistogram(BaseArgument):
    """A histogram-family node metric summarized over the same window."""

    availability: Literal['available', 'partial', 'missing', 'invalid']
    metric_key: str
    unit: Optional[str] = None
    count: Optional[int] = None
    mean: Optional[float] = None
    p50: Optional[float] = None
    p95: Optional[float] = None
    p99: Optional[float] = None
    boundary: Optional[Literal['exact', 'bracketed', 'step', 'unknown']] = None
    reason: Optional[str] = None


class ReportNodeMetric(BaseArgument):
    """One node/label/engine series, never merged with another node's values."""

    node_id: str
    role: Literal['prefill', 'decode']
    engine: str
    labels: Dict[str, str] = Field(default_factory=dict)
    metric_name: str
    metric_unit: Optional[str] = None
    metric_kind: Optional[str] = None
    window_source: Optional[str] = None
    statistic: Optional[ReportNodeStatistic] = None
    histogram: Optional[ReportNodeHistogram] = None
    reason: Optional[str] = None


class ReportNodeCacheRatio(BaseArgument):
    """vLLM prefix lookup-token ratio, distinct from response cache usage."""

    node_id: str
    role: Literal['prefill', 'decode']
    availability: Literal['available', 'partial', 'missing', 'invalid']
    hit_tokens: Optional[float] = None
    lookup_tokens: Optional[float] = None
    ratio: Optional[float] = None
    reason: Optional[str] = None
    semantics: Optional[str] = None


class ReportRunNodes(BaseArgument):
    """The node observation that already exists for this run, if any."""

    availability: Literal['available', 'partial', 'missing', 'invalid']
    reason: Optional[str] = None
    quality_reasons: List[str] = Field(default_factory=list)
    window_source: Optional[str] = None
    start_ns: Optional[int] = None
    end_ns: Optional[int] = None
    timed_requests: int = Field(default=0, ge=0)
    total_requests: int = Field(default=0, ge=0)
    max_gap_ns: Optional[int] = None
    metrics: List[ReportNodeMetric] = Field(default_factory=list)
    cache_ratios: List[ReportNodeCacheRatio] = Field(default_factory=list)


class ReportRun(BaseArgument):
    """One stable report row, whether or not its data could be read."""

    run_key: str
    run_id: str
    order: int
    archive_identity: str
    dimensions: ReportRunDimensions
    lifecycle: RunLifecycle
    quality: Optional[Quality] = None
    counts: ReportRunCounts
    ttft: Distribution
    tpot: Distribution
    e2e: Distribution
    tokens: TokenSummary
    rates: RateMetrics
    observation: ObservationWindow
    observed_concurrency: Optional[float] = None
    observed_concurrency_peak: Optional[int] = None
    nodes: Optional[ReportRunNodes] = None
    availability: Availability = 'available'
    reason: Optional[str] = None
    quality_reasons: List[str] = Field(default_factory=list)


def empty_run_metrics(reason: str) -> Dict[str, object]:
    """Metrics for a planned run with no readable normalized result."""
    return {
        'ttft': unavailable_distribution(
            unit='seconds', source='aiperf_client_ttft', population='successful_profiling_requests', reason=reason
        ),
        'tpot': unavailable_distribution(
            unit='seconds', source='per_request_itl', population='successful_profiling_requests', reason=reason
        ),
        'e2e': unavailable_distribution(
            unit='seconds', source='aiperf_client_latency', population='successful_profiling_requests', reason=reason
        ),
        'tokens': TokenSummary(),
        'rates': RateMetrics(availability='unavailable', reason=reason),
        'observation': ObservationWindow(),
        'observed_concurrency': None,
        'observed_concurrency_peak': None,
        'nodes': None,
        'availability': 'unavailable',
        'reason': reason,
    }


def dimensions_of(
    *,
    deployment_mode: Optional[str] = None,
    workload: Optional[str] = None,
    message_structure: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_token_limit: Optional[int] = None,
    target_ratio: Optional[float] = None,
    load_mode: Optional[str] = None,
    concurrency: Optional[int] = None,
    rate: Optional[float] = None,
    repetition: Optional[int] = None,
    case_id: Optional[str] = None,
) -> ReportRunDimensions:
    return ReportRunDimensions(
        deployment_mode=deployment_mode,
        workload=workload,
        message_structure=message_structure,
        input_tokens=input_tokens,
        output_token_limit=output_token_limit,
        target_ratio=target_ratio,
        load_mode=load_mode,
        concurrency=concurrency,
        rate=rate,
        repetition=repetition,
        case_id=case_id,
    )


def group_key(dimensions: ReportRunDimensions) -> str:
    """Group by deployment, workload semantics, input/output size and load mode.

    Concurrency or rate stay row dimensions so one group still lets the reader
    compare statistics across load points, exactly as the wide table needs.
    """
    return json.dumps(
        [
            dimensions.deployment_mode,
            dimensions.workload,
            dimensions.message_structure,
            dimensions.input_tokens,
            dimensions.output_token_limit,
            dimensions.target_ratio,
            dimensions.load_mode,
        ],
        separators=(',', ':'),
        ensure_ascii=False,
    )


class ReportGroup(BaseArgument):
    """A stable group description; member rows keep their own percentiles."""

    group_key: str
    dimensions: ReportRunDimensions
    run_keys: List[str]
    run_count: int = Field(ge=0)
    planned_requests: int = Field(ge=0)
    observed_requests: int = Field(ge=0)
    successful_requests: int = Field(ge=0)


def build_groups(runs: List[ReportRun]) -> List[ReportGroup]:
    groups: Dict[str, ReportGroup] = {}
    for run in runs:
        key = group_key(run.dimensions)
        group = groups.get(key)
        if group is None:
            group = ReportGroup(
                group_key=key,
                dimensions=run.dimensions.model_copy(update={'concurrency': None, 'rate': None, 'repetition': None}),
                run_keys=[],
                run_count=0,
                planned_requests=0,
                observed_requests=0,
                successful_requests=0,
            )
            groups[key] = group
        group.run_keys.append(run.run_key)
        group.run_count += 1
        group.planned_requests += run.counts.planned_requests
        group.observed_requests += run.counts.total
        group.successful_requests += run.counts.success
    return [groups[key] for key in sorted(groups)]


class ReportCounts(BaseArgument):
    """Plan-wide counts that never shrink to the currently paged window."""

    planned_runs: int = Field(default=0, ge=0)
    completed_runs: int = Field(default=0, ge=0)
    failed_runs: int = Field(default=0, ge=0)
    cancelled_runs: int = Field(default=0, ge=0)
    not_started_runs: int = Field(default=0, ge=0)
    running_runs: int = Field(default=0, ge=0)
    planned_requests: int = Field(default=0, ge=0)
    observed_requests: int = Field(default=0, ge=0)
    successful_requests: int = Field(default=0, ge=0)


def build_counts(runs: List[ReportRun]) -> ReportCounts:
    counts = ReportCounts(planned_runs=len(runs))
    for run in runs:
        if run.lifecycle == 'completed':
            counts.completed_runs += 1
        elif run.lifecycle == 'error':
            counts.failed_runs += 1
        elif run.lifecycle == 'cancelled':
            counts.cancelled_runs += 1
        elif run.lifecycle == 'not_started':
            counts.not_started_runs += 1
        elif run.lifecycle == 'running':
            counts.running_runs += 1
        counts.planned_requests += run.counts.planned_requests
        counts.observed_requests += run.counts.total
        counts.successful_requests += run.counts.success
    return counts


class ReportCapability(BaseArgument):
    availability: Literal['available', 'partial', 'unavailable', 'unsupported']
    reason: Optional[str] = None


class ReportCapabilities(BaseArgument):
    """What this snapshot can and cannot answer, per data family."""

    client_metrics: ReportCapability
    token_usage: ReportCapability
    cache: ReportCapability
    node_metrics: ReportCapability
    requests: ReportCapability


class ReportHeader(BaseArgument):
    """Snapshot identity and plan-wide description without the run rows."""

    schema_version: Literal['1.0'] = '1.0'
    statistics_version: str = STATISTICS_VERSION
    snapshot_id: str
    created_at: str
    source_kind: SourceKind
    source_identity: str
    task_id: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    quality: Optional[Quality] = None
    quality_reasons: List[str] = Field(default_factory=list)
    phase: Optional[str] = None
    plan_hash: Optional[str] = None
    normalized_hash: Optional[str] = None
    compiler_version: Optional[str] = None
    contract_version: Optional[str] = None
    workload_semantics: Optional[str] = None
    deployment_mode: Optional[str] = None
    deployment_evidence: Optional[str] = None
    declared_max_model_len: Optional[int] = None
    partial: bool = False
    partial_reason: Optional[str] = None
    counts: ReportCounts
    groups: List[ReportGroup] = Field(default_factory=list)
    capabilities: ReportCapabilities


class ReportDocument(ReportHeader):
    """One fixed snapshot; every export and page is a projection of this list."""

    runs: List[ReportRun] = Field(default_factory=list)


class ReportRequestRow(BaseArgument):
    """A request with every measured boundary kept apart."""

    index: int = Field(ge=0)
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    turn_index: Optional[int] = None
    phase: str
    status: Literal['success', 'error', 'cancelled', 'incomplete']
    status_reason: Optional[str] = None
    start_ns: Optional[int] = None
    end_ns: Optional[int] = None
    request_record_elapsed_seconds: Optional[float] = None
    latency_seconds: Optional[float] = None
    ttft_seconds: Optional[float] = None
    tpot_seconds: Optional[float] = None
    tpot_source: Optional[str] = None
    tpot_method: Optional[str] = None
    decode_duration_seconds: Optional[float] = None
    http_total_seconds: Optional[float] = None
    http_duration_seconds: Optional[float] = None
    prompt_tokens: Optional[int] = None
    prompt_tokens_source: Optional[str] = None
    output_tokens: Optional[int] = None
    output_tokens_source: Optional[str] = None
    cached_tokens: Optional[int] = None
    cached_tokens_source: Optional[str] = None
    aiperf_input_sequence_tokens: Optional[int] = None
    aiperf_output_sequence_tokens: Optional[int] = None
    usage_prompt_tokens: Optional[int] = None
    usage_completion_tokens: Optional[int] = None
    usage_total_tokens: Optional[int] = None
    usage_reasoning_tokens: Optional[int] = None
    error_code: Optional[int] = None


def request_row(record: PerfRequestRecord) -> ReportRequestRow:
    """Project one record, resolving TPOT through the shared derivation."""
    itl = resolve_itl(record)
    return ReportRequestRow(
        index=record.index,
        request_id=record.request_id,
        session_id=record.session_id,
        turn_index=record.turn_index,
        phase=record.phase,
        status=record.status,
        status_reason=record.status_reason,
        start_ns=record.start_ns,
        end_ns=record.end_ns,
        request_record_elapsed_seconds=record.request_record_elapsed_seconds,
        latency_seconds=record.latency_seconds,
        ttft_seconds=record.ttft_seconds,
        tpot_seconds=itl.seconds,
        tpot_source=itl.source,
        tpot_method=itl.method,
        decode_duration_seconds=record.decode_duration_seconds,
        http_total_seconds=record.http_total_seconds,
        http_duration_seconds=record.http_duration_seconds,
        prompt_tokens=record.prompt_tokens,
        prompt_tokens_source=record.prompt_tokens_source,
        output_tokens=record.output_tokens,
        output_tokens_source=record.output_tokens_source,
        cached_tokens=record.cached_tokens,
        cached_tokens_source=record.cached_tokens_source,
        aiperf_input_sequence_tokens=record.aiperf_input_sequence_tokens,
        aiperf_output_sequence_tokens=record.aiperf_output_sequence_tokens,
        usage_prompt_tokens=record.usage_prompt_tokens,
        usage_completion_tokens=record.usage_completion_tokens,
        usage_total_tokens=record.usage_total_tokens,
        usage_reasoning_tokens=record.usage_reasoning_tokens,
        error_code=record.error_code,
    )


class ReportChartPoint(BaseArgument):
    run_key: str
    run_id: str
    order: int
    dimensions: ReportRunDimensions
    value: Optional[float] = None
    availability: PointAvailability = 'available'
    reason: Optional[str] = None


class ReportChartSeries(BaseArgument):
    """One facet value, load mode and repetition; the chart never mixes output
    limits and never joins two independent repetition efforts into one line."""

    facet: str
    facet_dimensions: ReportRunDimensions
    load_mode: Optional[str] = None
    repetition: Optional[int] = None
    points: List[ReportChartPoint] = Field(default_factory=list)


def snapshot_id_for(source_identity: str, stamp: str) -> str:
    """A snapshot identity derived from the source plus its content stamp."""
    return hashlib.sha256(f'{source_identity}|{STATISTICS_VERSION}|{stamp}'.encode('utf-8')).hexdigest()[:16]
