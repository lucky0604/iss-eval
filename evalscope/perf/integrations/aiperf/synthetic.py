"""Controlled AIPerf synthetic compatibility run (shared system + unique user).

The legacy migration source drove AIPerf from flags, not from a config file:

``aiperf profile --model ... --tokenizer ... --endpoint-type chat --url ...
--shared-system-prompt-length S --isl U --osl 1000 --streaming
--request-count N --concurrency C --output-artifact-dir DIR``

This module rebuilds that invocation from structured values only. Every flag
comes from an allow-list, so a plan cannot inject an arbitrary command, and the
request shape stays ``system`` + ``user`` instead of one concatenated prompt.
"""

from pathlib import Path
from typing import List, Literal, Optional

from pydantic import Field, StrictInt

from evalscope.perf.executors.arguments import AIPerfArguments
from evalscope.perf.executors.external import Cancellation, run_external
from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome, ExternalInvocation
from evalscope.perf.executors.urls import normalize_chat_url
from evalscope.utils import BaseArgument

from .single_run import AIPerfCancelled, probe_version

SUPPORTED_AIPERF_VERSION = '0.13.0'


class SyntheticRunSpec(BaseArgument):
    """One legacy-shaped synthetic point; values are explicit, never re-derived."""

    case_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1)
    url: str = Field(min_length=1)
    tokenizer_path: str = Field(min_length=1)
    shared_system_tokens: StrictInt = Field(ge=0)
    unique_user_tokens: StrictInt = Field(gt=0)
    output_token_limit: StrictInt = Field(gt=0)
    request_count: StrictInt = Field(gt=0)
    concurrency: StrictInt = Field(gt=0)
    stream: bool = True
    apply_chat_template: Literal[False] = False
    cache_bust: Literal['none'] = 'none'


class PreparedSyntheticRun(BaseArgument):
    """A server-built invocation and its private artifact directory."""

    invocation: ExternalInvocation
    artifact_dir: str = Field(min_length=1)


def validate_synthetic_spec(spec: SyntheticRunSpec, arguments: Optional[AIPerfArguments] = None) -> None:
    """Reject every capability this compatibility path does not reproduce."""
    if not spec.stream:
        raise ValueError('synthetic compatibility requires streaming chat completions')
    if spec.apply_chat_template:
        raise ValueError('apply_chat_template=true is not reproduced by the synthetic compatibility path')
    if spec.cache_bust != 'none':
        raise ValueError('cache_bust is not supported by the synthetic compatibility path')
    if arguments is not None:
        if arguments.api_key is not None:
            raise ValueError(
                'synthetic compatibility does not pass an API key on the command line; '
                'use the single-run executor for authenticated endpoints'
            )
        if arguments.headers:
            raise ValueError('synthetic compatibility does not support custom inference headers')
    normalize_chat_url(spec.url)


def build_synthetic_arguments(spec: SyntheticRunSpec, artifact_dir: str) -> List[str]:
    """Return the exact flag vector for one synthetic run.

    ``--isl`` is the unique user length and ``--shared-system-prompt-length`` is
    the shared system prompt length, matching the legacy script's split.
    """
    validate_synthetic_spec(spec)
    origin, endpoint = normalize_chat_url(spec.url)
    # ``--endpoint-type chat`` appends the endpoint path itself, so the base must
    # keep the API prefix (``/v1``) that the legacy script passed verbatim.
    base = f'{origin}{endpoint[: -len("/chat/completions")]}'
    return [
        'profile',
        '--model',
        spec.model,
        '--tokenizer',
        spec.tokenizer_path,
        '--endpoint-type',
        'chat',
        '--url',
        base,
        '--shared-system-prompt-length',
        str(spec.shared_system_tokens),
        '--isl',
        str(spec.unique_user_tokens),
        '--osl',
        str(spec.output_token_limit),
        '--streaming',
        '--request-count',
        str(spec.request_count),
        '--concurrency',
        str(spec.concurrency),
        '--output-artifact-dir',
        artifact_dir,
    ]


def prepare_synthetic_run(spec: SyntheticRunSpec, context: ExecutionContext) -> PreparedSyntheticRun:
    """Create the private artifact directory and the server-owned invocation."""
    if context.runtime is None:
        raise ValueError('AIPerf runtime is not configured')
    root = Path(context.output_dir).resolve(strict=True)
    artifacts = (root / 'raw' / 'aiperf').resolve()
    if not artifacts.is_relative_to(root):
        raise ValueError('Artifact directory must be inside the task directory')
    if artifacts.exists():
        raise ValueError('Artifact directory already exists')
    artifacts.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        invocation = ExternalInvocation(
            arguments=build_synthetic_arguments(spec, str(artifacts)),
            working_dir=str(root),
            log_file=str(root / 'benchmark.log'),
        )
    except BaseException:
        artifacts.rmdir()
        raise
    return PreparedSyntheticRun(invocation=invocation, artifact_dir=str(artifacts))


def run_synthetic(
    spec: SyntheticRunSpec,
    context: ExecutionContext,
    cancel_event: Optional[Cancellation] = None,
) -> ExecutionOutcome:
    """Execute one synthetic run with the shared watchdog and artifact checks."""
    validate_synthetic_spec(spec)
    try:
        version = probe_version(context, cancel_event)
    except AIPerfCancelled:
        return ExecutionOutcome(exit_reason='cancelled', artifact_status='missing', cleanup_confirmed=True)
    prepared = prepare_synthetic_run(spec, context)
    outcome = run_external(context, prepared.invocation, cancel_event=cancel_event)
    directory = Path(prepared.artifact_dir)
    has_summary = (directory / 'profile_export_aiperf.json').is_file()
    has_records = (directory / 'profile_export.jsonl').is_file()
    status = (
        'available'
        if has_summary and has_records
        else ('partial' if directory.is_dir() and any(directory.iterdir()) else 'missing')
    )
    reason = 'error' if outcome.exit_reason == 'completed' and status != 'available' else outcome.exit_reason
    return outcome.model_copy(
        update={
            'exit_reason': reason,
            'artifact_status': status,
            'executor_version': version,
            'raw_artifact_dir': str(directory) if status != 'missing' else None,
        }
    )
