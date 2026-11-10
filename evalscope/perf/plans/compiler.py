"""Deterministic, side-effect-free compilation of a portable performance plan.

The compiler expands the matrix, orders it, computes request/token budgets and
derives stable hashes. It never reads a tokenizer, opens a socket or starts a
process, so it is safe to call from a preview request.
"""

import hashlib
import json
import random
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import Field

from evalscope.perf.executors.urls import normalize_chat_url
from evalscope.utils import BaseArgument

from .diagnostics import PlanDiagnostic, error
from .documents import (
    PLAN_SCHEMA_VERSION,
    PerfPlanDocument,
    SyntheticWorkload,
    TokenPrefixWorkload,
    parse_plan_document,
)
from .strict import PlanParseError, classify_document, parse_document_text

COMPILER_VERSION = '1.2'
"""1.2 derives the default synthetic seed from request semantics only, so the
quality policy no longer enters the semantic fingerprint; archives keep the
version they were compiled with and are never recomputed."""
CONTRACT_VERSION = PLAN_SCHEMA_VERSION
MAX_RUNS = 10000


class PlannedRun(BaseArgument):
    """One independently executed matrix member, in final order."""

    run_id: str
    order: int
    workload_kind: Literal['token_prefix', 'aiperf_synthetic']
    load_mode: Literal['closed_loop'] = 'closed_loop'
    concurrency: int = Field(gt=0)
    request_count: int = Field(gt=0)
    output_token_limit: int = Field(gt=0)
    repetition: int = Field(ge=0)
    seed: int
    primer_requests: int = Field(ge=0)
    namespace: str = ''
    case_id: Optional[str] = None
    input_tokens: Optional[int] = None
    target_ratio: Optional[float] = None
    declared_total_input_tokens: Optional[int] = None
    shared_system_tokens: Optional[int] = None
    unique_user_tokens: Optional[int] = None

    @property
    def message_structure(self) -> Literal['user_only', 'system_and_user']:
        """Return the wire shape this run must produce."""
        return 'user_only' if self.workload_kind == 'token_prefix' else 'system_and_user'

    def required_context_tokens(self, template_overhead: Optional[int] = None) -> Optional[int]:
        """Return ``input + output`` in tokens, or ``None`` when the input is unknown."""
        if self.workload_kind == 'token_prefix':
            return (self.input_tokens or 0) + self.output_token_limit
        if self.shared_system_tokens is None or self.unique_user_tokens is None:
            return None
        if template_overhead is None:
            return None
        return self.shared_system_tokens + self.unique_user_tokens + template_overhead + self.output_token_limit


class PlanBudgets(BaseArgument):
    """Request and output ceilings, with the one-off phases kept separate."""

    formal_requests: int = Field(ge=0)
    formal_output_tokens: int = Field(ge=0)
    warmup_requests: int = Field(ge=0)
    warmup_output_tokens: int = Field(ge=0)
    primer_requests: int = Field(ge=0)
    total_requests: int = Field(ge=0)


class PlanRuntimeIdentity(BaseArgument):
    """Identity of the runtime/tokenizer/template, explicitly unknown until checked."""

    tokenizer_status: Literal['unknown', 'resolved', 'unavailable'] = 'unknown'
    tokenizer_id: Optional[str] = None
    template_id: Optional[str] = None
    runtime_status: Literal['unknown', 'available', 'unavailable'] = 'unknown'
    runtime_version: Optional[str] = None
    reason: Optional[str] = None


class CompiledPerfPlan(BaseArgument):
    """Normalized plan plus its expanded runs, budgets and stable hashes."""

    schema_version: Literal['1.0'] = '1.0'
    contract_version: str = CONTRACT_VERSION
    compiler_version: str = COMPILER_VERSION
    workload_semantics: Literal['managed_token_prefix', 'aiperf_synthetic_compat']
    intent: Literal['diagnostic', 'benchmark']
    normalized: PerfPlanDocument
    runs: List[PlannedRun]
    run_count: int = Field(ge=0)
    budgets: PlanBudgets
    runtime: PlanRuntimeIdentity = Field(default_factory=PlanRuntimeIdentity)
    diagnostics: List[PlanDiagnostic] = Field(default_factory=list)
    source_hash: str
    normalized_hash: str
    plan_hash: str
    semantic_fingerprint: str

    @property
    def requires_acknowledgement(self) -> List[str]:
        """Return warning codes that must be acknowledged before submit."""
        from .diagnostics import acknowledgement_codes

        return acknowledgement_codes(self.diagnostics)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _workload_semantics(document: PerfPlanDocument) -> Literal['managed_token_prefix', 'aiperf_synthetic_compat']:
    return 'managed_token_prefix' if isinstance(document.workload, TokenPrefixWorkload) else 'aiperf_synthetic_compat'


