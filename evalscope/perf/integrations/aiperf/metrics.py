"""Conservative node-window projections for documented upstream metric families.

The names and types below were checked against vLLM v0.10.2 and SGLang
v0.5.2/v0.5.23 source. The deployed versions and topology still require verification.
No value in this module is attributed to an individual benchmark request.
"""

import math
from collections import defaultdict
from typing import Dict, List, Literal, Optional, Sequence

from pydantic import Field

from evalscope.perf.results.models import NodeMetricSample
from evalscope.perf.results.telemetry import (
    HistogramSample,
    HistogramWindow,
    MetricWindow,
    ScalarSample,
    WindowStatistic,
    counter_window,
    gauge_window,
    histogram_window,
)
from evalscope.utils import BaseArgument


class MetricMapping(BaseArgument):
    """One exact upstream metric family and its non-aggregated semantics."""

    engine: Literal['vllm', 'sglang']
    upstream_version: str
    name: str
    key: str
    kind: Literal['gauge', 'counter', 'histogram']
    unit: str
    source_unit: str
    scale: float = 1
    source_url: str


_VLLM = 'https://github.com/vllm-project/vllm/blob/v0.10.2/vllm/v1/metrics/loggers.py'
_SGLANG = 'https://github.com/sgl-project/sglang/blob/v0.5.2/python/sglang/srt/metrics/collector.py'
_SGLANG_NEW = 'https://github.com/sgl-project/sglang/blob/v0.5.23/python/sglang/srt/observability/metrics_collector.py'


def _mapping(
    engine: str,
    name: str,
    key: str,
    kind: str,
    unit: str,
    source_unit: Optional[str] = None,
    scale: float = 1,
    version: Optional[str] = None,
) -> MetricMapping:
    selected = version or ('0.10.2' if engine == 'vllm' else '0.5.2')
    return MetricMapping(
        engine=engine,
        upstream_version=selected,
        name=f'{engine}:{name}',
        key=key,
        kind=kind,
        unit=unit,
        source_unit=source_unit or unit,
        scale=scale,
        source_url=_VLLM if engine == 'vllm' else (_SGLANG_NEW if selected == '0.5.23' else _SGLANG),
    )


# AIPerf 0.13.0 exports Prometheus counter family names without the _total suffix.
MAPPINGS = (
    _mapping('vllm', 'num_requests_waiting', 'waiting_requests', 'gauge', 'requests'),
    _mapping('vllm', 'num_requests_running', 'running_requests', 'gauge', 'requests'),
    _mapping('vllm', 'kv_cache_usage_perc', 'kv_cache_usage', 'gauge', 'ratio'),
    _mapping('vllm', 'prefix_cache_queries', 'prefix_cache_lookup_tokens', 'counter', 'tokens'),
    _mapping('vllm', 'prefix_cache_hits', 'prefix_cache_hit_tokens', 'counter', 'tokens'),
    _mapping('vllm', 'request_queue_time_seconds', 'queue_time', 'histogram', 'seconds'),
    _mapping('vllm', 'request_prefill_time_seconds', 'prefill_time', 'histogram', 'seconds'),
    _mapping('vllm', 'request_decode_time_seconds', 'decode_time', 'histogram', 'seconds'),
    _mapping('sglang', 'num_queue_reqs', 'waiting_requests', 'gauge', 'requests'),
    _mapping('sglang', 'num_running_reqs', 'running_requests', 'gauge', 'requests'),
    _mapping('sglang', 'num_prefill_prealloc_queue_reqs', 'prefill_prealloc_queue', 'gauge', 'requests'),
    _mapping('sglang', 'num_prefill_inflight_queue_reqs', 'prefill_inflight_queue', 'gauge', 'requests'),
    _mapping('sglang', 'num_decode_prealloc_queue_reqs', 'decode_prealloc_queue', 'gauge', 'requests'),
    _mapping('sglang', 'num_decode_transfer_queue_reqs', 'decode_transfer_queue', 'gauge', 'requests'),
    _mapping('sglang', 'cache_hit_rate', 'engine_cache_hit_rate', 'gauge', 'ratio'),
    _mapping('sglang', 'queue_time_s', 'queue_time', 'histogram', 'seconds'),
    _mapping('sglang', 'kv_transfer_latency_ms', 'kv_transfer_latency', 'gauge', 'seconds', 'milliseconds', 0.001),
    _mapping('sglang', 'kv_transfer_speed_gb_s', 'kv_transfer_speed', 'gauge', 'GB/s'),
    _mapping('sglang', 'num_transfer_failed_reqs', 'kv_transfer_failures', 'counter', 'requests'),
    _mapping('sglang', 'cached_tokens', 'cached_prompt_tokens', 'counter', 'tokens'),
    _mapping(
        'sglang', 'num_prefill_bootstrap_queue_reqs', 'prefill_bootstrap_queue', 'gauge', 'requests', version='0.5.23'
    ),
    _mapping('sglang', 'queue_time_seconds', 'queue_time', 'histogram', 'seconds', version='0.5.23'),
    _mapping(
        'sglang',
        'kv_transfer_latency_ms',
        'kv_transfer_latency',
        'histogram',
        'seconds',
        'milliseconds',
        0.001,
        '0.5.23',
    ),
    _mapping('sglang', 'kv_transfer_speed_gb_s', 'kv_transfer_speed', 'histogram', 'GB/s', version='0.5.23'),
)
_INDEX = {(item.engine, item.name, item.kind): item for item in MAPPINGS}
_NAMES = {(item.engine, item.name): item for item in MAPPINGS}


