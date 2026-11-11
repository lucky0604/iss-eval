"""Service layer for portable performance plans.

Preview and export are pure functions over the submitted text. Submit re-runs
the same compilation, enforces plan-hash agreement, acknowledgement scope and
task-id idempotency, then hands an immutable compiled plan to the worker.

The performance artifact root is always ``<outputs>/<task_id>/perf``: the
executor, the archive reader, the progress reader and the result scanner call
:func:`task_perf_root` instead of joining their own paths.
"""

import json
import os
import time
from pathlib import Path
from typing import Collection, List, Literal, Optional

from evalscope.perf.plans import (
    CompiledPerfPlan,
    PlanAlignment,
    PlanDiagnostic,
    PlanParseError,
    PlanRuntimeIdentity,
    PlanSubmission,
    PreflightOutcome,
    compile_plan_text,
    preflight_plan,
    structure_diagnostic,
)
from evalscope.perf.plans.alignment import LegacySourceFacts
from evalscope.perf.plans.execution import PerfPlanManifest
from evalscope.perf.plans.provenance import (
    PlanExecutionConflict,
    PlanIdentityError,
    plan_dir,
    read_compiled_plan,
    read_effective_identity,
    read_plan_hash,
    read_submission,
    resolve_plan_dir,
    write_submission,
)
from evalscope.utils import BaseArgument

from .api_models.perf_plans import (
    PerfPlanArchiveResponse,
    PerfPlanExportResponse,
    PerfPlanPreviewResponse,
    PerfPlanRunView,
    PlanDiagnosticView,
    PlanRunSummary,
)

MAX_PREVIEW_CONTEXT_ROWS = 200
MAX_RUN_PAGE = 500
MAX_ARCHIVE_RUN_PAGE = 200
PERF_DIR_NAME = 'perf'

PlanLifecycle = Literal['reserved', 'queued', 'running', 'completed', 'stopped', 'error', 'lost']