def _namespace(plan_seed: str, run_id: str) -> str:
    return hashlib.sha256(f'{plan_seed}:{run_id}'.encode('utf-8')).hexdigest()


def planned_run_count(document: PerfPlanDocument) -> int:
    """Return the expanded matrix size without allocating a single run.

    The count is a plain integer product of the declared axes, so a huge
    ``repetitions`` value is rejected before any ``PlannedRun`` object or task
    directory exists.
    """
    load = document.load
    points = len(load.points)
    workload = document.workload
    if isinstance(workload, TokenPrefixWorkload):
        axes = len(workload.input_lengths) * len(workload.target_ratios)
    else:
        axes = len(workload.cases)
    return axes * points * load.repetitions


def _managed_members(document: PerfPlanDocument) -> List[PlannedRun]:
    workload = document.workload
    assert isinstance(workload, TokenPrefixWorkload)
    load = document.load
    seed = document.execution.seed if document.execution.seed is not None else workload.seed
    primer_requests = workload.prefix_pool_size
    members: List[PlannedRun] = []
    for load_index, point in enumerate(load.points):
        for input_index, input_tokens in enumerate(workload.input_lengths):
            for ratio_index, ratio in enumerate(workload.target_ratios):
                for repetition in range(load.repetitions):
                    run_id = f'i{input_index:03d}-r{ratio_index:03d}-l{load_index:03d}-n{repetition:03d}'
                    members.append(
                        PlannedRun(
                            run_id=run_id,
                            order=0,
                            workload_kind='token_prefix',
                            concurrency=point.concurrency,
                            request_count=load.request_count,
                            output_token_limit=load.output_token_limit,
                            repetition=repetition,
                            seed=seed,
                            primer_requests=primer_requests if ratio else 0,
                            namespace=_namespace(f'{seed}:{document.connection.tokenizer_path}', run_id),
                            input_tokens=input_tokens,
                            target_ratio=ratio,
                        )
                    )
    return members


def _request_semantics(document: PerfPlanDocument) -> Dict[str, Any]:
    """Fields that decide which requests are generated; quality policy is excluded.

    ``ratio_tolerance`` and ``require_observed_ratio`` only judge a result, so
    they must not reach the derived seed and, through it, the semantic
    fingerprint: two plans that send identical requests stay comparable even
    when one of them demands an observed ratio.
    """
    workload = document.workload
    if isinstance(workload, SyntheticWorkload):
        workload_payload = workload.model_dump(mode='json', exclude={'ratio_tolerance', 'require_observed_ratio'})
    else:
        workload_payload = workload.model_dump(mode='json')
    return {
        'connection': document.connection.model_dump(mode='json'),
        'load': document.load.model_dump(mode='json'),
        'workload': workload_payload,
    }


def _synthetic_seed(document: PerfPlanDocument) -> int:
    """Return the declared seed or a reproducible content-derived one.

    A synthetic case has no seed of its own. When the plan does not declare one
    and the order is deterministic, the seed is derived from the *request*
    semantics so two identical plans produce the same run seeds; quality policy
    and a time-based seed never enter it.
    """
    if document.execution.seed is not None:
        return document.execution.seed
    if document.execution.order == 'seeded_shuffle':
        raise PlanParseError(
            'invalid_value',
            'seeded_shuffle requires an explicit execution.seed for aiperf_synthetic workloads; '
            'a generated time seed would make the order irreproducible',
            'execution.seed',
        )
    payload = _canonical(_request_semantics(document))
    return int(hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12], 16)