class NodeMetricWindow(BaseArgument):
    """A single node/label series with upstream definition and measured window."""

    node_id: str
    role: Literal['prefill', 'decode']
    engine: Literal['vllm', 'sglang']
    labels: Dict[str, str]
    metric: MetricMapping
    verification: Literal['deployment_unverified'] = 'deployment_unverified'
    attribution: Literal['node_window_shared_traffic'] = 'node_window_shared_traffic'
    window_source: Literal['aiperf_phase', 'observed_request_bounds']
    statistic: Optional[WindowStatistic] = None
    histogram: Optional[HistogramWindow] = None
    reason: Optional[str] = None


class NodeMetricsAnalysis(BaseArgument):
    """Bounded node analysis and the provenance of its profiling interval."""

    availability: Literal['available', 'partial', 'missing', 'invalid']
    reason: Optional[str] = None
    quality_reasons: List[str] = Field(default_factory=list)
    window_source: Optional[Literal['aiperf_phase', 'observed_request_bounds']] = None
    window: Optional[MetricWindow] = None
    timed_requests: int = Field(default=0, ge=0)
    total_requests: int = Field(default=0, ge=0)
    max_gap_ns: int = Field(gt=0)
    series: List[NodeMetricWindow] = Field(default_factory=list)
    cache_ratios: List['NodeCacheRatio'] = Field(default_factory=list)
    scrapes: List['NodeScrapeSummary'] = Field(default_factory=list)


class NodeScrapeSummary(BaseArgument):
    """Observed successful scrapes; failures are unknown when not exported."""

    node_id: str
    observed_successful_scrapes: int = Field(ge=0)
    failed_scrapes: Optional[int] = None
    mean_interval_seconds: Optional[float] = None
    mean_latency_seconds: Optional[float] = None


class NodeCacheRatio(BaseArgument):
    """vLLM lookup-token ratio; never confused with response cache usage."""

    node_id: str
    role: Literal['prefill', 'decode']
    labels: Dict[str, str]
    availability: Literal['available', 'partial', 'missing', 'invalid']
    hit_tokens: Optional[float] = None
    lookup_tokens: Optional[float] = None
    ratio: Optional[float] = None
    reason: Optional[str] = None
    semantics: Literal['vllm_prefix_lookup_tokens'] = 'vllm_prefix_lookup_tokens'


def covers_node_ids(analysis: Optional[NodeMetricsAnalysis], node_ids: Sequence[str]) -> bool:
    """Check whether every configured node has at least one usable window value."""
    if analysis is None or not node_ids:
        return False
    covered = {
        row.node_id
        for row in analysis.series
        if (row.statistic is not None and row.statistic.value is not None)
        or (row.histogram is not None and row.histogram.count is not None and row.histogram.count > 0)
    }
    return set(node_ids).issubset(covered)


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Histogram count is not finite')
    if value < 0 or int(value) != value:
        raise ValueError('Histogram count is not a non-negative integer')
    return int(value)


