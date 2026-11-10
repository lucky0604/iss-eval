"""Versioned provenance archive for one compiled plan execution.

One layout is authoritative for every reader and writer::

    <task_dir>/perf/plan/{source.redacted,normalized,compiled,environment,effective,alignment}.json
    <task_dir>/perf/plan/manifest.json
    <task_dir>/perf/runs/<run_id>/...

``<task_dir>`` is the service task directory, ``perf`` is the performance
artifact root the executor already uses. Modules must not join their own
paths: they call :func:`perf_dir`, :func:`plan_dir`, or
:func:`resolve_plan_dir` so a later reader cannot disagree with the writer.

Source, normalized and compiled documents are immutable once accepted. Runtime
facts (which tokenizer/template content was actually resolved, which AIPerf
version ran) are published separately in ``effective.json`` so an accepted
``plan_hash`` is never rewritten by execution.

Files are private (0600) and the directory is 0700 because the source document
may still contain user prompts.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from evalscope.perf.experiments.storage import write_experiment_json
from evalscope.utils import BaseArgument

from .alignment import PlanAlignment
from .compiler import CompiledPerfPlan

ARCHIVE_SCHEMA_VERSION = '1.0'
EFFECTIVE_SCHEMA_VERSION = '1.0'
EFFECTIVE_IDENTITY_VERSION = '1'
PERF_DIR_NAME = 'perf'
PLAN_DIR_NAME = 'plan'
SUBMISSION_FILE = 'submission.json'
EFFECTIVE_FILE = 'effective.json'
_ARCHIVE_FILES = ('source.redacted.json', 'normalized.json', 'compiled.json', 'environment.json', 'alignment.json')
"""Documents written for every accepted plan; ``manifest.json``/``effective.json`` are added later."""


class PlanArchiveError(ValueError):
    """The plan archive is missing or inconsistent."""


class PlanIdentityError(ValueError):
    """A reserved or retried task ID does not carry the submitted plan identity."""


class PlanExecutionConflict(ValueError):
    """The task directory already holds a run set; a second execution is refused."""


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def perf_dir(task_dir: str | Path) -> Path:
    """Return the performance artifact root for one service task directory."""
    return Path(task_dir) / PERF_DIR_NAME


def plan_dir(perf_root: str | Path) -> Path:
    """Return the plan provenance directory for one performance artifact root."""
    return Path(perf_root) / PLAN_DIR_NAME


def legacy_plan_dir(task_dir: str | Path) -> Path:
    """Return the pre-fix layout ``<task_dir>/plan``; read-only compatibility only."""
    return Path(task_dir) / PLAN_DIR_NAME


def resolve_plan_dir(task_dir: str | Path) -> tuple[Path, bool]:
    """Return ``(directory, legacy)`` for one task directory.

    The canonical ``<task>/perf/plan`` always wins. A legacy ``<task>/plan`` is
    only returned when the canonical directory is absent, and callers must
    treat that result as read-only: no new execution writes there.
    """
    canonical = plan_dir(perf_dir(task_dir))
    if canonical.is_dir():
        return canonical, False
    old = legacy_plan_dir(task_dir)
    if old.is_dir():
        return old, True
    return canonical, False


def create_plan_dir(perf_root: str | Path) -> Path:
    """Create the plan directory once; an existing directory is a different task."""
    directory = plan_dir(perf_root)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(directory, 0o700)
    return directory


# ---------------------------------------------------------------------------
# Immutable archive documents
# ---------------------------------------------------------------------------


def _source_document(source_text: Optional[str], compiled: CompiledPerfPlan) -> Dict[str, Any]:
    """Keep the submitted text when it is a portable plan; otherwise record the normalized document."""
    if source_text:
        try:
            value = json.loads(source_text)
        except ValueError:
            value = None
        if isinstance(value, dict):
            return {'schema_version': ARCHIVE_SCHEMA_VERSION, 'kind': 'submitted_text', 'document': value}
    return {
        'schema_version': ARCHIVE_SCHEMA_VERSION,
        'kind': 'normalized_only',
        'document': compiled.normalized.model_dump(mode='json'),
    }


def write_plan_archive(
    perf_root: str | Path,
    compiled: CompiledPerfPlan,
    *,
    source_text: Optional[str] = None,
    alignment: Optional[PlanAlignment] = None,
    environment: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write the immutable plan archive next to the run artifacts."""
    directory = plan_dir(perf_root)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    write_experiment_json(directory / 'source.redacted.json', _source_document(source_text, compiled))
    write_experiment_json(directory / 'normalized.json', compiled.normalized.model_dump(mode='json'))
    write_experiment_json(directory / 'compiled.json', compiled.model_dump(mode='json'))
    write_experiment_json(
        directory / 'environment.json',
        {
            'schema_version': ARCHIVE_SCHEMA_VERSION,
            'plan_hash': compiled.plan_hash,
            'runtime': compiled.runtime.model_dump(mode='json'),
            'deployment': compiled.normalized.deployment.model_dump(mode='json'),
            **(environment or {}),
        },
    )
    if alignment is not None:
        write_experiment_json(directory / 'alignment.json', alignment.model_dump(mode='json'))
    return directory