def _synthetic_members(document: PerfPlanDocument) -> List[PlannedRun]:
    workload = document.workload
    assert isinstance(workload, SyntheticWorkload)
    load = document.load
    seed = _synthetic_seed(document)
    members: List[PlannedRun] = []
    for load_index, point in enumerate(load.points):
        for case_index, case in enumerate(workload.cases):
            for repetition in range(load.repetitions):
                run_id = f'c{case_index:03d}-l{load_index:03d}-n{repetition:03d}'
                members.append(
                    PlannedRun(
                        run_id=run_id,
                        order=0,
                        workload_kind='aiperf_synthetic',
                        concurrency=point.concurrency,
                        request_count=load.request_count,
                        output_token_limit=load.output_token_limit,
                        repetition=repetition,
                        seed=seed,
                        primer_requests=0,
                        namespace=_namespace(f'{seed}:{case.case_id}', run_id),
                        case_id=case.case_id,
                        declared_total_input_tokens=case.declared_total_input_tokens,
                        shared_system_tokens=case.shared_system_tokens,
                        unique_user_tokens=case.unique_user_tokens,
                        target_ratio=case.target_prefix_ratio,
                    )
                )
    return members


def _order_members(members: List[PlannedRun], document: PerfPlanDocument) -> List[PlannedRun]:
    """Apply the declared order; the execution layer must not reshuffle."""
    if document.execution.order == 'seeded_shuffle':
        seed = document.execution.seed
        if seed is None:
            workload = document.workload
            seed = workload.seed if isinstance(workload, TokenPrefixWorkload) else None
        if seed is None:
            raise PlanParseError(
                'invalid_value',
                'seeded_shuffle requires an explicit seed; a generated time seed is not reproducible',
                'execution.seed',
            )
        random.Random(seed).shuffle(members)
    for index, member in enumerate(members):
        member.order = index
    return members


def _budgets(document: PerfPlanDocument, runs: List[PlannedRun]) -> PlanBudgets:
    formal_requests = sum(run.request_count for run in runs)
    warmup = document.warmup
    warmup_requests = warmup.request_count if warmup.enabled else 0
    primer_requests = sum(run.primer_requests for run in runs)
    return PlanBudgets(
        formal_requests=formal_requests,
        formal_output_tokens=formal_requests * document.load.output_token_limit,
        warmup_requests=warmup_requests,
        warmup_output_tokens=warmup_requests * warmup.output_token_limit,
        primer_requests=primer_requests,
        total_requests=formal_requests + warmup_requests + primer_requests,
    )


_DEPLOYMENT_SEMANTIC_FIELDS = (
    'mode',
    'declared_max_model_len',
    'routing_policy',
    'routing_coverage',
    'deployment_id',
    'model_revision',
    'engine_image_digest',
    'engine_version',
    'node_count',
    'gpu_count',
    'tp',
    'dp',
    'pp',
    'ep',
    'kv_dtype',
    'prefix_cache',
    'max_num_seqs',
)
"""Deployment facts that make two results comparable; ``evidence`` is a provenance label, not a fact."""


def _semantic_payload(document: PerfPlanDocument, runs: List[PlannedRun]) -> Dict[str, Any]:
    """Fields that make two results comparable; identity, paths and credentials never enter.

    The tokenizer *path* is deliberately absent: it is identity, not semantics.
    Two runs that resolved the same tokenizer/template content stay comparable
    even when the local directory moved, and that comparison uses the archived
    effective identity rather than this fingerprint.
    """
    base_url, endpoint = normalize_chat_url(document.connection.url)
    deployment = document.deployment.model_dump(mode='json')
    return {
        'contract_version': CONTRACT_VERSION,
        'compiler_version': COMPILER_VERSION,
        'workload_semantics': _workload_semantics(document),
        'intent': document.intent,
        'connection': {
            'model': document.connection.model,
            'endpoint': base_url + endpoint,
            'protocol': document.connection.protocol,
            'streaming': document.connection.streaming,
        },
        'deployment': {field: deployment.get(field) for field in _DEPLOYMENT_SEMANTIC_FIELDS},
        'load': document.load.model_dump(mode='json'),
        'warmup': document.warmup.model_dump(mode='json'),
        'priming': document.priming.model_dump(mode='json'),
        'execution': document.execution.model_dump(mode='json'),
        # ``order`` is a scheduling decision and ``namespace`` derives from the
        # tokenizer path; neither changes what is measured.
        'runs': [run.model_dump(mode='json', exclude={'order', 'namespace'}) for run in runs],
    }


