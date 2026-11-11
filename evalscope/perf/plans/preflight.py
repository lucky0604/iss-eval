"""Offline plan preflight: context budget, load adequacy, alignment and acknowledgement.

Everything here is arithmetic over the compiled plan. No tokenizer is loaded,
no socket is opened and no request is generated, so the API may call it before
any task directory exists.
"""

from typing import Dict, List, Literal, Optional, Tuple

from pydantic import Field

from evalscope.utils import BaseArgument
from evalscope.utils.uri_utils import sanitize_endpoint

from .alignment import (
    AlignmentDimension,
    LegacySourceFacts,
    PlanAlignment,
    overall_status,
)
from .compiler import CompiledPerfPlan, PlanRuntimeIdentity
from .diagnostics import PlanDiagnostic, acknowledgement_codes, error, info, warning
from .documents import SyntheticWorkload, TokenPrefixWorkload


class ContextCheck(BaseArgument):
    """One distinct context judgement, exposed so the UI can explain the basis."""

    run_id: str
    case_id: Optional[str] = None
    required_tokens: Optional[int] = None
    limit_tokens: Optional[int] = None
    overhead: Literal['included', 'known', 'unknown'] = 'unknown'
    status: Literal['fits', 'overflow', 'unknown'] = 'unknown'


class PreflightOutcome(BaseArgument):
    """A compiled plan plus preflight diagnostics, context evidence and alignment."""

    plan: CompiledPerfPlan
    alignment: PlanAlignment
    context: List[ContextCheck] = Field(default_factory=list)
    can_submit: bool
    requires_acknowledgement: List[str] = Field(default_factory=list)


def _distinct_context(
    plan: CompiledPerfPlan, limit: Optional[int], overhead: Optional[int]
) -> Tuple[List[ContextCheck], List[PlanDiagnostic]]:
    checks: List[ContextCheck] = []
    diagnostics: List[PlanDiagnostic] = []
    seen: set[Tuple[Optional[str], Optional[int], Optional[float], Optional[int]]] = set()
    for run in plan.runs:
        key = (run.case_id, run.input_tokens, run.target_ratio, run.declared_total_input_tokens)
        if key in seen:
            continue
        seen.add(key)
        if limit is None:
            checks.append(ContextCheck(run_id=run.run_id, case_id=run.case_id, overhead='unknown', status='unknown'))
            continue
        if run.workload_kind == 'token_prefix':
            required = (run.input_tokens or 0) + run.output_token_limit
            status = 'fits' if required <= limit else 'overflow'
            checks.append(
                ContextCheck(
                    run_id=run.run_id,
                    case_id=run.case_id,
                    required_tokens=required,
                    limit_tokens=limit,
                    overhead='included',
                    status=status,
                )
            )
            if status == 'overflow':
                diagnostics.append(
                    error(
                        'context_overflow',
                        f'Input {run.input_tokens} + output {run.output_token_limit} = {required} tokens exceeds the '
                        f'declared context limit {limit}; the templated prompt already includes the chat template.',
                        'workload.input_lengths',
                        run.case_id,
                        input_tokens=run.input_tokens,
                        output_token_limit=run.output_token_limit,
                        required_tokens=required,
                        limit_tokens=limit,
                    )
                )
            continue
        base = (run.shared_system_tokens or 0) + (run.unique_user_tokens or 0) + run.output_token_limit
        required = base + (overhead or 0)
        if base > limit:
            status = 'overflow'
            diagnostics.append(
                error(
                    'context_overflow',
                    f'Shared {run.shared_system_tokens} + unique {run.unique_user_tokens} + output '
                    f'{run.output_token_limit} = {base} tokens already exceeds the declared context limit {limit}; '
                    'message wrapping overhead is not included in this lower bound.',
                    'workload.cases',
                    run.case_id,
                    shared_system_tokens=run.shared_system_tokens,
                    unique_user_tokens=run.unique_user_tokens,
                    output_token_limit=run.output_token_limit,
                    required_tokens=base,
                    limit_tokens=limit,
                )
            )
        elif overhead is None:
            status = 'unknown'
        else:
            status = 'fits' if required <= limit else 'overflow'
            if status == 'overflow':
                diagnostics.append(
                    error(
                        'context_overflow',
                        f'Messages + template overhead {overhead} + output {run.output_token_limit} = {required} '
                        f'tokens exceeds the declared context limit {limit}.',
                        'workload.cases',
                        run.case_id,
                        required_tokens=required,
                        limit_tokens=limit,
                    )
                )
        checks.append(
            ContextCheck(
                run_id=run.run_id,
                case_id=run.case_id,
                required_tokens=required,
                limit_tokens=limit,
                overhead='known' if overhead is not None else 'unknown',
                status=status,
            )
        )
    return checks, diagnostics


