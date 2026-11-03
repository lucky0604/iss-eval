"""Prepare and execute one restricted AIPerf 0.13.0 profiling run."""

import json
import os
import tempfile
from pathlib import Path
from threading import Event, Timer
from typing import Callable, Optional

from pydantic import Field

from evalscope.perf.executors.arguments import AIPerfArguments
from evalscope.perf.executors.external import Cancellation, run_external
from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome, ExternalInvocation
from evalscope.perf.executors.urls import normalize_chat_url, normalize_metrics_url
from evalscope.perf.integrations.aiperf.metrics_relay import MetricsRelayRoutes, metrics_auth_relay
from evalscope.utils import BaseArgument


class PreparedAIPerfRun(BaseArgument):
    """Internal reference to a prepared invocation and its private config."""

    invocation: ExternalInvocation
    config_file: str = Field(min_length=1)
    artifact_dir: str = Field(min_length=1)


class AIPerfSingleRunRequest(BaseArgument):
    """Validated single-run settings and a service-owned input file."""

    arguments: AIPerfArguments
    input_file: str = Field(min_length=1)


class _CombinedCancellation:
    def __init__(self, timeout: Event, requested: Optional[Cancellation]) -> None:
        self.timeout = timeout
        self.requested = requested

    def is_set(self) -> bool:
        return self.timeout.is_set() or (self.requested is not None and self.requested.is_set())


class AIPerfCancelled(Exception):
    """A preflight was cancelled and its subprocess cleanup was confirmed."""


def probe_version(
    context: ExecutionContext, cancel_event: Optional[Cancellation] = None, timeout_seconds: float = 10
) -> str:
    """Validate the configured executable without loading AIPerf in-process."""
    if context.runtime is None:
        raise ValueError('AIPerf runtime is not configured')
    if timeout_seconds <= 0:
        raise ValueError('Version probe timeout must be positive')
    root = Path(context.output_dir).resolve(strict=True)
    with tempfile.NamedTemporaryFile(dir=root, prefix='.aiperf-version-', delete=False) as file:
        version_log = Path(file.name)
    timeout = Event()
    cancel = _CombinedCancellation(timeout, cancel_event)
    timer = Timer(timeout_seconds, timeout.set)
    try:
        timer.start()
        outcome = run_external(
            context,
            ExternalInvocation(
                arguments=['--version'], working_dir=str(root), log_file=str(version_log), max_log_bytes=1024
            ),
            cancel_event=cancel,
        )
        version = version_log.read_text(encoding='utf-8').strip()
    except (OSError, RuntimeError):
        raise RuntimeError('AIPerf executable is unavailable') from None
    finally:
        timer.cancel()
        version_log.unlink(missing_ok=True)
    if cancel_event is not None and cancel_event.is_set() and outcome.cleanup_confirmed:
        raise AIPerfCancelled()
    if (
        not outcome.cleanup_confirmed
        or outcome.exit_reason != 'completed'
        or version != context.runtime.expected_version
    ):
        raise RuntimeError('Unsupported AIPerf runtime version')
    return version


def _validate_input(path: Path, root: Path, arguments: AIPerfArguments) -> None:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise ValueError('Input must be a file inside the task directory')
    count = 0
    with resolved.open(encoding='utf-8') as dataset:
        for line in dataset:
            try:
                row = json.loads(line)
            except ValueError:
                raise ValueError('Invalid single-turn input') from None
            if (
                not isinstance(row, dict)
                or set(row) != {'text', 'output_length'}
                or not isinstance(row['text'], str)
                or not row['text']
                or type(row['output_length']) is not int
                or row['output_length'] != arguments.load.output_token_limit
            ):
                raise ValueError('Single-turn inputs must contain text and the configured output limit')
            count += 1
    if count != arguments.load.request_count:
        raise ValueError('Input count must match the profiling request count')


def validate_single_run_configuration(arguments: AIPerfArguments) -> None:
    """Reject capabilities that have not yet been implemented for single runs."""
    if arguments.cache_experiment is not None or len(arguments.load.points) != 1 or arguments.load.repetitions != 1:
        raise ValueError('Experiment orchestration is not available in the single-run executor')
    if not arguments.stream or arguments.headers:
        raise ValueError('This executor currently supports streaming without custom inference headers')
    endpoints = [normalize_metrics_url(target.url) for target in arguments.telemetry.targets]
    if len(set(endpoints)) != len(endpoints):
        raise ValueError('Metrics endpoints must be unique')
    if arguments.telemetry.targets and not 0.001 <= arguments.telemetry.sample_interval_seconds <= 300:
        raise ValueError('Metrics interval is outside the AIPerf 0.13.0 range')
    normalize_chat_url(arguments.url)