def initialize_plan_archive(
    perf_root: str | Path,
    compiled: CompiledPerfPlan,
    *,
    source_text: Optional[str] = None,
    alignment: Optional[PlanAlignment] = None,
    environment: Optional[Dict[str, Any]] = None,
) -> Path:
    """Create or adopt the archive of one submission, verifying its identity.

    A submission may already have reserved ``<perf>/plan`` before it was
    enqueued, so the archive is created here when absent and adopted when it
    carries the same accepted identity. A different identity, or an archive
    that already owns a ``runs/`` directory, is refused: one task ID is one
    plan and one execution attempt.
    """
    root = Path(perf_root)
    directory = plan_dir(root)
    if directory.exists():
        archived = read_compiled_plan(root)
        if archived.plan_hash != compiled.plan_hash:
            raise PlanIdentityError('This task ID already holds a different plan; use a new task ID')
    else:
        create_plan_dir(root)
    runs = root / 'runs'
    if runs.exists():
        raise PlanExecutionConflict('This task ID already executed a run set; use a new task ID')
    write_plan_archive(root, compiled, source_text=source_text, alignment=alignment, environment=environment)
    return plan_dir(root)


# ---------------------------------------------------------------------------
# Submission identity (reserved before enqueue)
# ---------------------------------------------------------------------------

SubmissionState = Literal['reserved', 'queued', 'failed']


class PlanSubmission(BaseArgument):
    """The minimal identity persisted before a plan is handed to the queue.

    It exists so ``GET``, retries and the worker can tell "reserved but not
    enqueued" from "queued" from "enqueue failed" without a second scheduler.
    """

    schema_version: Literal['1.0'] = '1.0'
    state: SubmissionState = 'reserved'
    task_id: str
    plan_hash: str
    normalized_hash: str
    semantic_fingerprint: str
    compiler_version: str
    contract_version: str
    reserved_ns: int
    updated_ns: int
    enqueued_at: Optional[str] = None
    reason: Optional[str] = None


def submission_path(perf_root: str | Path) -> Path:
    return plan_dir(perf_root) / SUBMISSION_FILE


