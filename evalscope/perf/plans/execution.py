"""Execute a compiled performance plan through the existing task lifecycle.

Managed runs reuse the cache-scenario machinery (generated prefix workload,
primer barrier, AIPerf single run, result import). Synthetic runs use the
controlled AIPerf synthetic invocation. Both write into one plan manifest and
never retry a failed run automatically.
"""

import asyncio
import multiprocessing
import time
from pathlib import Path
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import Field

from evalscope.perf.executors.arguments import (
    AIPerfArguments,
    CacheExperiment,
    LoadConfiguration,
    LoadPoint,
    WarmupConfiguration,
)
from evalscope.perf.executors.external import Cancellation
from evalscope.perf.executors.models import ExecutionContext
from evalscope.perf.experiments.prefix_workload import (
    PrefixTokenizer,
    PrefixWorkloadArguments,
    WorkloadCancelled,
    build_prefix_workload,
)
from evalscope.perf.experiments.priming import PrimingResult
from evalscope.perf.experiments.scenario import CacheScenarioArguments, run_cache_scenario
from evalscope.perf.experiments.storage import write_experiment_json
from evalscope.perf.experiments.warmup import WarmupResult, run_warmup
from evalscope.perf.integrations.aiperf.synthetic import SyntheticRunSpec, run_synthetic
from evalscope.perf.results.aiperf_import import ImportCancelled, import_aiperf_run, observed_cache_usage
from evalscope.perf.results.models import AIPerfImportArguments, CacheObservation
from evalscope.utils import BaseArgument
from evalscope.utils.logger import get_logger

from .alignment import PlanAlignment
from .compiler import CompiledPerfPlan, PlannedRun, compile_plan
from .documents import SyntheticWorkload, TokenPrefixWorkload
from .provenance import (
    EffectiveIdentity,
    PlanIdentityError,
    TokenizerIdentity,
    initialize_plan_archive,
    write_effective_identity,
)

logger = get_logger()

RunStatus = Literal['not_started', 'running', 'completed', 'cancelled', 'error']
PlanStatus = Literal['running', 'completed', 'stopped', 'error']
Quality = Literal['complete', 'partial', 'invalid']


class CacheRatioVerification(BaseArgument):
    """Declared-versus-observed prefix reuse for one synthetic run.

    The declared ratio is a property of the generated workload; only a
    server-reported ratio can confirm or refute it. A ratio the server did not
    report is ``unavailable``, never an invented deviation, and it only becomes
    a data-quality defect when the plan explicitly requires the observation.
    """

    declared_ratio: Optional[float] = None
    declared_source: Literal['target_prefix_ratio', 'token_split'] = 'token_split'
    observed_ratio: Optional[float] = None
    delta: Optional[float] = None
    tolerance: float = Field(default=0.05, ge=0, le=1)
    required: bool = False
    status: Literal['verified', 'mismatch', 'unavailable', 'not_applicable']


class PlanRunState(BaseArgument):
    """Progress and artifact references for one planned run."""

    run_id: str
    order: int
    workload_kind: Literal['token_prefix', 'aiperf_synthetic']
    message_structure: Literal['user_only', 'system_and_user']
    concurrency: int
    formal_requests: int
    primer_requests: int = 0
    status: RunStatus = 'not_started'
    quality: Optional[Quality] = None
    scenario: Optional[str] = None
    normalized: Optional[str] = None
    reason: Optional[str] = None
    priming: Optional[PrimingResult] = None
    cache_verification: Optional[CacheRatioVerification] = None
    executor_version: Optional[str] = None
    started_ns: Optional[int] = None
    finished_ns: Optional[int] = None


class PlanWarmupState(BaseArgument):
    """Ordinary warmup outcome, kept out of the formal request counts."""

    status: Literal['skipped', 'completed', 'failed', 'cancelled']
    executor: Literal['evalscope_http'] = 'evalscope_http'
    request_count: int = 0
    completed_requests: int = 0
    failed_requests: int = 0
    concurrency: int = 1
    total_input_tokens: int = 0
    output_token_limit: int = 0
    reason: Optional[str] = None