def _warmup_context(
    plan: CompiledPerfPlan, limit: Optional[int]
) -> Tuple[Optional[ContextCheck], List[PlanDiagnostic]]:
    """Judge the ordinary warmup as its own context requirement.

    Warmup input lengths are rendered through the same chat template as a
    formal managed run, so ``input_tokens`` already includes the template and
    ``input + output`` is the complete requirement. Disabled warmup adds no
    check at all, which keeps a formal overflow the only reason to reject.
    """
    warmup = plan.normalized.warmup
    if not warmup.enabled:
        return None, []
    required = warmup.input_tokens + warmup.output_token_limit
    if limit is None:
        return (
            ContextCheck(
                run_id='warmup',
                required_tokens=required,
                overhead='included',
                status='unknown',
            ),
            [],
        )
    status = 'fits' if required <= limit else 'overflow'
    check = ContextCheck(
        run_id='warmup',
        required_tokens=required,
        limit_tokens=limit,
        overhead='included',
        status=status,
    )
    if status == 'fits':
        return check, []
    diagnostic = error(
        'context_overflow',
        f'Warmup input {warmup.input_tokens} + output {warmup.output_token_limit} = {required} tokens exceeds the '
        f'declared context limit {limit}; the render already includes the chat template.',
        'warmup.input_tokens',
        input_tokens=warmup.input_tokens,
        output_token_limit=warmup.output_token_limit,
        required_tokens=required,
        limit_tokens=limit,
    )
    return check, [diagnostic]


def _compare_source(plan: CompiledPerfPlan, source: LegacySourceFacts) -> PlanAlignment:
    """Compare parameters and semantics against the migration source."""
    document = plan.normalized
    different: List[str] = []
    if source.model and source.model != document.connection.model:
        different.append(f'model {source.model} -> {document.connection.model}')
    if source.url and sanitize_endpoint(source.url) != sanitize_endpoint(document.connection.url):
        different.append(f'endpoint {sanitize_endpoint(source.url)} -> {sanitize_endpoint(document.connection.url)}')
    if source.tokenizer and source.tokenizer != document.connection.tokenizer_path:
        different.append('tokenizer path differs from the source script default')
    if source.endpoint_type and source.endpoint_type != 'chat':
        different.append(f'endpoint type {source.endpoint_type} is not reproduced')
    if source.streaming is not None and source.streaming != document.connection.streaming:
        different.append('streaming differs from the source script')
    if source.output_tokens is not None and source.output_tokens != document.load.output_token_limit:
        different.append(f'output tokens {source.output_tokens} -> {document.load.output_token_limit}')
    if source.request_count is not None and source.request_count != document.load.request_count:
        different.append(f'request count {source.request_count} -> {document.load.request_count}')
    source_concurrency = list(source.concurrencies)
    plan_concurrency = [point.concurrency for point in document.load.points]
    if source_concurrency and source_concurrency != plan_concurrency:
        different.append(f'concurrency matrix {source_concurrency} -> {plan_concurrency}')
    if source.repetitions is not None and source.repetitions != document.load.repetitions:
        different.append(f'repetitions {source.repetitions} -> {document.load.repetitions}')
    if document.warmup.enabled:
        if source.warmup_request_count is not None and source.warmup_request_count != document.warmup.request_count:
            different.append(f'warmup requests {source.warmup_request_count} -> {document.warmup.request_count}')
        if source.warmup_input_tokens is not None and source.warmup_input_tokens != document.warmup.input_tokens:
            different.append(f'warmup input {source.warmup_input_tokens} -> {document.warmup.input_tokens}')
        if (
            source.warmup_output_tokens is not None
            and source.warmup_output_tokens != document.warmup.output_token_limit
        ):
            different.append(f'warmup output {source.warmup_output_tokens} -> {document.warmup.output_token_limit}')
        if source.warmup_concurrency is not None and source.warmup_concurrency != document.warmup.concurrency:
            different.append(f'warmup concurrency {source.warmup_concurrency} -> {document.warmup.concurrency}')
    elif source.warmup_request_count:
        different.append('ordinary warmup is disabled in the plan')

    parameters = AlignmentDimension(
        dimension='parameters',
        status='different' if different else 'matched',
        reasons=different or ['declared values, matrix and warmup match the source script'],
    )
    if not different and source.order == 'concurrency_then_input' and document.execution.order != 'load_then_input':
        parameters.status = 'different'
        parameters.reasons = ['execution order is not the source concurrency-then-input order']

    request_reasons = [
        'system/user roles and shared-prefix split are described explicitly, but AIPerf 0.13.0 synthetic semantics '
        'were not verified against a real runtime here',
        'chat template, BOS/EOS and output-forcing behaviour are not pinned by the source script',
    ]
    request_semantics = AlignmentDimension(dimension='request_semantics', status='unknown', reasons=request_reasons)
    cache = AlignmentDimension(
        dimension='cache',
        status='unknown',
        reasons=['routing coverage across replicas is unknown; a target ratio is not an observed hit rate'],
    )
    environment = AlignmentDimension(
        dimension='environment',
        status='unknown',
        reasons=['model weights, engine image and deployment topology are declared, not measured by this plan'],
    )
    version = AlignmentDimension(
        dimension='version',
        status='unknown' if not source.runtime_version else 'matched',
        reasons=[f'source AIPerf version: {source.runtime_version or "unknown"}'],
    )
    statistics = AlignmentDimension(
        dimension='statistics',
        status='unknown',
        reasons=['sample adequacy is reported per result, not asserted by the plan'],
    )
    dimensions = [parameters, request_semantics, cache, environment, version, statistics]
    return PlanAlignment(source=source.script_name, overall=overall_status(dimensions), dimensions=dimensions)


