"""Typed contracts for the Task Center, task history and task detail views.

These models are the single source of truth for task classification, status
groups, result availability, data capabilities and the public (already
sanitized) configuration snapshot. The bundled Web UI consumes the generated
TypeScript mirror; do not redefine these shapes on the client.
"""

from typing import Dict, List, Literal, Optional

from pydantic import Field

from .common import ApiResponseModel

TaskKind = Literal['eval', 'perf']

TaskRawStatus = Literal[
    'queued', 'preparing', 'running', 'cancelling', 'finalizing', 'completed', 'stopped', 'error', 'unknown'
]

StatusGroup = Literal['executing', 'queued', 'completed', 'stopped', 'failed', 'uncertain']

PerfTestType = Literal['single', 'sweep', 'cache_experiment', 'unknown']

EngineName = Literal['evalscope', 'aiperf']

QualityLevel = Literal['complete', 'partial', 'invalid']

Availability = Literal['available', 'pending', 'not_configured', 'unsupported', 'unavailable', 'unknown']

CapabilityName = Literal['overview', 'sweep', 'requests', 'node_metrics', 'cache_experiment', 'configuration', 'html']

TimeBasis = Literal['aware', 'legacy', 'unknown']

Completeness = Literal['complete', 'partial']


class AvailabilityState(ApiResponseModel):
    """Whether one result surface can be read, with the reason when it cannot."""

    availability: Availability = 'unknown'
    reason: Optional[str] = None


class ResultAvailability(ApiResponseModel):
    """Readability of a task's result as a whole (independent of its quality)."""

    availability: Availability = 'unknown'
    reason: Optional[str] = None


class ResultCapabilities(ApiResponseModel):
    """Per-surface capability set resolved from the archive, never from the engine label."""

    overview: AvailabilityState = Field(default_factory=lambda: AvailabilityState(availability='unknown'))
    sweep: AvailabilityState = Field(default_factory=lambda: AvailabilityState(availability='unknown'))
    requests: AvailabilityState = Field(default_factory=lambda: AvailabilityState(availability='unknown'))
    node_metrics: AvailabilityState = Field(default_factory=lambda: AvailabilityState(availability='unknown'))
    cache_experiment: AvailabilityState = Field(default_factory=lambda: AvailabilityState(availability='unknown'))
    configuration: AvailabilityState = Field(default_factory=lambda: AvailabilityState(availability='unknown'))
    html: AvailabilityState = Field(default_factory=lambda: AvailabilityState(availability='unknown'))


class EvalConfigSnapshot(ApiResponseModel):
    """Public evaluation settings that are safe to display and copy."""

    snapshot_type: Literal['eval'] = 'eval'
    schema_version: int = 1
    kind: Literal['eval'] = 'eval'
    engine: Optional[EngineName] = None
    model: str = ''
    alias: Optional[str] = None
    endpoint: Optional[str] = None
    datasets: List[str] = Field(default_factory=list)
    limit: Optional[float] = None
    eval_batch_size: Optional[int] = None
    repeats: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_tokens: Optional[int] = None


class NativePerfConfigSnapshot(ApiResponseModel):
    """Public settings of a native EvalScope performance run (single or sweep)."""

    snapshot_type: Literal['perf_native'] = 'perf_native'
    schema_version: int = 1
    kind: Literal['perf'] = 'perf'
    test_type: PerfTestType = 'unknown'
    engine: Literal['evalscope'] = 'evalscope'
    model: str = ''
    alias: Optional[str] = None
    endpoint: Optional[str] = None
    api: Optional[str] = None
    dataset: Optional[str] = None
    parallel: Optional[int | List[int]] = None
    number: Optional[int | List[int]] = None
    rate: Optional[float | List[float]] = None
    max_tokens: Optional[int | List[int]] = None
    min_tokens: Optional[int] = None
    max_prompt_length: Optional[int] = None
    min_prompt_length: Optional[int] = None


class AiPerfSingleConfigSnapshot(ApiResponseModel):
    """Public settings of an AIPerf single load run imported by the service."""

    snapshot_type: Literal['perf_aiperf_single'] = 'perf_aiperf_single'
    schema_version: int = 1
    kind: Literal['perf'] = 'perf'
    test_type: Literal['single'] = 'single'
    engine: Literal['aiperf'] = 'aiperf'
    model: str = ''
    alias: Optional[str] = None
    endpoint: Optional[str] = None
    api_type: str = 'openai'
    load_mode: Optional[str] = None
    concurrency: Optional[int] = None
    rate: Optional[float] = None
    planned_requests: Optional[int] = None
    output_token_limit: Optional[int] = None
    input_sha256: Optional[str] = None


