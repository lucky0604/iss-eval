import math
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import AfterValidator, Field

from evalscope.perf.experiments.orchestrator import CacheExperimentOutcome
from evalscope.perf.experiments.planner import CacheExperimentPlan
from evalscope.perf.integrations.aiperf.live import LiveNode
from evalscope.perf.integrations.aiperf.metrics import NodeMetricsAnalysis
from evalscope.perf.integrations.aiperf.preflight import NodePreflight
from evalscope.perf.results.models import (
    CacheObservation,
    CacheRequestGroup,
    NodeMetricSample,
    PerfRequestRecord,
    PerfRunResult,
)

from .common import ApiResponseModel
from .reports import MetricSemantics


def _finite_or_none(value: Optional[float]) -> Optional[float]:
    return value if value is not None and math.isfinite(value) else None


MetricValue = Annotated[Optional[float], AfterValidator(_finite_or_none)]
TableCell = Union[str, MetricValue]


class PerfRunSummary(ApiResponseModel):
    path: str
    model: str
    api_type: str
    dataset: str
    alias: Optional[str] = None
    num_runs: int
    total_requests: int
    success_rate: Optional[float]
    best_rps: Optional[float]
    best_latency: Optional[float]
    avg_input_tokens: Optional[float] = None
    avg_output_tokens: Optional[float] = None
    is_embedding: bool
    has_html: bool
    timestamp: str
    provider: Optional[str] = None
    protocol: Optional[str] = None
    api_host: Optional[str] = None
    concurrency: Optional[List[int]] = None
    engine: Optional[Literal['evalscope', 'aiperf']] = None
    executor_version: Optional[str] = None
    quality: Optional[Literal['complete', 'partial', 'invalid']] = None


class ListPerfRunsResponse(ApiResponseModel):
    runs: List[PerfRunSummary]
    total: int
    metric_semantics: Dict[str, MetricSemantics] = Field(default_factory=dict)


class PerfSummaryColumn(ApiResponseModel):
    key: str
    label: str
    semantics: Optional[MetricSemantics]


class PerfSummaryRow(ApiResponseModel):
    values: Dict[str, MetricValue]
    sample_counts: Dict[str, int]


class PerfDetailResponse(ApiResponseModel):
    path: str
    model: str
    api_type: str
    dataset: str
    alias: Optional[str] = None
    generated_at: str
    basic_info: Dict[str, str]
    configuration: Optional[Dict[str, str]] = None
    summary_columns: List[PerfSummaryColumn]
    summary_rows: List[PerfSummaryRow]
    total_requests: int
    best_config: Dict[str, str]
    recommendations: List[str]
    num_runs: int
    is_embedding: bool
    has_html: bool
    engine: Optional[Literal['evalscope', 'aiperf']] = None
    executor_version: Optional[str] = None
    quality: Optional[Literal['complete', 'partial', 'invalid']] = None
    node_analysis: Optional[NodeMetricsAnalysis] = None
    quality_reasons: List[str] = Field(default_factory=list)


class PerfRunItem(ApiResponseModel):
    dir_name: str
    name: str
    parallel: Optional[int]
    number: int
    rate: Optional[float]
    total_requests: int
    succeed_requests: int
    success_rate: Optional[float]
    num_requests: int
    has_requests: bool
    percentile_columns: List[str]
    percentile_rows: List[List[TableCell]]


class PerfRunsListResponse(ApiResponseModel):
    runs: List[PerfRunItem]
    total: int


class PerfRequestsResponse(ApiResponseModel):
    columns: List[str]
    rows: List[Dict[str, Any]]
    total: int
    page: int
    page_size: int
    has_db: bool


class DeletePerfRunResponse(ApiResponseModel):
    success: bool
    path: str


class PerfEngineCapability(ApiResponseModel):
    """Public availability of a performance execution engine."""

    engine: Literal['evalscope', 'aiperf']
    available: bool
    version: Optional[str] = None
    reason: Optional[str] = None
    protocols: List[str] = Field(default_factory=list)


class PerfEnginesResponse(ApiResponseModel):
    """Available and unavailable execution engines."""

    engines: List[PerfEngineCapability]