class PerfPlanManifest(BaseArgument):
    """The single authoritative progress document for one plan execution."""

    schema_version: Literal['1.0'] = '1.0'
    plan_hash: str
    normalized_hash: str
    semantic_fingerprint: str
    workload_semantics: Literal['managed_token_prefix', 'aiperf_synthetic_compat']
    status: PlanStatus = 'running'
    phase: Optional[str] = 'preflight'
    current_run: Optional[str] = None
    completed_runs: int = 0
    total_runs: int = 0
    quality: Optional[Quality] = None
    quality_reasons: List[str] = Field(default_factory=list)
    warmup: Optional[PlanWarmupState] = None
    runs: List[PlanRunState] = Field(default_factory=list)
    archive: Dict[str, str] = Field(default_factory=dict)


class PerfPlanOutcome(BaseArgument):
    """Small terminal result; per-run details stay in the plan manifest."""

    status: Literal['completed', 'stopped', 'error']
    quality: Quality
    plan_hash: str
    completed_runs: int
    total_runs: int
    cleanup_confirmed: bool = True
    manifest: str = 'plan/manifest.json'
    quality_reasons: List[str] = Field(default_factory=list)


class _PlanCancellation:
    """Cancellation that also fires when the supervising parent process dies."""

    def __init__(self, requested: Optional[Cancellation]) -> None:
        self.requested = requested
        self.parent = multiprocessing.parent_process()

    def is_set(self) -> bool:
        return bool(
            (self.requested is not None and self.requested.is_set())
            or (self.parent is not None and not self.parent.is_alive())
        )


def _deployment_mode(compiled: CompiledPerfPlan) -> Literal['single', 'pd']:
    """Map a deployment fact onto the legacy executor switch; it is not topology control."""
    return 'pd' if compiled.normalized.deployment.mode == 'disaggregated' else 'single'


def _arguments(
    compiled: CompiledPerfPlan,
    *,
    run: Optional[PlannedRun] = None,
    cache: Optional[CacheExperiment] = None,
    load: Optional[LoadConfiguration] = None,
) -> AIPerfArguments:
    document = compiled.normalized
    resolved_load = load or LoadConfiguration(
        points=[LoadPoint(concurrency=run.concurrency)] if run else [LoadPoint(concurrency=1)],
        request_count=run.request_count if run else document.load.request_count,
        output_token_limit=run.output_token_limit if run else document.load.output_token_limit,
        repetitions=1,
    )
    return AIPerfArguments(
        model=document.connection.model,
        url=document.connection.url,
        tokenizer_path=document.connection.tokenizer_path,
        stream=document.connection.streaming,
        deployment_mode=_deployment_mode(compiled),
        cache_experiment=cache,
        load=resolved_load,
    )


def display_arguments(compiled: CompiledPerfPlan) -> AIPerfArguments:
    """Return the AIPerf arguments used for task metadata display."""
    return _arguments(compiled, run=compiled.runs[0] if compiled.runs else None)


class PerfPlanRunRequest(BaseArgument):
    """Picklable worker payload; the compiled plan is the immutable authority."""

    plan: CompiledPerfPlan
    source_text: str
    tokenizer_path: str
    alignment: Optional[PlanAlignment] = None
    max_token_pool: int = Field(default=65536, gt=0)


def _managed_cache(compiled: CompiledPerfPlan, run: PlannedRun) -> CacheExperiment:
    document = compiled.normalized
    workload = document.workload
    assert isinstance(workload, TokenPrefixWorkload)
    assert run.input_tokens is not None and run.target_ratio is not None
    return CacheExperiment(
        mode='token_prefix_ratio',
        target_ratios=[run.target_ratio],
        total_input_tokens=run.input_tokens,
        input_token_values=[run.input_tokens],
        prefix_pool_size=workload.prefix_pool_size,
        seed=workload.seed,
        warmup=None,
    )


def _declared_prefix_ratio(run: PlannedRun) -> Tuple[Optional[float], Literal['target_prefix_ratio', 'token_split']]:
    """Return the reuse ratio the plan claims to send, preferring the explicit target."""
    if run.target_ratio is not None:
        return run.target_ratio, 'target_prefix_ratio'
    shared = run.shared_system_tokens or 0
    unique = run.unique_user_tokens or 0
    if shared <= 0 or unique <= 0:
        return None, 'token_split'
    return shared / (shared + unique), 'token_split'