def project_node_metrics(
    samples: Sequence[NodeMetricSample],
    window: MetricWindow,
    max_gap_ns: int,
    window_source: Literal['aiperf_phase', 'observed_request_bounds'],
) -> List[NodeMetricWindow]:
    """Calculate per-series windows; never merge ranks or infer missing metrics."""
    groups: dict[tuple, list[NodeMetricSample]] = defaultdict(list)
    for sample in samples:
        mapping = _NAMES.get((sample.engine, sample.metric_name))
        if mapping is None or sample.node_id is None or sample.role is None:
            continue
        groups[
            (sample.node_id, sample.role, sample.engine, sample.metric_name, tuple(sorted(sample.labels.items())))
        ].append(sample)
    rows = []
    for (node_id, role, engine, name, labels), items in sorted(groups.items()):
        mapping = _INDEX.get((engine, name, items[0].metric_type), _NAMES[(engine, name)])
        identity = dict(
            node_id=node_id, role=role, engine=engine, labels=dict(labels), metric=mapping, window_source=window_source
        )
        try:
            if any(item.metric_type != mapping.kind for item in items):
                raise ValueError('upstream_metric_type_mismatch')
            items.sort(key=lambda item: item.timestamp_ns)
            if mapping.kind == 'histogram':
                points = []
                for item in items:
                    observation = item.observation
                    raw_buckets = observation.get('buckets')
                    if not isinstance(raw_buckets, dict):
                        raise ValueError('invalid_histogram')
                    buckets = {}
                    for key, value in raw_buckets.items():
                        boundary = float(key) * mapping.scale
                        normalized = '+Inf' if boundary == math.inf else str(boundary)
                        if normalized in buckets:
                            raise ValueError('duplicate_histogram_boundary')
                        buckets[normalized] = _integer(value)
                    total = observation.get('sum')
                    if isinstance(total, bool) or not isinstance(total, (int, float)):
                        raise ValueError('invalid_histogram_sum')
                    points.append(
                        HistogramSample(
                            timestamp_ns=item.timestamp_ns,
                            buckets=buckets,
                            count=_integer(observation.get('count')),
                            sum=total * mapping.scale,
                        )
                    )
                result = histogram_window(points, window, max_gap_ns, mapping.unit, mapping.key)
                rows.append(NodeMetricWindow(**identity, histogram=result))
            else:
                points = []
                for item in items:
                    value = item.observation.get('value')
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        raise ValueError('invalid_scalar')
                    if value < 0 or (mapping.unit == 'ratio' and value > 1):
                        raise ValueError('invalid_scalar_range')
                    points.append(ScalarSample(timestamp_ns=item.timestamp_ns, value=value * mapping.scale))
                statistic = (
                    counter_window(points, window, max_gap_ns)
                    if mapping.kind == 'counter'
                    else gauge_window(points, window, max_gap_ns)
                )
                rows.append(NodeMetricWindow(**identity, statistic=statistic))
        except (ValueError, TypeError, OverflowError) as error:
            rows.append(NodeMetricWindow(**identity, reason=str(error) or 'invalid_metric_series'))
    return rows


def analyze_node_metrics(normalized_dir: str, max_gap_ns: int, limit: int = 100000) -> NodeMetricsAnalysis:
    """Analyze persisted samples with an explicit phase or observed-request window."""
    from evalscope.perf.results.aiperf_import import load_aiperf_run, query_node_metrics, request_observation_bounds

    if type(max_gap_ns) is not int or max_gap_ns <= 0 or type(limit) is not int or not 1 <= limit <= 100000:
        raise ValueError('Invalid metric analysis limits')
    result = load_aiperf_run(normalized_dir)
    bounds = request_observation_bounds(normalized_dir)
    common = dict(timed_requests=bounds.timed_requests, total_requests=bounds.total_requests, max_gap_ns=max_gap_ns)
    if 'parser_version_outdated' in result.quality_reasons:
        return NodeMetricsAnalysis(availability='missing', reason='parser_version_outdated', **common)
    if result.profiling_start_ns is not None and result.profiling_end_ns is not None:
        start, end, source = result.profiling_start_ns, result.profiling_end_ns, 'aiperf_phase'
    elif bounds.start_ns is not None and bounds.end_ns is not None:
        start, end, source = bounds.start_ns, bounds.end_ns, 'observed_request_bounds'
    else:
        return NodeMetricsAnalysis(availability='missing', reason='profiling_window_unavailable', **common)
    if end <= start:
        return NodeMetricsAnalysis(availability='invalid', reason='profiling_window_invalid', **common)
    window = MetricWindow(start_ns=start, end_ns=end)
    total, batch = query_node_metrics(normalized_dir, limit=500)
    if total > limit:
        return NodeMetricsAnalysis(
            availability='invalid', reason='node_sample_limit_exceeded', window=window, window_source=source, **common
        )
    samples = list(batch)
    while len(samples) < total:
        _, batch = query_node_metrics(normalized_dir, offset=len(samples), limit=500)
        if not batch:
            return NodeMetricsAnalysis(
                availability='invalid',
                reason='node_samples_inconsistent',
                window=window,
                window_source=source,
                **common,
            )
        samples.extend(batch)
    series = project_node_metrics(samples, window, max_gap_ns, source)
    if not series:
        return NodeMetricsAnalysis(
            availability='missing', reason='no_documented_node_series', window=window, window_source=source, **common
        )
    usable = any(
        (item.statistic is not None and item.statistic.value is not None)
        or (item.histogram is not None and item.histogram.count is not None)
        for item in series
    )
    quality_reasons = ['deployment_version_unverified', 'node_window_shared_traffic']
    if source == 'observed_request_bounds':
        quality_reasons.append('observed_request_bounds_not_phase_window')
    if bounds.timed_requests != bounds.total_requests:
        quality_reasons.append('request_timing_coverage_incomplete')
    return NodeMetricsAnalysis(
        availability='partial' if usable else 'missing',
        reason='deployment_version_and_attribution_unverified' if usable else 'no_usable_series_in_window',
        quality_reasons=quality_reasons,
        scrapes=summarize_scrapes(samples),
        window=window,
        window_source=source,
        series=series,
        cache_ratios=cache_lookup_ratios(series),
        **common,
    )