def _standalone_alignment(plan: CompiledPerfPlan) -> PlanAlignment:
    reasons = ['no migration source snapshot was supplied for comparison']
    dimensions = [
        AlignmentDimension(dimension='parameters', status='unknown', reasons=reasons),
        AlignmentDimension(
            dimension='request_semantics',
            status='unknown',
            reasons=['message structure is compiled from the plan; the runtime was not exercised by this preflight'],
        ),
        AlignmentDimension(dimension='cache', status='unknown', reasons=reasons),
        AlignmentDimension(dimension='environment', status='unknown', reasons=reasons),
        AlignmentDimension(dimension='version', status='unknown', reasons=['runtime version is checked at execution']),
        AlignmentDimension(dimension='statistics', status='unknown', reasons=reasons),
    ]
    return PlanAlignment(overall=overall_status(dimensions), dimensions=dimensions)


def preflight_plan(
    plan: CompiledPerfPlan,
    *,
    template_overhead: Optional[int] = None,
    source: Optional[LegacySourceFacts] = None,
    runtime: Optional[PlanRuntimeIdentity] = None,
) -> PreflightOutcome:
    """Evaluate every configured point before any request is sent."""
    document = plan.normalized
    limit = document.deployment.declared_max_model_len
    diagnostics: List[PlanDiagnostic] = list(plan.diagnostics)

    checks, context_diagnostics = _distinct_context(plan, limit, template_overhead)
    warmup_check, warmup_diagnostics = _warmup_context(plan, limit)
    if warmup_check is not None:
        checks = [*checks, warmup_check]
    diagnostics.extend(context_diagnostics)
    diagnostics.extend(warmup_diagnostics)
    if limit is None:
        diagnostics.append(
            error(
                'context_limit_unknown',
                'The served model context limit is not declared; an unknown limit cannot prove capacity for any '
                'configured point.',
                'deployment.declared_max_model_len',
            )
        )
    elif document.load.output_token_limit >= limit:
        diagnostics.append(
            error(
                'output_budget_exceeded',
                f'An output limit of {document.load.output_token_limit} tokens leaves no room inside the declared '
                f'context limit {limit}.',
                'load.output_token_limit',
            )
        )

    below: Dict[int, PlanDiagnostic] = {}
    small: Dict[int, PlanDiagnostic] = {}
    for point in document.load.points:
        if document.load.request_count < point.concurrency:
            below[point.concurrency] = (error if document.intent == 'benchmark' else warning)(
                'request_count_below_concurrency',
                f'{document.load.request_count} requests cannot reach concurrency {point.concurrency}; '
                f'{point.concurrency - document.load.request_count} slots would never be occupied.',
                'load.request_count',
                concurrency=point.concurrency,
                request_count=document.load.request_count,
            )
        elif document.intent == 'benchmark' and document.load.request_count < 2 * point.concurrency:
            small[point.concurrency] = warning(
                'sample_budget_small',
                f'{document.load.request_count} requests at concurrency {point.concurrency} leave fewer than two '
                'observations per slot; completion is not statistical adequacy.',
                'load.request_count',
                concurrency=point.concurrency,
                request_count=document.load.request_count,
            )
    diagnostics.extend(below.values())
    diagnostics.extend(small.values())

    if document.warmup.enabled and plan.budgets.warmup_output_tokens > plan.budgets.formal_output_tokens:
        diagnostics.append(
            warning(
                'warmup_budget_large',
                f'Warmup output budget {plan.budgets.warmup_output_tokens} tokens exceeds the formal budget '
                f'{plan.budgets.formal_output_tokens}.',
                'warmup',
                warmup_output_tokens=plan.budgets.warmup_output_tokens,
                formal_output_tokens=plan.budgets.formal_output_tokens,
            )
        )
    if document.deployment.routing_coverage == 'unknown':
        diagnostics.append(
            warning(
                'routing_coverage_unknown',
                'Routing across replicas is unknown; a primer cannot prove every replica is warm and a target ratio '
                'is not an observed hit rate.',
                'deployment.routing_coverage',
            )
        )
    workload = document.workload
    if isinstance(workload, TokenPrefixWorkload) and workload.prefix_pool_size > document.load.request_count:
        diagnostics.append(
            error(
                'invalid_value',
                f'The prefix pool holds {workload.prefix_pool_size} shared prefixes but each run sends only '
                f'{document.load.request_count} requests; the primer could not populate the pool.',
                'workload.prefix_pool_size',
                prefix_pool_size=workload.prefix_pool_size,
                request_count=document.load.request_count,
            )
        )
    if isinstance(workload, SyntheticWorkload):
        for case in workload.cases:
            split = case.shared_system_tokens + case.unique_user_tokens
            if split != case.declared_total_input_tokens:
                diagnostics.append(
                    warning(
                        'legacy_rounding_difference',
                        f'Declared total {case.declared_total_input_tokens} differs from shared + unique = {split} '
                        f'({case.shared_system_tokens} + {case.unique_user_tokens}); the explicit integers are kept.',
                        'workload.cases',
                        case.case_id,
                        declared_total=case.declared_total_input_tokens,
                        shared=case.shared_system_tokens,
                        unique=case.unique_user_tokens,
                        difference=split - case.declared_total_input_tokens,
                    )
                )
        diagnostics.append(
            warning(
                'synthetic_semantics_unverified',
                'AIPerf 0.13.0 synthetic request construction was not verified in this build environment; the '
                'compiled plan and its flag set follow the migration source and are re-checked by the worker.',
                'workload.kind',
            )
        )
    elif isinstance(workload, TokenPrefixWorkload) and template_overhead is None:
        diagnostics.append(
            info(
                'context_overhead_unknown',
                'Managed input lengths are treated as the complete templated prompt; the tokenizer template is '
                're-validated by the worker before requests are sent.',
                'workload.input_lengths',
            )
        )
    alignment = _compare_source(plan, source) if source is not None else _standalone_alignment(plan)
    if source is not None:
        if not source.runtime_version:
            diagnostics.append(
                warning(
                    'legacy_version_unknown',
                    'The source script did not record an AIPerf version; historical equivalence stays unknown.',
                    'source.runtime_version',
                )
            )
        parameters = alignment.by_dimension['parameters']
        if parameters.status == 'different':
            diagnostics.append(
                warning(
                    'legacy_source_differs',
                    'The plan does not reproduce the source script: ' + '; '.join(parameters.reasons),
                    'connection',
                )
            )
    if template_overhead is not None and any(check.status == 'unknown' for check in checks):
        diagnostics.append(
            info(
                'context_overhead_unknown',
                f'A template overhead of {template_overhead} tokens was applied to every synthetic case.',
                'workload.cases',
            )
        )

    runtime_identity = runtime or plan.runtime
    if runtime_identity.runtime_status == 'unavailable':
        # Plan validity and runtime availability are separate facts: a valid plan
        # can still be refused with 503 at submit time when the engine is missing.
        diagnostics.append(
            warning(
                'runtime_unavailable',
                runtime_identity.reason or 'AIPerf 0.13.0 runtime is unavailable',
                'runtime',
            )
        )
    if runtime_identity.runtime_status == 'available' and runtime_identity.runtime_version not in (None, '0.13.0'):
        diagnostics.append(
            error(
                'runtime_version_mismatch',
                f'Runtime version {runtime_identity.runtime_version} is not the supported 0.13.0',
                'runtime',
            )
        )
    if runtime_identity.tokenizer_status == 'unavailable':
        diagnostics.append(
            error(
                'tokenizer_unavailable',
                runtime_identity.reason or 'Tokenizer is not readable',
                'connection.tokenizer_path',
            )
        )

    updated = plan.model_copy(update={'diagnostics': diagnostics, 'runtime': runtime_identity})
    return PreflightOutcome(
        plan=updated,
        alignment=alignment,
        context=checks,
        can_submit=not any(item.severity == 'error' for item in diagnostics),
        requires_acknowledgement=acknowledgement_codes(diagnostics),
    )