def read_submission(perf_root: str | Path) -> Optional[PlanSubmission]:
    """Read the persisted submission identity, ignoring a malformed file."""
    try:
        value = json.loads(submission_path(perf_root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    try:
        return PlanSubmission.model_validate(value)
    except ValueError:
        return None


def write_submission(perf_root: str | Path, submission: PlanSubmission) -> PlanSubmission:
    """Persist the submission identity atomically."""
    updated = submission.model_copy(update={'updated_ns': time.time_ns()})
    write_experiment_json(submission_path(perf_root), updated.model_dump(mode='json'))
    return updated


def reserve_submission(
    perf_root: str | Path,
    compiled: CompiledPerfPlan,
    *,
    task_id: str,
    source_text: Optional[str] = None,
    alignment: Optional[PlanAlignment] = None,
) -> PlanSubmission:
    """Reserve one task ID for one accepted plan inside the ID-level critical section.

    ``os.mkdir`` of the plan directory is the lock: exactly one request can
    create it. The loser re-reads the winner's ``compiled.json`` and either
    joins an identical reservation or reports a conflict.
    """
    root = Path(perf_root)
    try:
        create_plan_dir(root)
    except FileExistsError:
        archived = read_compiled_plan(root)
        if archived.plan_hash != compiled.plan_hash:
            raise PlanIdentityError('This task ID already holds a different plan; use a new task ID') from None
        existing = read_submission(root)
        if existing is not None:
            return existing
        now = time.time_ns()
        return write_submission(
            root,
            PlanSubmission(
                task_id=task_id,
                plan_hash=compiled.plan_hash,
                normalized_hash=compiled.normalized_hash,
                semantic_fingerprint=compiled.semantic_fingerprint,
                compiler_version=compiled.compiler_version,
                contract_version=compiled.contract_version,
                reserved_ns=now,
                updated_ns=now,
            ),
        )
    now = time.time_ns()
    write_plan_archive(root, compiled, source_text=source_text, alignment=alignment)
    return write_submission(
        root,
        PlanSubmission(
            task_id=task_id,
            plan_hash=compiled.plan_hash,
            normalized_hash=compiled.normalized_hash,
            semantic_fingerprint=compiled.semantic_fingerprint,
            compiler_version=compiled.compiler_version,
            contract_version=compiled.contract_version,
            reserved_ns=now,
            updated_ns=now,
        ),
    )


# ---------------------------------------------------------------------------
# Effective runtime identity (published after execution starts)
# ---------------------------------------------------------------------------


class TokenizerIdentity(BaseArgument):
    """What was actually resolved, never what a path suggests."""

    status: Literal['resolved', 'unknown'] = 'unknown'
    content_hash: Optional[str] = None
    template_hash: Optional[str] = None
    path: Optional[str] = None


class EffectiveIdentity(BaseArgument):
    """Execution-time identity, kept apart from the accepted plan hash.

    ``identity_version`` versions the comparison semantics. A comparison of two
    records with different versions, or with unknown content hashes, is
    ``unknown``: an equal unknown fingerprint never means "matched".
    """

    schema_version: Literal['1.0'] = EFFECTIVE_SCHEMA_VERSION
    identity_version: Literal['1'] = EFFECTIVE_IDENTITY_VERSION
    plan_hash: str
    parent_plan_hash: Optional[str] = None
    contract_version: str
    compiler_version: str
    tokenizer: TokenizerIdentity = TokenizerIdentity()
    aiperf_version: Optional[str] = None
    executor: Literal['evalscope_service'] = 'evalscope_service'
    deployment_evidence: Literal['declared', 'measured', 'unknown'] = 'unknown'


def write_effective_identity(perf_root: str | Path, identity: EffectiveIdentity) -> Path:
    """Publish the runtime identity atomically; the accepted hash is untouched."""
    path = plan_dir(perf_root) / EFFECTIVE_FILE
    write_experiment_json(path, identity.model_dump(mode='json'))
    return path


def read_effective_identity(perf_root: str | Path) -> Optional[EffectiveIdentity]:
    try:
        value = json.loads((plan_dir(perf_root) / EFFECTIVE_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    try:
        return EffectiveIdentity.model_validate(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def read_compiled_plan(perf_root: str | Path) -> CompiledPerfPlan:
    """Read the archived compiled plan; missing or malformed archives are an error."""
    path = plan_dir(perf_root) / 'compiled.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise PlanArchiveError('Plan archive is missing compiled.json') from None
    except ValueError:
        raise PlanArchiveError('Plan archive compiled.json is not valid JSON') from None
    return CompiledPerfPlan.model_validate(value)


def _compiled_value(perf_root: str | Path) -> Optional[dict]:
    path = plan_dir(perf_root) / 'compiled.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def read_plan_hash(perf_root: str | Path) -> Optional[str]:
    """Return the archived plan hash without requiring the full document."""
    value = _compiled_value(perf_root)
    plan_hash = value.get('plan_hash') if value else None
    return plan_hash if isinstance(plan_hash, str) else None


def read_normalized_hash(perf_root: str | Path) -> Optional[str]:
    """Return the archived normalized hash, used to detect a reused task ID."""
    value = _compiled_value(perf_root)
    normalized_hash = value.get('normalized_hash') if value else None
    return normalized_hash if isinstance(normalized_hash, str) else None