def summarize_scrapes(samples: Sequence[NodeMetricSample]) -> List[NodeScrapeSummary]:
    """Deduplicate metric families within each observed endpoint scrape."""
    nodes: dict[str, dict[int, Optional[int]]] = defaultdict(dict)
    for sample in samples:
        if sample.node_id is not None:
            nodes[sample.node_id][sample.timestamp_ns] = sample.scrape_latency_ns
    results = []
    for node_id, scrapes in sorted(nodes.items()):
        timestamps = sorted(scrapes)
        intervals = [b - a for a, b in zip(timestamps, timestamps[1:])]
        latencies = [value for value in scrapes.values() if value is not None]
        results.append(
            NodeScrapeSummary(
                node_id=node_id,
                observed_successful_scrapes=len(timestamps),
                mean_interval_seconds=sum(intervals) / len(intervals) / 1e9 if intervals else None,
                mean_latency_seconds=sum(latencies) / len(latencies) / 1e9 if latencies else None,
            )
        )
    return results


def cache_lookup_ratios(series: Sequence[NodeMetricWindow]) -> List[NodeCacheRatio]:
    """Pair matching vLLM token counters only when their boundaries agree."""
    groups: dict[tuple, dict[str, NodeMetricWindow]] = defaultdict(dict)
    for item in series:
        if item.engine == 'vllm' and item.metric.key in ('prefix_cache_hit_tokens', 'prefix_cache_lookup_tokens'):
            groups[(item.node_id, item.role, tuple(sorted(item.labels.items())))][item.metric.key] = item
    results = []
    for (node_id, role, labels), pair in sorted(groups.items()):
        identity = dict(node_id=node_id, role=role, labels=dict(labels))
        hits = pair.get('prefix_cache_hit_tokens')
        queries = pair.get('prefix_cache_lookup_tokens')
        if hits is None or queries is None or hits.statistic is None or queries.statistic is None:
            results.append(NodeCacheRatio(**identity, availability='missing', reason='counter_pair_unavailable'))
            continue
        h, q = hits.statistic, queries.statistic
        if 'invalid' in (h.availability, q.availability):
            results.append(NodeCacheRatio(**identity, availability='invalid', reason='invalid_cache_counter'))
        elif (
            h.value is None
            or q.value is None
            or (h.source_start_ns, h.source_end_ns) != (q.source_start_ns, q.source_end_ns)
        ):
            results.append(NodeCacheRatio(**identity, availability='missing', reason='counter_window_mismatch'))
        elif q.value <= 0 or h.value < 0 or h.value > q.value:
            results.append(
                NodeCacheRatio(
                    **identity,
                    availability='invalid',
                    reason='invalid_cache_token_counts',
                    hit_tokens=h.value,
                    lookup_tokens=q.value,
                )
            )
        else:
            results.append(
                NodeCacheRatio(
                    **identity,
                    availability='partial' if 'partial' in (h.availability, q.availability) else 'available',
                    hit_tokens=h.value,
                    lookup_tokens=q.value,
                    ratio=h.value / q.value,
                )
            )
    return results