def _verify_synthetic_ratio(
    compiled: CompiledPerfPlan,
    run: PlannedRun,
    observation: Optional[CacheObservation],
) -> CacheRatioVerification:
    """Record whether the server reported the prefix reuse the workload declared.

    AIPerf only controls the request payload; it never reports a hit rate, so
    the declared ratio says what was *sent*, not what was cached. Comparing the
    two is therefore the only way to catch a workload whose prompts were reused
    beyond the declaration, and a missing observation stays an observation gap.
    """
    workload = compiled.normalized.workload
    tolerance = workload.ratio_tolerance if isinstance(workload, SyntheticWorkload) else 0.05
    required = workload.require_observed_ratio if isinstance(workload, SyntheticWorkload) else False
    declared, source = _declared_prefix_ratio(run)
    observed = observation.ratio if observation is not None else None
    verification = CacheRatioVerification(
        declared_ratio=declared,
        declared_source=source,
        observed_ratio=observed,
        tolerance=tolerance,
        required=required,
        status='not_applicable',
    )
    if declared is None or declared <= 0:
        return verification
    if observed is None:
        return verification.model_copy(update={'status': 'unavailable'})
    delta = observed - declared
    return verification.model_copy(
        update={'delta': delta, 'status': 'verified' if abs(delta) <= tolerance else 'mismatch'}
    )


def _synthetic_spec(compiled: CompiledPerfPlan, run: PlannedRun) -> SyntheticRunSpec:
    document = compiled.normalized
    assert run.shared_system_tokens is not None and run.unique_user_tokens is not None
    return SyntheticRunSpec(
        case_id=run.case_id or run.run_id,
        model=document.connection.model,
        url=document.connection.url,
        tokenizer_path=document.connection.tokenizer_path,
        shared_system_tokens=run.shared_system_tokens,
        unique_user_tokens=run.unique_user_tokens,
        output_token_limit=run.output_token_limit,
        request_count=run.request_count,
        concurrency=run.concurrency,
        stream=document.connection.streaming,
    )


def _warmup(
    compiled: CompiledPerfPlan,
    tokenizer: Optional[PrefixTokenizer],
    token_pool: Optional[Sequence[int]],
    tokenizer_id: str,
    template_id: str,
    cancellation: _PlanCancellation,
) -> PlanWarmupState:
    """Send the one-off warmup with the service HTTP client, before the matrix."""
    document = compiled.normalized
    warmup = document.warmup
    if not warmup.enabled:
        return PlanWarmupState(status='skipped')
    if tokenizer is None or token_pool is None:
        raise ValueError('A tokenizer is required to generate ordinary warmup inputs')
    configuration = WarmupConfiguration(
        request_count=warmup.request_count,
        concurrency=warmup.concurrency,
        total_input_tokens=warmup.input_tokens,
        output_token_limit=warmup.output_token_limit,
    )
    workload = build_prefix_workload(
        PrefixWorkloadArguments(
            target_ratio=0,
            total_input_tokens=warmup.input_tokens,
            request_count=warmup.request_count,
            prefix_pool_size=1,
            seed=document.workload.seed if hasattr(document.workload, 'seed') else 42,
            namespace=f'plan-warmup:{compiled.plan_hash}',
            tokenizer_id=tokenizer_id,
            template_id=template_id,
        ),
        tokenizer,
        token_pool,
        cancellation,
    )
    result: WarmupResult = asyncio.run(run_warmup(_arguments(compiled), configuration, workload, cancellation))
    return PlanWarmupState(
        status=result.status,
        request_count=result.request_count,
        completed_requests=result.completed_requests,
        failed_requests=result.failed_requests,
        concurrency=result.concurrency,
        total_input_tokens=result.total_input_tokens,
        output_token_limit=result.output_token_limit,
        reason=result.reason,
    )


class PlanQualitySummary(BaseArgument):
    """Typed lifecycle/quality rollup; a partial result can never be promoted."""

    status: PlanStatus
    quality: Quality
    reasons: List[str] = Field(default_factory=list)