class CacheExperimentConfigSnapshot(ApiResponseModel):
    """Public plan identity of a prefix-cache experiment (credentials excluded)."""

    snapshot_type: Literal['perf_cache_experiment'] = 'perf_cache_experiment'
    schema_version: int = 1
    kind: Literal['perf'] = 'perf'
    test_type: Literal['cache_experiment'] = 'cache_experiment'
    engine: Optional[EngineName] = None
    model: str = ''
    alias: Optional[str] = None
    endpoint: Optional[str] = None
    tokenizer_id: Optional[str] = None
    template_id: Optional[str] = None
    stream: Optional[bool] = None
    deployment_mode: Optional[str] = None
    plan_hash: Optional[str] = None
    configuration_hash: Optional[str] = None
    target_ratios: List[float] = Field(default_factory=list)
    load_modes: List[str] = Field(default_factory=list)
    repetitions: Optional[int] = None
    total_subruns: Optional[int] = None
    node_ids: List[str] = Field(default_factory=list)


class PlanConfigSnapshot(ApiResponseModel):
    """Public identity and mechanics of a portable performance plan.

    Declared deployment facts are labelled as declared; the effective tokenizer
    and template hashes come from the archive and stay ``None`` until a run
    resolves them.
    """

    snapshot_type: Literal['perf_plan'] = 'perf_plan'
    schema_version: int = 1
    kind: Literal['perf'] = 'perf'
    test_type: Literal['cache_experiment'] = 'cache_experiment'
    engine: Literal['aiperf'] = 'aiperf'
    model: str = ''
    alias: Optional[str] = None
    endpoint: Optional[str] = None
    name: Optional[str] = None
    intent: Optional[str] = None
    workload_semantics: Optional[str] = None
    plan_hash: Optional[str] = None
    normalized_hash: Optional[str] = None
    source_hash: Optional[str] = None
    semantic_fingerprint: Optional[str] = None
    compiler_version: Optional[str] = None
    contract_version: Optional[str] = None
    tokenizer_id: Optional[str] = None
    template_id: Optional[str] = None
    aiperf_version: Optional[str] = None
    deployment_mode: Optional[str] = None
    declared_max_model_len: Optional[int] = None
    deployment_evidence: Optional[str] = None
    deployment_measured: Optional[bool] = None
    input_lengths: List[int] = Field(default_factory=list)
    target_ratios: List[float] = Field(default_factory=list)
    concurrencies: List[int] = Field(default_factory=list)
    repetitions: Optional[int] = None
    request_count: Optional[int] = None
    output_token_limit: Optional[int] = None
    warmup_enabled: Optional[bool] = None
    warmup_input_tokens: Optional[int] = None
    warmup_output_token_limit: Optional[int] = None
    primer_timeout_seconds: Optional[float] = None
    total_runs: Optional[int] = None


class TaskSummaryItem(ApiResponseModel):
    """One task as shown in the Task Center list or used for detail headers."""

    task_id: str
    kind: TaskKind
    engine: Optional[EngineName] = None
    test_type: Optional[PerfTestType] = None
    model: str = ''
    alias: Optional[str] = None
    endpoint: Optional[str] = None
    status: TaskRawStatus
    status_group: StatusGroup
    percent: Optional[float] = None
    phase: Optional[str] = None
    current_subrun: Optional[str] = None
    completed_subruns: Optional[int] = None
    total_subruns: Optional[int] = None
    quality: Optional[QualityLevel] = None
    queue_position: Optional[int] = None
    started_at: Optional[str] = None
    enqueued_at: Optional[str] = None
    finished_at: Optional[str] = None
    observed_at: str
    effective_time: Optional[str] = None
    time_basis: TimeBasis = 'unknown'
    result_availability: ResultAvailability = Field(default_factory=ResultAvailability)
    capabilities: ResultCapabilities = Field(default_factory=ResultCapabilities)
    report_url: Optional[str] = None
    html_report_url: Optional[str] = None
    error: Optional[str] = None


class TaskIndexingState(ApiResponseModel):
    """How much of the output root the persistent index has discovered."""

    state: Literal['ready', 'indexing', 'unavailable'] = 'ready'
    scanned: int = 0
    reason: Optional[str] = None


class TaskListResponse(ApiResponseModel):
    """A filtered page of tasks plus the scope it was read from."""

    items: List[TaskSummaryItem]
    total: int
    known_total: int
    completeness: Completeness
    offset: int
    limit: int
    observed_at: str
    revision: int
    indexing: TaskIndexingState
    excluded_without_time: int = 0


class QueueConnectivity(ApiResponseModel):
    backend: str
    reachable: bool
    reason: Optional[str] = None


class TaskSummaryResponse(ApiResponseModel):
    """Global task counts; never derived from a single page of list items."""

    executing: int
    queued: int
    attention: int
    completed_in_range: int
    observed_at: str
    scope: Literal['global', 'global+range'] = 'global'
    completeness: Completeness = 'complete'
    revision: int = 0
    queue: QueueConnectivity
    unknown_timestamp_count: int = 0
    excluded_from_range: int = 0


class TaskDetailResponse(TaskSummaryItem):
    """Task detail: the list projection plus the sanitized configuration snapshot."""

    config_snapshot: Optional[
        EvalConfigSnapshot
        | NativePerfConfigSnapshot
        | AiPerfSingleConfigSnapshot
        | CacheExperimentConfigSnapshot
        | PlanConfigSnapshot
    ] = None
    config_completeness: Literal['full', 'summary_only', 'unavailable'] = 'unavailable'
    logs_available: bool = False