def prepare_single_run(
    arguments: AIPerfArguments, context: ExecutionContext, input_file: str, relay: Optional[MetricsRelayRoutes] = None
) -> PreparedAIPerfRun:
    """Create a private AIPerf config for one validated, server-owned dataset."""
    if context.runtime is None:
        raise ValueError('AIPerf runtime is not configured')
    validate_single_run_configuration(arguments)
    root = Path(context.output_dir).resolve(strict=True)
    _validate_input(Path(input_file), root, arguments)
    base, endpoint = normalize_chat_url(arguments.url)
    artifacts = (root / 'raw' / 'aiperf').resolve()
    if not artifacts.is_relative_to(root):
        raise ValueError('Artifact directory must be inside the task directory')
    if artifacts.exists():
        raise ValueError('Artifact directory already exists')
    point = arguments.load.points[0]
    phase = {'name': 'profiling', 'kind': 'profiling', 'requests': arguments.load.request_count}
    if arguments.load.mode == 'closed_loop':
        phase.update({'type': 'concurrency', 'concurrency': point.concurrency})
    else:
        phase.update({'type': 'poisson', 'rate': point.rate})
    endpoint_config = {'urls': [base], 'path': endpoint, 'type': 'chat', 'streaming': True}
    environment = {}
    if arguments.api_key is not None:
        endpoint_config['api_key'] = '${EVALSCOPE_AIPERF_API_KEY}'
        environment['EVALSCOPE_AIPERF_API_KEY'] = arguments.api_key
    targets = arguments.telemetry.targets
    if any(target.headers and (relay is None or target.node_id not in relay.collector_urls) for target in targets):
        raise ValueError('Authenticated metrics targets require an active relay')
    if targets:
        environment['AIPERF_SERVER_METRICS_COLLECTION_INTERVAL'] = str(arguments.telemetry.sample_interval_seconds)
    config = {
        'schemaVersion': '2.0',
        'benchmark': {
            'models': {'items': [{'name': arguments.model}]},
            'endpoint': endpoint_config,
            'datasets': [
                {
                    'name': 'main',
                    'type': 'file',
                    'path': str(Path(input_file).resolve()),
                    'format': 'single_turn',
                    'entries': arguments.load.request_count,
                }
            ],
            'phases': [phase],
            'tokenizer': {'name': arguments.tokenizer_path},
            'server_metrics': {
                'enabled': bool(targets),
                'urls': [
                    relay.collector_urls.get(target.node_id, target.url) if relay else target.url for target in targets
                ],
                'formats': ['json', 'jsonl'],
            },
            'artifacts': {'dir': str(artifacts), 'records': ['jsonl'], 'raw': True, 'auto_plot': False},
        },
    }
    artifacts.mkdir(mode=0o700, parents=True, exist_ok=False)
    config_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=root, prefix='.aiperf-config-', suffix='.json', delete=False
        ) as file:
            config_file = file.name
            os.fchmod(file.fileno(), 0o600)
            json.dump(config, file)
        invocation = ExternalInvocation(
            arguments=['profile', '--config', config_file],
            working_dir=str(root),
            log_file=str(root / 'benchmark.log'),
            environment=environment,
        )
    except BaseException:
        if config_file is not None:
            Path(config_file).unlink(missing_ok=True)
        artifacts.rmdir()
        raise
    return PreparedAIPerfRun(invocation=invocation, config_file=config_file, artifact_dir=str(artifacts))


def run_single(
    arguments: AIPerfArguments, context: ExecutionContext, input_file: str, cancel_event: Optional[Cancellation] = None
) -> ExecutionOutcome:
    """Execute a restricted single run and preserve incomplete raw artifacts."""
    validate_single_run_configuration(arguments)
    try:
        version = probe_version(context, cancel_event)
    except AIPerfCancelled:
        return ExecutionOutcome(exit_reason='cancelled', artifact_status='missing', cleanup_confirmed=True)
    return _run_verified(arguments, context, input_file, version, cancel_event)


def _run_verified(
    arguments: AIPerfArguments,
    context: ExecutionContext,
    input_file: str,
    version: str,
    cancel_event: Optional[Cancellation] = None,
    on_draining: Optional[Callable[[], None]] = None,
) -> ExecutionOutcome:
    with metrics_auth_relay(arguments.telemetry.targets) as relay:
        prepared = prepare_single_run(arguments, context, input_file, relay)
        try:
            if relay.collector_urls:
                mapping = Path(prepared.artifact_dir) / 'metrics_target_map.json'
                fd = os.open(mapping, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as file:
                    json.dump(
                        {
                            'schema_version': '1.0',
                            'targets': {
                                target.node_id: {
                                    'collector_url': relay.collector_urls[target.node_id],
                                    'endpoint_url': normalize_metrics_url(target.url),
                                }
                                for target in arguments.telemetry.targets
                                if target.node_id in relay.collector_urls
                            },
                        },
                        file,
                    )
            outcome = run_external(context, prepared.invocation, cancel_event=cancel_event, on_draining=on_draining)
            directory = Path(prepared.artifact_dir)
            has_summary = (directory / 'profile_export_aiperf.json').is_file()
            has_records = (directory / 'profile_export.jsonl').is_file()
            status = (
                'available'
                if has_summary and has_records
                else (
                    'partial'
                    if directory.is_dir()
                    and any(item.name != 'metrics_target_map.json' for item in directory.iterdir())
                    else 'missing'
                )
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
        finally:
            Path(prepared.config_file).unlink(missing_ok=True)