def _unique(values: Sequence[str]) -> List[str]:
    """Keep the specific quality reasons in first-seen order, without duplicates."""
    seen: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def summarize_plan_quality(
    *,
    requested_status: PlanStatus,
    runs: Sequence[PlanRunState],
    reasons: Sequence[str],
    cleanup_confirmed: bool,
    cancelled: bool,
) -> PlanQualitySummary:
    """Roll per-run outcomes into one lifecycle status and one data quality.

    Lifecycle (``completed``/``stopped``/``error``) and data quality
    (``complete``/``partial``/``invalid``) are independent facts. An uncertain
    cleanup is an execution failure whatever the loop reported, a partial run
    keeps the result ``partial`` even when every run finished, and an invalid
    run is never downgraded to ``partial`` by a cancellation.
    """
    collected = _unique(reasons)
    invalid_reasons = [name for name in collected if name.endswith(':invalid')]
    if not cleanup_confirmed:
        # An unconfirmed cleanup is an execution failure, so a finished matrix
        # can be promoted to neither ``completed`` nor ``stopped``.
        return PlanQualitySummary(
            status='error',
            quality='invalid',
            reasons=_unique([*collected, 'cleanup_unconfirmed']),
        )
    if requested_status == 'error':
        return PlanQualitySummary(status='error', quality='invalid', reasons=collected)
    stopped = requested_status == 'stopped' or cancelled
    if invalid_reasons or any(row.quality == 'invalid' for row in runs):
        # Unusable data outranks a cancellation: an intentional stop stays a
        # stop, but the rows it left behind are never reported as merely partial.
        return PlanQualitySummary(
            status='stopped' if stopped else 'error',
            quality='invalid',
            reasons=_unique([*collected, 'cancelled']) if stopped else collected,
        )
    if stopped:
        return PlanQualitySummary(
            status='stopped',
            quality='partial',
            reasons=_unique([*collected, 'cancelled']),
        )
    completed = sum(1 for row in runs if row.status == 'completed')
    if completed != len(runs):
        return PlanQualitySummary(
            status='error',
            quality='invalid',
            reasons=_unique([*collected, 'incomplete_matrix']),
        )
    if collected or any(row.quality == 'partial' for row in runs):
        return PlanQualitySummary(status='completed', quality='partial', reasons=collected)
    return PlanQualitySummary(status='completed', quality='complete', reasons=[])


def _effective_identity(
    compiled: CompiledPerfPlan,
    tokenizer_id: Optional[str],
    template_id: Optional[str],
    tokenizer_path: str,
    runtime_version: Optional[str],
) -> EffectiveIdentity:
    """Describe what actually ran, without touching the accepted plan hash."""
    resolved = tokenizer_id is not None and template_id is not None
    return EffectiveIdentity(
        plan_hash=compiled.plan_hash,
        contract_version=compiled.contract_version,
        compiler_version=compiled.compiler_version,
        tokenizer=TokenizerIdentity(
            status='resolved' if resolved else 'unknown',
            content_hash=tokenizer_id,
            template_hash=template_id,
            path=tokenizer_path if resolved else None,
        ),
        aiperf_version=runtime_version,
        deployment_evidence=compiled.normalized.deployment.evidence,
    )


def _partial_import(raw_dir: str, run_dir: Path, row: PlanRunState) -> None:
    """Preserve a bounded partial tail without inventing metrics.

    The parent process is still watched, but the already-set cancel flag is not
    re-checked: otherwise every record of a cancelled run would abort the very
    import that should preserve it.
    """
    try:
        result = import_aiperf_run(
            AIPerfImportArguments(
                raw_dir=raw_dir,
                normalized_dir=str(run_dir / 'normalized'),
                allow_partial_tail=True,
                metrics_required=False,
                targets=[],
            ),
            cancel_event=_PlanCancellation(None),
        )
    except (OSError, ValueError, ImportCancelled):
        row.normalized = None
        row.quality = 'invalid'
        row.reason = row.reason or 'partial_import_unavailable'
        return
    row.normalized = 'normalized'
    row.quality = 'partial' if result.quality != 'invalid' else 'invalid'


_QUALITY_SEVERITY: Dict[Quality, int] = {'complete': 0, 'partial': 1, 'invalid': 2}