class PublicAIPerfOutcome(ApiResponseModel):
    """Single-run outcome without local paths or credentials."""

    engine: Literal['aiperf']
    executor_version: Optional[str] = None
    exit_reason: Literal['completed', 'error', 'cancelled']
    exit_code: Optional[int] = None
    artifact_status: Literal['available', 'partial', 'missing']
    cleanup_confirmed: bool


class PerfInvokeResponse(ApiResponseModel):
    """Typed AIPerf task result, compatible with the shared task monitor."""

    status: Literal['completed', 'error', 'stopped']
    task_id: str
    result: Union[CacheExperimentOutcome, PublicAIPerfOutcome]


class PerfPlanResponse(ApiResponseModel):
    """A side-effect-free cache experiment plan and its limitations."""

    plan: CacheExperimentPlan
    engine_available: bool
    warnings: List[str] = Field(default_factory=list)
    metrics_probe: Literal['not_requested', 'completed'] = 'not_requested'
    nodes: List[NodePreflight] = Field(default_factory=list)


class PerfLiveResponse(ApiResponseModel):
    """Bounded last node scrapes and the current planned scenario, when available."""

    task_id: str
    current_subrun: Optional[str] = None
    target_ratio: Optional[float] = None
    load_mode: Optional[Literal['closed_loop', 'open_loop']] = None
    concurrency: Optional[int] = None
    rate: Optional[float] = None
    repetition: Optional[int] = None
    expected_interval_seconds: Optional[float] = None
    nodes: List[LiveNode] = Field(default_factory=list)


class CacheSubrunState(ApiResponseModel):
    subrun_id: str
    status: Literal['not_started', 'running', 'completed', 'cancelled', 'error']
    quality: Optional[Literal['complete', 'partial', 'invalid']] = None
    scenario: Optional[str] = None


class CacheExperimentState(ApiResponseModel):
    schema_version: Literal['1.0']
    plan_hash: str
    status: Literal['running', 'completed', 'stopped', 'error', 'unknown']
    phase: Optional[str] = None
    last_phase: Optional[str] = None
    current_subrun: Optional[str] = None
    completed_subruns: int = Field(ge=0)
    total_subruns: int = Field(ge=0)
    quality: Optional[Literal['complete', 'partial', 'invalid']] = None
    subruns: List[CacheSubrunState]


class CacheExperimentResponse(ApiResponseModel):
    task_id: str
    plan: CacheExperimentPlan
    state: CacheExperimentState


class CacheExperimentListItem(ApiResponseModel):
    task_id: str
    status: Literal['running', 'completed', 'stopped', 'error', 'unknown']
    quality: Optional[Literal['complete', 'partial', 'invalid']] = None
    completed_subruns: int = Field(ge=0)
    total_subruns: int = Field(ge=0)


class CacheExperimentListResponse(ApiResponseModel):
    experiments: List[CacheExperimentListItem]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0, le=500)


class CacheScenarioResponse(ApiResponseModel):
    subrun_id: str
    status: str
    target_ratio: float
    generated_ratio: Optional[float] = None
    generated_count: int = Field(ge=0)
    input_tokens_per_request: Optional[int] = None
    request_hashes_sha256: Optional[str] = None
    cache: Optional[CacheObservation] = None
    result: Optional[PerfRunResult] = None
    node_analysis: Optional[NodeMetricsAnalysis] = None
    quality_reasons: List[str] = Field(default_factory=list)


class CacheRequestsResponse(ApiResponseModel):
    total: int
    offset: int
    limit: int
    requests: List[PerfRequestRecord]


class CacheRequestGroupsResponse(ApiResponseModel):
    availability: Literal['available', 'unavailable']
    reason: Optional[str] = None
    successful_requests: Optional[int] = None
    observed_requests: int = Field(ge=0)
    groups: List[CacheRequestGroup] = Field(default_factory=list)


class CacheNodeMetricsResponse(ApiResponseModel):
    total: int
    offset: int
    limit: int
    samples: List[NodeMetricSample]
    aggregation: Literal['raw'] = 'raw'
    downsample: Literal['none'] = 'none'
