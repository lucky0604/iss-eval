"""Typed Web responses for portable performance plans."""

from typing import Dict, List, Literal, Optional

from pydantic import Field

from evalscope.perf.plans.alignment import PlanAlignment
from evalscope.perf.plans.compiler import PlanBudgets, PlanRuntimeIdentity
from evalscope.perf.plans.documents import PerfPlanDocument
from evalscope.perf.plans.execution import (
    CacheRatioVerification,
    PerfPlanOutcome,
    PlanRunState,
    PlanWarmupState,
)
from evalscope.perf.plans.preflight import ContextCheck
from evalscope.perf.plans.provenance import EffectiveIdentity

from .common import ApiResponseModel

PlanLifecycle = Literal['reserved', 'queued', 'running', 'completed', 'stopped', 'error', 'lost']
RunLifecycle = Literal['not_started', 'running', 'completed', 'cancelled', 'error']
QualityLevel = Literal['complete', 'partial', 'invalid']


class PlanDiagnosticView(ApiResponseModel):
    """One structured diagnostic; never contains prompts, secrets or local paths."""

    code: str
    severity: Literal['error', 'warning', 'info']
    field_path: str = ''
    case_id: Optional[str] = None
    message_key: str = ''
    message: str = ''
    parameters: dict = Field(default_factory=dict)


class PlanRunSummary(ApiResponseModel):
    """Bounded per-run projection of the compiled matrix."""

    run_id: str
    order: int
    workload_kind: Literal['token_prefix', 'aiperf_synthetic']
    message_structure: Literal['user_only', 'system_and_user']
    case_id: Optional[str] = None
    input_tokens: Optional[int] = None
    target_ratio: Optional[float] = None
    declared_total_input_tokens: Optional[int] = None
    shared_system_tokens: Optional[int] = None
    unique_user_tokens: Optional[int] = None
    concurrency: int
    request_count: int
    output_token_limit: int
    repetition: int
    primer_requests: int


class PerfPlanPreviewResponse(ApiResponseModel):
    """A side-effect-free preview: normalized plan, matrix, budgets, diagnostics and alignment."""

    schema_version: Literal['1.0'] = '1.0'
    contract_version: str
    compiler_version: str
    workload_semantics: Literal['managed_token_prefix', 'aiperf_synthetic_compat']
    intent: Literal['diagnostic', 'benchmark']
    plan_hash: str
    normalized_hash: str
    source_hash: str
    semantic_fingerprint: str
    normalized: PerfPlanDocument
    runs: List[PlanRunSummary]
    total_runs: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0, le=500)
    budgets: PlanBudgets
    diagnostics: List[PlanDiagnosticView]
    alignment: PlanAlignment
    context: List[ContextCheck]
    can_submit: bool
    requires_acknowledgement: List[str] = Field(default_factory=list)
    acknowledged: List[str] = Field(default_factory=list)
    engine_available: bool
    runtime: PlanRuntimeIdentity


class PerfPlanExportResponse(ApiResponseModel):
    """A stable, credential-free serialization that can be imported again."""

    content: str
    plan_hash: str
    normalized_hash: str
    semantic_fingerprint: str
    can_submit: bool
    diagnostics: List[PlanDiagnosticView] = Field(default_factory=list)


class PerfPlanRunView(ApiResponseModel):
    """One planned run as requested (dimensions) and as executed (state).

    The dimensions come from the accepted plan and the state from the manifest,
    so a partially executed matrix shows the real length/concurrency/repetition
    of every point instead of the first run's arguments.
    """

    run_id: str
    order: int
    workload_kind: Literal['token_prefix', 'aiperf_synthetic']
    message_structure: Literal['user_only', 'system_and_user']
    case_id: Optional[str] = None
    input_tokens: Optional[int] = None
    target_ratio: Optional[float] = None
    declared_total_input_tokens: Optional[int] = None
    shared_system_tokens: Optional[int] = None
    unique_user_tokens: Optional[int] = None
    concurrency: int
    formal_requests: int
    primer_requests: int = 0
    output_token_limit: int
    repetition: int
    status: RunLifecycle = 'not_started'
    quality: Optional[QualityLevel] = None
    reason: Optional[str] = None
    normalized: Optional[str] = None
    requests_available: bool = False
    cache_verification: Optional[CacheRatioVerification] = None
    started_ns: Optional[int] = None
    finished_ns: Optional[int] = None

    @classmethod
    def build(cls, run, state: Optional[PlanRunState]) -> 'PerfPlanRunView':
        """Merge one compiled run with its manifest row (if the worker started)."""
        return cls(
            run_id=run.run_id,
            order=run.order,
            workload_kind=run.workload_kind,
            message_structure=run.message_structure,
            case_id=run.case_id,
            input_tokens=run.input_tokens,
            target_ratio=run.target_ratio,
            declared_total_input_tokens=run.declared_total_input_tokens,
            shared_system_tokens=run.shared_system_tokens,
            unique_user_tokens=run.unique_user_tokens,
            concurrency=run.concurrency,
            formal_requests=run.request_count,
            primer_requests=run.primer_requests,
            output_token_limit=run.output_token_limit,
            repetition=run.repetition,
            status=state.status if state else 'not_started',
            quality=state.quality if state else None,
            reason=state.reason if state else None,
            normalized=state.normalized if state else None,
            cache_verification=state.cache_verification if state else None,
            started_ns=state.started_ns if state else None,
            finished_ns=state.finished_ns if state else None,
        )


class PerfPlanArchiveResponse(ApiResponseModel):
    """Archived plan provenance, bounded run page and effective runtime identity."""

    task_id: str
    name: Optional[str] = None
    model: str = ''
    workload_semantics: Optional[Literal['managed_token_prefix', 'aiperf_synthetic_compat']] = None
    contract_version: Optional[str] = None
    compiler_version: Optional[str] = None
    plan_hash: Optional[str] = None
    normalized_hash: Optional[str] = None
    source_hash: Optional[str] = None
    semantic_fingerprint: Optional[str] = None
    state: PlanLifecycle = 'queued'
    state_reason: Optional[str] = None
    status: Optional[Literal['running', 'completed', 'stopped', 'error']] = None
    quality: Optional[QualityLevel] = None
    quality_reasons: List[str] = Field(default_factory=list)
    phase: Optional[str] = None
    current_run: Optional[str] = None
    completed_runs: int = 0
    total_runs: int = 0
    budgets: Optional[PlanBudgets] = None
    context: List[ContextCheck] = Field(default_factory=list)
    warmup: Optional[PlanWarmupState] = None
    runs: List[PerfPlanRunView] = Field(default_factory=list)
    offset: int = 0
    limit: int = 100
    archive: Dict[str, str] = Field(default_factory=dict)
    alignment: Optional[PlanAlignment] = None
    effective: Optional[EffectiveIdentity] = None
    report_available: bool = False


class PerfPlanInvokeResponse(ApiResponseModel):
    """Terminal result of a synchronous plan execution."""

    status: Literal['completed', 'error', 'stopped']
    task_id: str
    result: PerfPlanOutcome


class PerfPlanSubmitResponse(ApiResponseModel):
    """The truthful acceptance answer for one plan submission.

    ``state`` is the state of the task this plan already owns, so an accepted
    retry never pretends a finished run is still queued.
    """

    task_id: str
    state: PlanLifecycle
    duplicate: bool = False
    enqueued_at: Optional[str] = None
    queue_position: Optional[int] = None
    message: Optional[str] = None