class PlanRejected(ValueError):
    """A rejected plan with a stable HTTP status and typed diagnostics."""

    def __init__(
        self,
        status: int,
        error: str,
        diagnostics: Optional[List[PlanDiagnostic]] = None,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(error)
        self.status = status
        self.error = error
        self.diagnostics = diagnostics or []
        self.details = details or {}

    def payload(self) -> dict:
        """Render a JSON body for the Web layer."""
        body: dict = {'error': self.error}
        if self.diagnostics:
            body['diagnostics'] = [_diagnostic_view(item).model_dump(mode='json') for item in self.diagnostics]
        body.update(self.details)
        return body


class PlanState(BaseArgument):
    """The truthful current state of one plan task, read from its own files."""

    state: PlanLifecycle
    reason: Optional[str] = None
    manifest: Optional[PerfPlanManifest] = None
    submission: Optional[PlanSubmission] = None
    legacy: bool = False


class PreparedPlanInvoke(BaseArgument):
    """A validated plan ready to be materialized as a task."""

    task_id: str
    plan: CompiledPerfPlan
    source_text: str
    alignment: PlanAlignment
    duplicate: bool = False
    retry: bool = False
    state: PlanLifecycle = 'reserved'
    state_reason: Optional[str] = None


def task_perf_root(outputs_root: str, task_id: str) -> Path:
    """Return the single performance artifact root for one service task."""
    return Path(outputs_root) / task_id / PERF_DIR_NAME


def _diagnostic_view(item: PlanDiagnostic) -> PlanDiagnosticView:
    return PlanDiagnosticView(
        code=item.code,
        severity=item.severity,
        field_path=item.field_path,
        case_id=item.case_id,
        message_key=item.message_key,
        message=item.message,
        parameters=dict(item.parameters),
    )


def _run_summary(run) -> PlanRunSummary:
    """Project one planned run onto the bounded Web contract."""
    return PlanRunSummary(
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
        request_count=run.request_count,
        output_token_limit=run.output_token_limit,
        repetition=run.repetition,
        primer_requests=run.primer_requests,
    )


def runtime_identity(engine_available: bool, runtime_version: Optional[str] = None) -> PlanRuntimeIdentity:
    """Describe the runtime honestly: configuration presence is not a version probe."""
    if not engine_available:
        return PlanRuntimeIdentity(
            runtime_status='unavailable', reason='AIPerf 0.13.0 runtime is unavailable on this service'
        )
    return PlanRuntimeIdentity(runtime_status='available', runtime_version=runtime_version)


def _parse_or_reject(content: object) -> tuple[CompiledPerfPlan, str]:
    try:
        plan = compile_plan_text(content)
        return plan, str(content)
    except PlanParseError as error_value:
        diagnostic = structure_diagnostic(error_value)
        status = 413 if error_value.code == 'too_large_document' else 400
        raise PlanRejected(status, error_value.message, [diagnostic]) from None


def preflight(
    content: object, *, engine_available: bool, source: Optional[LegacySourceFacts] = None
) -> PreflightOutcome:
    """Compile and preflight one document, mapping rejections to typed errors."""
    plan, _ = _parse_or_reject(content)
    return preflight_plan(plan, source=source, runtime=runtime_identity(engine_available))


def build_preview(
    content: object,
    *,
    engine_available: bool,
    offset: int = 0,
    limit: int = 100,
    acknowledged: Collection[str] = (),
    source: Optional[LegacySourceFacts] = None,
    runtime_version: Optional[str] = None,
) -> PerfPlanPreviewResponse:
    """Return a bounded, side-effect-free preview of the submitted plan."""
    if offset < 0 or not 1 <= limit <= MAX_RUN_PAGE:
        raise PlanRejected(400, 'Invalid pagination window')
    plan, _ = _parse_or_reject(content)
    outcome = preflight_plan(plan, source=source, runtime=runtime_identity(engine_available, runtime_version))
    requires = outcome.requires_acknowledgement
    return PerfPlanPreviewResponse(
        contract_version=outcome.plan.contract_version,
        compiler_version=outcome.plan.compiler_version,
        workload_semantics=outcome.plan.workload_semantics,
        intent=outcome.plan.intent,
        plan_hash=outcome.plan.plan_hash,
        normalized_hash=outcome.plan.normalized_hash,
        source_hash=outcome.plan.source_hash,
        semantic_fingerprint=outcome.plan.semantic_fingerprint,
        normalized=outcome.plan.normalized,
        runs=[_run_summary(run) for run in outcome.plan.runs[offset : offset + limit]],
        total_runs=outcome.plan.run_count,
        offset=offset,
        limit=limit,
        budgets=outcome.plan.budgets,
        diagnostics=[_diagnostic_view(item) for item in outcome.plan.diagnostics],
        alignment=outcome.alignment,
        context=outcome.context[:MAX_PREVIEW_CONTEXT_ROWS],
        can_submit=outcome.can_submit,
        requires_acknowledgement=requires,
        acknowledged=sorted(set(acknowledged) & set(requires)),
        engine_available=engine_available,
        runtime=outcome.plan.runtime,
    )


def build_export(
    content: object,
    *,
    engine_available: bool,
    source: Optional[LegacySourceFacts] = None,
) -> PerfPlanExportResponse:
    """Serialize the normalized document deterministically so it re-imports identically."""
    outcome = preflight(content, engine_available=engine_available, source=source)
    text = json.dumps(outcome.plan.normalized.model_dump(mode='json'), indent=2, sort_keys=True, ensure_ascii=False)
    return PerfPlanExportResponse(
        content=text + '\n',
        plan_hash=outcome.plan.plan_hash,
        normalized_hash=outcome.plan.normalized_hash,
        semantic_fingerprint=outcome.plan.semantic_fingerprint,
        can_submit=outcome.can_submit,
        diagnostics=[_diagnostic_view(item) for item in outcome.plan.diagnostics],
    )


def read_plan_state(perf_root: str | Path, *, queued: Optional[bool] = None) -> PlanState:
    """Resolve the current state from the manifest, the submission and the queue.

    ``queued`` is the queue's own answer when it can be asked. A submission
    recorded as queued but unknown to the queue is ``lost``: the service does
    not silently replay a model task it cannot prove has not started.
    """
    root = Path(perf_root)
    directory, legacy = resolve_plan_dir(root.parent)
    if not directory.is_dir():
        return PlanState(state='queued', reason='plan_archive_missing')
    manifest: Optional[PerfPlanManifest] = None
    manifest_path = directory / 'manifest.json'
    if manifest_path.is_file():
        try:
            manifest = PerfPlanManifest.model_validate_json(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            manifest = None
    submission = read_submission(root)
    if manifest is not None:
        status = manifest.status
        state: PlanLifecycle = {'running': 'running', 'completed': 'completed', 'stopped': 'stopped', 'error': 'error'}[
            status
        ]
        return PlanState(state=state, manifest=manifest, submission=submission, legacy=legacy)
    if legacy:
        return PlanState(state='error', reason='legacy_archive_without_manifest', legacy=True)
    if submission is not None:
        if submission.state == 'failed':
            return PlanState(state='error', reason=submission.reason or 'enqueue_failed', submission=submission)
        if submission.state == 'reserved':
            return PlanState(state='reserved', submission=submission)
        if queued is False:
            return PlanState(state='lost', reason='queue_record_missing', submission=submission)
        return PlanState(state='queued', submission=submission)
    return PlanState(state='queued', reason='submission_record_missing')


def prepare_invoke(
    content: object,
    *,
    task_id: str,
    outputs_root: str,
    engine_available: bool,
    acknowledged: Collection[str] = (),
    expected_plan_hash: Optional[str] = None,
    source: Optional[LegacySourceFacts] = None,
    queued: Optional[bool] = None,
) -> PreparedPlanInvoke:
    """Validate a submission; never creates or reuses a task directory here.

    Every submission must bind to the plan hash its preview produced. An
    acknowledgement is therefore only valid for the exact plan that required
    it: editing the document changes the hash and forces a new preview.
    """
    plan, source_text = _parse_or_reject(content)
    outcome = preflight_plan(plan, source=source, runtime=runtime_identity(engine_available))
    if not engine_available:
        raise PlanRejected(503, 'AIPerf 0.13.0 runtime is unavailable', outcome.plan.diagnostics)
    errors = [item for item in outcome.plan.diagnostics if item.severity == 'error']
    if errors:
        raise PlanRejected(400, 'The plan is not valid for submission', errors)
    if expected_plan_hash is None:
        raise PlanRejected(
            400,
            'expected_plan_hash is required: submit the hash returned by the preview of this exact plan',
            details={'plan_hash': outcome.plan.plan_hash},
        )
    if expected_plan_hash != outcome.plan.plan_hash:
        raise PlanRejected(
            409,
            'The plan changed since it was previewed; preview it again',
            details={'plan_hash': outcome.plan.plan_hash},
        )
    missing = [code for code in outcome.requires_acknowledgement if code not in set(acknowledged)]
    if missing:
        raise PlanRejected(
            400,
            'Explicit acknowledgement is required for this plan',
            [item for item in outcome.plan.diagnostics if item.code in missing],
            {'missing_acknowledgements': missing, 'plan_hash': outcome.plan.plan_hash},
        )

    task_directory = Path(outputs_root) / task_id
    if not task_directory.exists():
        return PreparedPlanInvoke(
            task_id=task_id, plan=outcome.plan, source_text=source_text, alignment=outcome.alignment
        )
    perf_root = task_perf_root(outputs_root, task_id)
    directory, legacy = resolve_plan_dir(task_directory)
    if legacy:
        raise PlanRejected(409, 'This task ID holds a read-only legacy plan archive; use a new task ID')
    archived = read_plan_hash(perf_root)
    if archived is None:
        # The directory exists but holds no plan identity (a native perf task,
        # a half-created directory or a lost race). It is not reusable.
        raise PlanRejected(409, 'Task ID already exists')
    state = read_plan_state(perf_root, queued=queued)
    if archived != outcome.plan.plan_hash:
        raise PlanRejected(
            409, 'This task ID already holds a different plan; use a new task ID', details={'plan_hash': archived}
        )
    if state.state in ('reserved', 'queued', 'running', 'completed', 'stopped', 'lost'):
        return PreparedPlanInvoke(
            task_id=task_id,
            plan=outcome.plan,
            source_text=source_text,
            alignment=outcome.alignment,
            duplicate=True,
            state=state.state,
            state_reason=state.reason,
        )
    # state == 'error' from a definite enqueue failure: the reservation is
    # retryable and keeps its plan identity.
    return PreparedPlanInvoke(
        task_id=task_id,
        plan=outcome.plan,
        source_text=source_text,
        alignment=outcome.alignment,
        retry=True,
        state='error',
        state_reason=state.reason,
    )


def reserve_invoke(prepared: PreparedPlanInvoke, outputs_root: str) -> PlanSubmission:
    """Persist the accepted identity before the task is handed to the queue."""
    from evalscope.perf.plans.provenance import reserve_submission

    return reserve_submission(
        task_perf_root(outputs_root, prepared.task_id),
        prepared.plan,
        task_id=prepared.task_id,
        source_text=prepared.source_text,
        alignment=prepared.alignment,
    )


def mark_enqueued(prepared: PreparedPlanInvoke, outputs_root: str, submission: PlanSubmission) -> PlanSubmission:
    """Record that the queue accepted the job."""
    return write_submission(
        task_perf_root(outputs_root, prepared.task_id),
        submission.model_copy(update={'state': 'queued', 'enqueued_at': _now_iso(), 'reason': None}),
    )


def mark_enqueue_failed(
    prepared: PreparedPlanInvoke, outputs_root: str, submission: PlanSubmission, reason: str
) -> PlanSubmission:
    """Record a definite enqueue failure so the same ID stays retryable."""
    return write_submission(
        task_perf_root(outputs_root, prepared.task_id),
        submission.model_copy(update={'state': 'failed', 'reason': reason}),
    )


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


def plan_progress(perf_root: str | Path, *, queued: Optional[bool] = None) -> Optional[dict]:
    """Project one plan onto the existing progress vocabulary.

    ``None`` means "no plan archive here", so the caller falls back to the
    legacy progress readers. The percent is computed from real run counts, not
    from a single native run's request percentage.
    """
    root = Path(perf_root)
    directory, _ = resolve_plan_dir(root.parent)
    if not directory.is_dir():
        return None
    state = read_plan_state(root, queued=queued)
    manifest = state.manifest
    total = manifest.total_runs if manifest else 0
    completed = manifest.completed_runs if manifest else 0
    if state.state == 'completed':
        percent: Optional[float] = 100.0
    elif state.state in ('reserved', 'queued'):
        percent = 0.0
    elif total:
        percent = min(99.9, 100.0 * completed / total)
    else:
        percent = None
    return {
        'percent': percent,
        'phase': manifest.phase if manifest else None,
        'current_subrun': manifest.current_run if manifest else None,
        'completed_subruns': completed,
        'total_subruns': total,
        'quality': manifest.quality if manifest else None,
        'plan_state': state.state,
        'plan_state_reason': state.reason,
    }


def resolve_plan_perf_root(
    *,
    outputs_root: str,
    task_id: Optional[str] = None,
    root_path: Optional[str] = None,
    rel_path: Optional[str] = None,
) -> tuple[Path, Optional[str]]:
    """Resolve a plan's ``perf`` root from a task id or an explicit archive path.

    The explicit form exists so an arbitrary outputs root can list its plans
    without pretending to own the service's task namespace. The path must end
    at ``perf`` and stay inside the requested root.
    """
    if task_id is not None:
        from .utils import validate_task_id

        validate_task_id(task_id)
        return task_perf_root(outputs_root, task_id), task_id
    if not rel_path:
        raise ValueError('A plan path is required')
    root = Path(root_path or outputs_root).resolve(strict=True)
    target = (root / rel_path).resolve(strict=True)
    if target != root and root not in target.parents:
        raise ValueError('Plan path escapes the outputs root')
    if target.name != PERF_DIR_NAME or not (target / 'plan' / 'compiled.json').is_file():
        raise FileNotFoundError('Plan archive not found')
    label = target.parent.name if target.parent != root else None
    return target, label


def load_archive(
    task_id: Optional[str] = None,
    outputs_root: str = '',
    *,
    root_path: Optional[str] = None,
    rel_path: Optional[str] = None,
    offset: int = 0,
    limit: int = 100,
    queued: Optional[bool] = None,
) -> PerfPlanArchiveResponse:
    """Read the plan archive, a bounded run page and the effective identity."""
    if offset < 0 or not 1 <= limit <= MAX_ARCHIVE_RUN_PAGE:
        raise PlanRejected(400, 'Invalid run page')
    perf_root, task_id = resolve_plan_perf_root(
        outputs_root=outputs_root, task_id=task_id, root_path=root_path, rel_path=rel_path
    )
    directory, legacy = resolve_plan_dir(perf_root.parent)
    if not directory.is_dir():
        raise FileNotFoundError('Plan archive not found')
    try:
        compiled = read_compiled_plan(perf_root)
    except ValueError:
        compiled = None
    state = read_plan_state(perf_root, queued=queued)
    manifest = state.manifest
    alignment: Optional[PlanAlignment] = None
    alignment_path = directory / 'alignment.json'
    if alignment_path.is_file():
        try:
            alignment = PlanAlignment.model_validate_json(alignment_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            alignment = None
    effective = read_effective_identity(perf_root)
    rows = {row.run_id: row for row in (manifest.runs if manifest else [])}
    runs: List[PerfPlanRunView] = []
    if compiled is not None:
        for run in compiled.runs[offset : offset + limit]:
            view = PerfPlanRunView.build(run, rows.get(run.run_id))
            view.requests_available = _run_requests_available(perf_root, run.run_id)
            runs.append(view)
    document = compiled.normalized if compiled is not None else None
    return PerfPlanArchiveResponse(
        task_id=task_id or (compiled.normalized.name if compiled and compiled.normalized.name else 'unknown'),
        name=document.name if document else None,
        model=document.connection.model if document else '',
        workload_semantics=compiled.workload_semantics if compiled else None,
        contract_version=compiled.contract_version if compiled else None,
        compiler_version=compiled.compiler_version if compiled else None,
        plan_hash=(manifest.plan_hash if manifest else (compiled.plan_hash if compiled else None)),
        normalized_hash=(manifest.normalized_hash if manifest else (compiled.normalized_hash if compiled else None)),
        source_hash=compiled.source_hash if compiled else None,
        semantic_fingerprint=(
            manifest.semantic_fingerprint if manifest else (compiled.semantic_fingerprint if compiled else None)
        ),
        state=state.state,
        state_reason=state.reason,
        status=manifest.status if manifest else None,
        quality=manifest.quality if manifest else None,
        quality_reasons=list(manifest.quality_reasons) if manifest else [],
        phase=manifest.phase if manifest else None,
        current_run=manifest.current_run if manifest else None,
        completed_runs=manifest.completed_runs if manifest else 0,
        total_runs=compiled.run_count if compiled else (manifest.total_runs if manifest else 0),
        budgets=compiled.budgets if compiled else None,
        warmup=manifest.warmup if manifest else None,
        runs=runs,
        offset=offset,
        limit=limit,
        archive=manifest.archive if manifest else {},
        alignment=alignment,
        effective=effective,
        report_available=compiled is not None and not legacy,
    )


def _run_requests_available(perf_root: Path, run_id: str) -> bool:
    """State whether one run published a readable normalized request store."""
    normalized = perf_root / 'runs' / run_id / 'normalized'
    generations = normalized / 'generations'
    if not (normalized / 'current.json').is_file():
        return False
    try:
        return any(
            (generations / name / 'requests.db').is_file()
            for name in os.listdir(generations)
            if (generations / name).is_dir()
        )
    except OSError:
        return False


def run_normalized_dir(
    outputs_root: str,
    task_id: Optional[str],
    run_id: str,
    *,
    root_path: Optional[str] = None,
    rel_path: Optional[str] = None,
) -> Path:
    """Resolve one run's normalized directory, refusing any path escape."""
    if not run_id or os.path.basename(run_id) != run_id or run_id in ('.', '..'):
        raise ValueError('Invalid run id')
    perf_root, _ = resolve_plan_perf_root(
        outputs_root=outputs_root, task_id=task_id, root_path=root_path, rel_path=rel_path
    )
    perf_root = perf_root.resolve(strict=True)
    target = (perf_root / 'runs' / run_id / 'normalized').resolve()
    if perf_root not in target.parents:
        raise ValueError('Run directory escapes the task archive')
    return target


def plan_archive_root(outputs_root: str, task_id: str) -> tuple[Path, bool]:
    """Return ``(plan directory, legacy)`` for one task id."""
    from .utils import validate_task_id

    validate_task_id(task_id)
    return resolve_plan_dir(Path(outputs_root) / task_id)


__all__ = [
    'MAX_ARCHIVE_RUN_PAGE',
    'MAX_PREVIEW_CONTEXT_ROWS',
    'MAX_RUN_PAGE',
    'PlanExecutionConflict',
    'PlanRejected',
    'PlanState',
    'PreparedPlanInvoke',
    'build_export',
    'build_preview',
    'load_archive',
    'mark_enqueue_failed',
    'mark_enqueued',
    'plan_archive_root',
    'plan_progress',
    'preflight',
    'prepare_invoke',
    'read_plan_state',
    'reserve_invoke',
    'resolve_plan_perf_root',
    'run_normalized_dir',
    'runtime_identity',
    'task_perf_root',
]