def compile_plan(document: PerfPlanDocument, source_text: Optional[str] = None) -> CompiledPerfPlan:
    """Expand and hash a validated plan without any I/O.

    ``source_text`` is the original uploaded text; when it is absent (a plan
    recompiled by a worker from its own normalized archive) the canonical
    normalized document is used, which yields the same ``source_hash`` shape.

    The matrix size is checked before expansion: an oversized plan must not
    allocate its run list, its document text or a task directory.
    """
    diagnostics: List[PlanDiagnostic] = []
    planned = planned_run_count(document)
    if planned > MAX_RUNS:
        raise PlanParseError(
            'too_many_runs',
            f'The plan expands to {planned} runs; the limit is {MAX_RUNS}',
            'load',
        )
    if isinstance(document.workload, TokenPrefixWorkload):
        members = _managed_members(document)
    else:
        members = _synthetic_members(document)
    if len(members) != planned:
        raise PlanParseError(
            'invalid_value',
            f'The plan declared {planned} runs but expanded to {len(members)}',
            'load',
        )
    runs = _order_members(members, document)
    normalized = document.model_dump(mode='json')
    normalized_text = _canonical(normalized)
    source_payload = source_text if source_text is not None else normalized_text
    semantic_payload = _semantic_payload(document, runs)
    plan_payload = {
        'contract_version': CONTRACT_VERSION,
        'compiler_version': COMPILER_VERSION,
        'normalized_hash': _sha256(normalized_text),
        'semantic_fingerprint': _sha256(_canonical(semantic_payload)),
        'runs': [run.model_dump(mode='json') for run in runs],
    }
    return CompiledPerfPlan(
        workload_semantics=_workload_semantics(document),
        intent=document.intent,
        normalized=document,
        runs=runs,
        run_count=len(runs),
        budgets=_budgets(document, runs),
        diagnostics=diagnostics,
        source_hash=_sha256(source_payload),
        normalized_hash=_sha256(normalized_text),
        plan_hash=_sha256(_canonical(plan_payload)),
        semantic_fingerprint=_sha256(_canonical(semantic_payload)),
    )


def _upgrade_parse_code(error_value: PlanParseError) -> PlanParseError:
    """Map a duplicate-related validation message to its stable diagnostic code."""
    message = error_value.message
    if 'must be unique' in message or 'must have unique' in message:
        lowered = error_value.field_path.lower()
        if 'ratio' in lowered or 'ratio' in message:
            return PlanParseError('duplicate_ratio', message, error_value.field_path)
        if 'concurrency' in lowered or 'load point' in message:
            return PlanParseError('duplicate_load_point', message, error_value.field_path)
    return error_value


def parse_plan_text(text: Any) -> Tuple[PerfPlanDocument, str]:
    """Strictly decode, classify and validate one portable plan document."""
    document, source_text = parse_document_text(text)
    if classify_document(document) != 'perf_plan':
        raise PlanParseError(
            'unsupported_schema',
            'This endpoint reads portable perf plans only; a configuration library must be imported by the workspace.',
            'kind',
        )
    try:
        return parse_plan_document(document), source_text
    except PlanParseError as error_value:
        raise _upgrade_parse_code(error_value) from None


def compile_plan_text(text: Any) -> CompiledPerfPlan:
    """Parse and compile one document from its original JSON text."""
    document, source_text = parse_plan_text(text)
    return compile_plan(document, source_text)


def structure_diagnostic(error_value: PlanParseError) -> PlanDiagnostic:
    """Convert a parse rejection into one typed diagnostic."""
    code = error_value.code
    if code == 'too_many_runs':
        return error('too_many_runs', error_value.message, error_value.field_path)
    if code in ('unsupported_schema', 'duplicate_json_key', 'invalid_json', 'too_large_document'):
        return error(code, error_value.message, error_value.field_path)  # type: ignore[arg-type]
    if code in ('credential_in_document', 'unsupported_feature'):
        return error(code, error_value.message, error_value.field_path)  # type: ignore[arg-type]
    if code in ('duplicate_ratio', 'duplicate_load_point'):
        return error(code, error_value.message, error_value.field_path)  # type: ignore[arg-type]
    return error('invalid_value', error_value.message, error_value.field_path)