def _degrade_quality(current: Optional[Quality], candidate: Quality) -> Quality:
    """Keep the most severe quality fact; a later check can never promote a run.

    ``complete`` → ``partial`` → ``invalid`` is the only allowed direction, so a
    cache-ratio deviation cannot turn an unusable import into a merely partial
    one, and a missing observation cannot invent a defect.
    """
    if current is None:
        return candidate
    return current if _QUALITY_SEVERITY[current] >= _QUALITY_SEVERITY[candidate] else candidate


def _record_run_quality(row: PlanRunState, outcome_quality: Optional[Quality], reasons: List[str]) -> None:
    """Merge one run's quality into the manifest and keep its specific reasons."""
    if outcome_quality is None:
        return
    row.quality = _degrade_quality(row.quality, outcome_quality)
    if outcome_quality == 'partial':
        reasons.append(f'{row.run_id}:partial')
    elif outcome_quality == 'invalid':
        reasons.append(f'{row.run_id}:invalid')


def run_perf_plan(
    compiled: CompiledPerfPlan,
    context: ExecutionContext,
    *,
    source_text: Optional[str] = None,
    tokenizer: Optional[PrefixTokenizer] = None,
    token_pool: Optional[Sequence[int]] = None,
    tokenizer_id: Optional[str] = None,
    template_id: Optional[str] = None,
    alignment: Optional[PlanAlignment] = None,
    cancel_event: Optional[Cancellation] = None,
) -> PerfPlanOutcome:
    """Run every compiled member serially under one manifest, without implicit retry."""
    fresh = compile_plan(compiled.normalized)
    if fresh.plan_hash != compiled.plan_hash:
        raise PlanIdentityError(
            'The accepted plan hash does not match this compiler version; the submission was not executed. '
            'Preview and submit the plan again.'
        )
    if context.expected_plan_hash is not None and context.expected_plan_hash != compiled.plan_hash:
        raise ValueError('The submitted plan hash does not match the compiled plan')
    if context.runtime is None:
        raise ValueError('AIPerf runtime is not configured')
    needs_tokenizer = compiled.workload_semantics == 'managed_token_prefix' or compiled.normalized.warmup.enabled
    if needs_tokenizer and (tokenizer is None or token_pool is None or tokenizer_id is None or template_id is None):
        raise ValueError(
            'This plan requires a resolved tokenizer identity and chat template before any request is sent'
        )

    root = Path(context.output_dir).resolve(strict=True)
    environment = {
        'task_id': context.task_id,
        'workload_semantics': compiled.workload_semantics,
        'flow': 'evalscope-service',
        'executor': 'aiperf-0.13.0',
    }
    archive = initialize_plan_archive(
        root, compiled, source_text=source_text, alignment=alignment, environment=environment
    )
    effective = _effective_identity(
        compiled,
        tokenizer_id,
        template_id,
        compiled.normalized.connection.tokenizer_path,
        compiled.runtime.runtime_version,
    )
    write_effective_identity(root, effective)
    runs_dir = root / 'runs'
    runs_dir.mkdir(exist_ok=False)
    manifest_path = archive / 'manifest.json'
    rows = [
        PlanRunState(
            run_id=run.run_id,
            order=run.order,
            workload_kind=run.workload_kind,
            message_structure=run.message_structure,
            concurrency=run.concurrency,
            formal_requests=run.request_count,
            primer_requests=run.primer_requests,
        )
        for run in compiled.runs
    ]
    manifest = PerfPlanManifest(
        plan_hash=compiled.plan_hash,
        normalized_hash=compiled.normalized_hash,
        semantic_fingerprint=compiled.semantic_fingerprint,
        workload_semantics=compiled.workload_semantics,
        total_runs=len(rows),
        runs=rows,
        archive={
            'source': 'plan/source.redacted.json',
            'normalized': 'plan/normalized.json',
            'compiled': 'plan/compiled.json',
            'environment': 'plan/environment.json',
            'effective': 'plan/effective.json',
            'alignment': 'plan/alignment.json' if alignment is not None else '',
        },
    )

    def publish() -> None:
        write_experiment_json(manifest_path, manifest.model_dump(mode='json'))

    def observe_version(version: Optional[str]) -> None:
        """Record the AIPerf version each run actually reported."""
        if not version or effective.aiperf_version == version:
            return
        write_effective_identity(root, effective.model_copy(update={'aiperf_version': version}))

    publish()
    cancellation = _PlanCancellation(cancel_event)
    quality_reasons: List[str] = []
    status: str = 'completed'
    cleanup_confirmed = True
    cancelled = False

    try:
        if compiled.normalized.warmup.enabled:
            manifest.phase = 'warmup'
            publish()
            try:
                warmup_state = _warmup(
                    compiled,
                    tokenizer,
                    token_pool,
                    tokenizer_id or '',
                    template_id or '',
                    cancellation,
                )
            except WorkloadCancelled:
                warmup_state = PlanWarmupState(status='cancelled', reason='cancelled')
            manifest.warmup = warmup_state
            publish()
            if warmup_state.status == 'cancelled':
                quality_reasons.append('warmup_cancelled')
                cancelled = True
                status = 'stopped'
            elif warmup_state.status == 'failed':
                quality_reasons.append('warmup_failed')
                if compiled.normalized.warmup.failure_policy == 'abort':
                    status = 'error'

        for index, run in enumerate(compiled.runs if status == 'completed' else []):
            if cancellation.is_set():
                cancelled = True
                status = 'stopped'
                break
            row = rows[index]
            row.status = 'running'
            row.started_ns = time.time_ns()
            manifest.current_run = run.run_id
            manifest.phase = 'preflight'
            publish()
            run_dir = runs_dir / run.run_id
            try:
                run_dir.mkdir(exist_ok=False)
                sub_context = context.model_copy(update={'output_dir': str(run_dir)})
                if run.workload_kind == 'token_prefix':
                    assert tokenizer is not None and token_pool is not None
                    assert run.target_ratio is not None and run.input_tokens is not None
                    sub_arguments = _arguments(compiled, run=run, cache=_managed_cache(compiled, run))
                    scenario = CacheScenarioArguments(
                        target_ratio=run.target_ratio,
                        input_tokens=run.input_tokens,
                        namespace=run.namespace,
                        tokenizer_id=tokenizer_id or '',
                        template_id=template_id or '',
                        primer_timeout_seconds=compiled.normalized.priming.timeout_seconds,
                    )

                    def publish_phase(phase: str) -> None:
                        if phase in ('preflight', 'generating', 'priming', 'profiling', 'draining', 'importing'):
                            manifest.phase = phase
                            publish()

                    outcome = run_cache_scenario(
                        sub_arguments,
                        sub_context,
                        scenario,
                        tokenizer,
                        token_pool,
                        cancellation,
                        on_phase=publish_phase,
                    )
                    row.scenario = f'runs/{run.run_id}/scenario.json'
                    row.priming = outcome.priming
                    if outcome.execution is not None:
                        row.executor_version = outcome.execution.executor_version
                        observe_version(row.executor_version)
                    if outcome.cleanup_confirmed is False:
                        cleanup_confirmed = False
                    if outcome.status == 'cancelled':
                        row.status = 'cancelled'
                        row.quality = 'partial'
                        cancelled = True
                        status = 'stopped'
                    elif outcome.status == 'error':
                        row.status = 'error'
                        row.reason = outcome.reason
                        row.quality = 'invalid'
                        status = 'error'
                        quality_reasons.append(f'{run.run_id}:{outcome.reason or "error"}')
                    else:
                        row.status = 'completed'
                        _record_run_quality(row, outcome.quality, quality_reasons)
                else:
                    spec = _synthetic_spec(compiled, run)
                    outcome = run_synthetic(spec, sub_context, cancellation)
                    row.executor_version = outcome.executor_version
                    observe_version(outcome.executor_version)
                    scenario_path = run_dir / 'scenario.json'
                    payload = {
                        'schema_version': '1.0',
                        'workload_kind': 'aiperf_synthetic',
                        'case_id': run.case_id,
                        'message_structure': 'system_and_user',
                        'shared_system_tokens': run.shared_system_tokens,
                        'unique_user_tokens': run.unique_user_tokens,
                        'declared_total_input_tokens': run.declared_total_input_tokens,
                        'target_prefix_ratio': run.target_ratio,
                        'output_token_limit': run.output_token_limit,
                        'request_count': run.request_count,
                        'concurrency': run.concurrency,
                        'status': outcome.exit_reason,
                        'artifact_status': outcome.artifact_status,
                        'executor': 'aiperf',
                        'executor_version': outcome.executor_version,
                        'cleanup_confirmed': outcome.cleanup_confirmed,
                    }
                    write_experiment_json(scenario_path, payload)
                    row.scenario = f'runs/{run.run_id}/scenario.json'
                    if not outcome.cleanup_confirmed:
                        cleanup_confirmed = False
                    if outcome.exit_reason == 'cancelled':
                        row.status = 'cancelled'
                        row.quality = 'partial'
                        cancelled = True
                        status = 'stopped'
                        if outcome.raw_artifact_dir is not None:
                            _partial_import(outcome.raw_artifact_dir, run_dir, row)
                    elif outcome.exit_reason != 'completed' or outcome.artifact_status == 'missing':
                        row.status = 'error'
                        row.reason = 'synthetic_run_failed'
                        row.quality = 'invalid'
                        status = 'error'
                        quality_reasons.append(f'{run.run_id}:synthetic_run_failed')
                        if outcome.raw_artifact_dir is not None:
                            _partial_import(outcome.raw_artifact_dir, run_dir, row)
                    else:
                        normalized = run_dir / 'normalized'
                        assert outcome.raw_artifact_dir is not None
                        result = import_aiperf_run(
                            AIPerfImportArguments(
                                raw_dir=outcome.raw_artifact_dir,
                                normalized_dir=str(normalized),
                                metrics_required=False,
                                targets=[],
                            ),
                            cancel_event=cancellation,
                        )
                        row.normalized = 'normalized'
                        _record_run_quality(row, result.quality, quality_reasons)
                        for reason in result.quality_reasons:
                            quality_reasons.append(f'{run.run_id}:{reason}')
                        observation = None
                        try:
                            observation = observed_cache_usage(str(normalized))
                            payload['cache'] = observation.model_dump(mode='json')
                        except (OSError, ValueError):
                            observation = None
                        verification = _verify_synthetic_ratio(compiled, run, observation)
                        payload['cache_verification'] = verification.model_dump(mode='json')
                        row.cache_verification = verification
                        if verification.status == 'mismatch':
                            row.quality = _degrade_quality(row.quality, 'partial')
                            quality_reasons.append(f'{run.run_id}:target_ratio_mismatch')
                        elif verification.status == 'unavailable' and verification.required:
                            row.quality = _degrade_quality(row.quality, 'partial')
                            quality_reasons.append(f'{run.run_id}:target_ratio_unverified')
                        payload['quality'] = row.quality
                        write_experiment_json(scenario_path, payload)
                        row.status = 'completed'
            except (WorkloadCancelled, ImportCancelled):
                # A cancelled import is a cancelled run, never an execution error.
                row.status = 'cancelled'
                row.quality = 'partial'
                cancelled = True
                status = 'stopped'
            except Exception as error_value:
                # Log the failure class and run id, never a prompt or credential.
                logger.error(f'Plan run {run.run_id} failed: {type(error_value).__name__}: {error_value}')
                row.status = 'error'
                row.quality = 'invalid'
                row.reason = row.reason or 'run_failed'
                status = 'error'
                quality_reasons.append(f'{run.run_id}:run_failed')
            finally:
                row.finished_ns = time.time_ns()
                manifest.current_run = None
                if row.status == 'completed':
                    manifest.completed_runs += 1
                manifest.phase = None
                publish()
            if status in ('error', 'stopped'):
                break
    finally:
        summary = summarize_plan_quality(
            requested_status=status if status in ('completed', 'stopped', 'error') else 'error',  # type: ignore[arg-type]
            runs=rows,
            reasons=quality_reasons,
            cleanup_confirmed=cleanup_confirmed,
            cancelled=cancelled,
        )
        manifest.status = summary.status
        manifest.phase = None
        manifest.current_run = None
        manifest.quality = summary.quality
        manifest.quality_reasons = summary.reasons
        publish()

    return PerfPlanOutcome(
        status=manifest.status if manifest.status != 'running' else 'error',
        quality=manifest.quality or 'invalid',
        plan_hash=compiled.plan_hash,
        completed_runs=manifest.completed_runs,
        total_runs=len(rows),
        cleanup_confirmed=cleanup_confirmed,
        quality_reasons=manifest.quality_reasons,
    )
