"""Normalized, provenance-aware performance result contracts."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, StrictInt, field_validator, model_validator

from evalscope.utils import BaseArgument


class MetricStatistics(BaseArgument):
    """A metric with explicit unit, scope, source, and observation count."""

    key: str
    unit: str
    source: str
    scope: Literal['request', 'run', 'node_window']
    value: Optional[float] = None
    statistics: Dict[str, float] = Field(default_factory=dict)
    count: Optional[int] = Field(default=None, ge=0)
    availability: Literal['available', 'unavailable'] = 'available'
    reason: Optional[str] = None


class PerfRequestRecord(BaseArgument):
    """A request record without inferred server cache data."""

    index: int = Field(ge=0)
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    turn_index: Optional[int] = None
    phase: str
    status: Literal['success', 'error', 'cancelled', 'incomplete']
    status_reason: Optional[str] = None
    start_ns: Optional[int] = None
    end_ns: Optional[int] = None
    latency_seconds: Optional[float] = None
    ttft_seconds: Optional[float] = None
    prompt_tokens: Optional[int] = None
    prompt_tokens_source: Optional[Literal['server_usage', 'aiperf_estimate']] = None
    output_tokens: Optional[int] = None
    output_tokens_source: Optional[Literal['server_usage', 'aiperf_estimate']] = None
    cached_tokens: Optional[int] = None
    cached_tokens_source: Optional[Literal['server_usage']] = None
    error_code: Optional[int] = None


class PerfRunResult(BaseArgument):
    """A normalized run; raw files remain the source of truth."""

    schema_version: Literal['1.0'] = '1.0'
    engine: Literal['aiperf'] = 'aiperf'
    executor_version: str
    summary_schema: Optional[str] = None
    records_schema: Literal['unversioned'] = 'unversioned'
    server_metrics_schema: Optional[str] = None
    quality: Literal['complete', 'partial', 'invalid']
    quality_reasons: List[str] = Field(default_factory=list)
    profiling_requests: int = Field(ge=0)
    profiling_successes: int = Field(ge=0)
    profiling_errors: int = Field(ge=0)
    profiling_cancelled: Optional[int] = Field(default=None, ge=0)
    profiling_incomplete: Optional[int] = Field(default=None, ge=0)
    profiling_start_ns: Optional[int] = None
    profiling_end_ns: Optional[int] = None
    metrics: List[MetricStatistics] = Field(default_factory=list)


class ImportManifest(BaseArgument):
    """Input and parser identity for an atomically published import."""

    schema_version: Literal['1.0'] = '1.0'
    parser_version: Literal['aiperf-0.13.0-v1', 'aiperf-0.13.0-v2', 'aiperf-0.13.0-v3', 'aiperf-0.13.0-v4'] = (
        'aiperf-0.13.0-v4'
    )
    input_hash: str
    files: Dict[str, str]
    allow_partial_tail: bool = False
    target_identity: Optional[str] = None
    run_file: str = 'run.json'
    requests_file: str = 'requests.db'


class MetricTargetReference(BaseArgument):
    """Explicit node attribution for one configured metrics endpoint."""

    node_id: str = Field(min_length=1)
    role: Literal['prefill', 'decode']
    engine: Literal['vllm', 'sglang']
    url: str
    labels: Dict[str, str] = Field(default_factory=dict)


class AIPerfImportArguments(BaseArgument):
    """Server-owned paths for an offline, repeatable AIPerf import."""

    raw_dir: str
    normalized_dir: str
    executor_version: Literal['0.13.0'] = '0.13.0'
    allow_partial_tail: bool = False
    metrics_required: bool = False
    targets: List[MetricTargetReference] = Field(default_factory=list)


class NodeMetricSample(BaseArgument):
    """One source-attributed raw sample; no request-level PD inference is made."""

    timestamp_ns: int
    node_id: Optional[str] = None
    role: Optional[Literal['prefill', 'decode']] = None
    engine: Optional[Literal['vllm', 'sglang']] = None
    endpoint: str
    metric_name: str
    metric_type: Literal['counter', 'gauge', 'histogram', 'unknown']
    labels: Dict[str, str]
    observation: Dict[str, Any]
    request_sent_ns: Optional[int] = Field(default=None, ge=0)
    scrape_latency_ns: Optional[int] = Field(default=None, ge=0)
    phase_kind: Optional[Literal['warmup', 'profiling']] = None
    scope: Literal['node_window'] = 'node_window'
    semantics: Literal['unverified'] = 'unverified'


class NodeMetricSeriesQuery(BaseArgument):
    """A bounded window for one exactly labelled node metric series."""

    node_id: str = Field(min_length=1, max_length=512)
    metric_name: str = Field(min_length=1, max_length=512)
    labels: Dict[str, str]
    start_ns: Optional[StrictInt] = Field(default=None, ge=0, le=2**63 - 1)
    end_ns: Optional[StrictInt] = Field(default=None, ge=0, le=2**63 - 1)
    max_points: StrictInt = Field(default=500, ge=2, le=2000)

    @field_validator('node_id', 'metric_name')
    @classmethod
    def validate_dimension(cls, value: str) -> str:
        if '\x00' in value:
            raise ValueError('Invalid metric dimension')
        return value

    @field_validator('labels')
    @classmethod
    def validate_labels(cls, labels: Dict[str, str]) -> Dict[str, str]:
        if len(labels) > 64 or any(
            len(key) > 512 or len(value) > 512 or '\x00' in key or '\x00' in value for key, value in labels.items()
        ):
            raise ValueError('Invalid metric labels')
        return labels

    @model_validator(mode='after')
    def validate_window(self) -> 'NodeMetricSeriesQuery':
        if self.start_ns is not None and self.end_ns is not None and self.start_ns > self.end_ns:
            raise ValueError('Invalid metric window')
        return self


class NodeMetricSeries(BaseArgument):
    """Selected raw points, reporting the full matching population and sampling method."""

    total: int = Field(ge=0)
    max_points: int = Field(ge=2, le=2000)
    aggregation: Literal['raw'] = 'raw'
    downsample: Literal['none', 'uniform_index']
    samples: List[NodeMetricSample]


class RequestObservationBounds(BaseArgument):
    """Observed request time extent, distinct from an authoritative phase window."""

    start_ns: Optional[int] = None
    end_ns: Optional[int] = None
    timed_requests: int = Field(ge=0)
    total_requests: int = Field(ge=0)


class CacheObservation(BaseArgument):
    """Server-reported cache-read token totals and their actual coverage."""

    cached_tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    ratio: Optional[float] = None
    observed_requests: int = Field(ge=0)
    successful_requests: Optional[int] = Field(default=None, ge=0)
    source: Optional[Literal['server_usage']] = None
    availability: Literal['available', 'unavailable']
    reason: Optional[str] = None


class RequestLatencyDistribution(BaseArgument):
    """Exact nearest-rank percentiles for observed request latencies."""

    count: int = Field(ge=0)
    mean: Optional[float] = None
    p50: Optional[float] = None
    p95: Optional[float] = None
    p99: Optional[float] = None
    unit: Literal['seconds'] = 'seconds'


class CacheRequestGroup(BaseArgument):
    """A group defined only by the server's request-level cached token count."""

    group: Literal['no_cached_tokens', 'some_cached_tokens']
    count: int = Field(ge=0)
    latency: RequestLatencyDistribution
    ttft: RequestLatencyDistribution


class CacheRequestGroups(BaseArgument):
    """Request-level cache groups and their coverage, without node-level inference."""

    availability: Literal['available', 'unavailable']
    reason: Optional[str] = None
    successful_requests: Optional[int] = None
    observed_requests: int = Field(ge=0)
    groups: List[CacheRequestGroup] = Field(default_factory=list)
