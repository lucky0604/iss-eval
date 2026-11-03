"""Execute one prefix-cache scenario with an explicit primer barrier."""

import asyncio
import json
import multiprocessing
import os
import time
from pathlib import Path
from typing import Callable, Literal, Optional, Sequence

from pydantic import Field, field_validator

from evalscope.perf.executors.arguments import AIPerfArguments
from evalscope.perf.executors.external import Cancellation
from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome
from evalscope.perf.experiments.prefix_workload import (
    PrefixTokenizer,
    PrefixWorkloadArguments,
    WorkloadCancelled,
    build_prefix_workload,
)
from evalscope.perf.experiments.priming import PrimingResult, prime_prefixes
from evalscope.perf.experiments.storage import write_experiment_json
from evalscope.perf.integrations.aiperf.metrics import NodeMetricsAnalysis, analyze_node_metrics
from evalscope.perf.integrations.aiperf.single_run import (
    AIPerfCancelled,
    _run_verified,
    probe_version,
    validate_single_run_configuration,
)
from evalscope.perf.results.aiperf_import import ImportCancelled, import_aiperf_run, observed_cache_usage
from evalscope.perf.results.models import AIPerfImportArguments, CacheObservation, MetricTargetReference
from evalscope.utils import BaseArgument


class CacheScenarioArguments(BaseArgument):
    """Identity and target for one member of a cache experiment matrix."""

    target_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    namespace: str = Field(min_length=1)
    tokenizer_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    tolerance_tokens: int = Field(default=0, ge=0, strict=True)

    @field_validator('target_ratio', mode='before')
    @classmethod
    def reject_boolean_ratio(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError('Scenario ratio must be numeric')
        return value


class CacheScenarioOutcome(BaseArgument):
    """Small task result with relative references to persistent artifacts."""

    status: Literal['completed', 'error', 'cancelled']
    quality: Literal['partial', 'invalid']
    reason: Optional[str] = None
    cleanup_confirmed: bool = False
    manifest: str = 'scenario.json'
    priming: Optional[PrimingResult] = None
    execution: Optional[ExecutionOutcome] = None
    cache: Optional[CacheObservation] = None


class _ScenarioCancellation:
    def __init__(self, requested: Optional[Cancellation]) -> None:
        self.requested = requested
        self.parent = multiprocessing.parent_process()

    def is_set(self) -> bool:
        return bool(
            (self.requested is not None and self.requested.is_set())
            or (self.parent is not None and not self.parent.is_alive())
        )


def run_cache_scenario(
    arguments: AIPerfArguments,
    context: ExecutionContext,
    scenario: CacheScenarioArguments,
    tokenizer: PrefixTokenizer,
    token_pool: Sequence[int],
    cancel_event: Optional[Cancellation] = None,
    on_phase: Optional[Callable[[str], None]] = None,
) -> CacheScenarioOutcome:
    """Run one scenario; the synchronous caller must own task cancellation."""
    cache_config = arguments.cache_experiment
    if cache_config is None or scenario.target_ratio not in cache_config.target_ratios:
        raise ValueError('Scenario target is not in the configured cache experiment')
    if getattr(tokenizer, 'tokenizer_hash', scenario.tokenizer_id) != scenario.tokenizer_id:
        raise ValueError('Tokenizer identity does not match the scenario')
    if getattr(tokenizer, 'template_hash', scenario.template_id) != scenario.template_id:
        raise ValueError('Tokenizer template identity does not match the scenario')
    single = arguments.model_copy(update={'cache_experiment': None})
    validate_single_run_configuration(single)
    root = Path(context.output_dir).resolve(strict=True)
    if any((root / path).exists() for path in ('scenario.json', 'prefix_inputs.jsonl', 'raw/aiperf', 'normalized')):
        raise ValueError('Scenario output already exists')
    manifest_path = root / 'scenario.json'
    fd = os.open(manifest_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    os.close(fd)
    state = {
        'schema_version': '1.0',
        'status': 'preflight',
        'target_ratio': scenario.target_ratio,
        'namespace': scenario.namespace,
        'routing_coverage': 'unknown',
        'template_match': 'unverified',
        'telemetry_sample_interval_seconds': arguments.telemetry.sample_interval_seconds,
        'telemetry_max_gap_seconds': (
            arguments.telemetry.max_scrape_gap_seconds or 2 * arguments.telemetry.sample_interval_seconds
        ),
        'timestamps': {
            'priming_completed_ns': None,
            'profiling_launch_ns': None,
            'profiling_start_ns': None,
            'dispatch_stopped_ns': None,
            'profiling_end_ns': None,
            'draining_started_ns': None,
            'final_scrape_ns': None,
            'import_completed_ns': None,
        },
    }

    def publish() -> None:
        write_experiment_json(manifest_path, state)
        if on_phase is not None:
            on_phase(state['status'])

    def publish_node_analysis(normalized: Path) -> Optional[NodeMetricsAnalysis]:
        if not arguments.telemetry.targets:
            return None
        gap = arguments.telemetry.max_scrape_gap_seconds or 2 * arguments.telemetry.sample_interval_seconds
        try:
            analysis = analyze_node_metrics(str(normalized), max_gap_ns=max(1, int(gap * 1_000_000_000)))
            write_experiment_json(root / 'node_analysis.json', analysis.model_dump(mode='json'))
            state['node_analysis'] = 'node_analysis.json'
            return analysis
        except (OSError, ValueError):
            state['node_analysis_reason'] = 'node_analysis_failed'
            return None

    def missing_required_nodes(analysis: Optional[NodeMetricsAnalysis]) -> bool:
        if not arguments.telemetry.required:
            return False
        if analysis is None or not arguments.telemetry.targets:
            return True
        covered = {
            row.node_id
            for row in analysis.series
            if (row.statistic is not None and row.statistic.value is not None)
            or (row.histogram is not None and row.histogram.count is not None)
        }
        return bool({target.node_id for target in arguments.telemetry.targets} - covered)

    publish()
    cancellation = _ScenarioCancellation(cancel_event)
    priming = None
    execution = None
    try:
        version = probe_version(context, cancellation)
        if cancellation.is_set():
            raise AIPerfCancelled()
        state['status'] = 'generating'
        publish()
        plan = PrefixWorkloadArguments(
            target_ratio=scenario.target_ratio,
            total_input_tokens=cache_config.total_input_tokens,
            request_count=arguments.load.request_count,
            prefix_pool_size=cache_config.prefix_pool_size,
            seed=cache_config.seed,
            namespace=scenario.namespace,
            tokenizer_id=scenario.tokenizer_id,
            template_id=scenario.template_id,
            tolerance_tokens=scenario.tolerance_tokens,
        )
        workload = build_prefix_workload(plan, tokenizer, token_pool, cancellation)
        state['workload'] = workload.model_dump(
            exclude={'primers': {'__all__': {'text'}}, 'requests': {'__all__': {'text'}}}
        )
        input_path = root / 'prefix_inputs.jsonl'
        fd = os.open(input_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            for item in workload.requests:
                if cancellation.is_set():
                    raise WorkloadCancelled()
                file.write(json.dumps({'text': item.text, 'output_length': arguments.load.output_token_limit}) + '\n')
        state['status'] = 'priming'
        publish()
        priming = asyncio.run(prime_prefixes(single, workload, cancel_event=cancellation))
        state['priming'] = priming.model_dump(mode='json')
        if priming.status == 'cancelled' or cancellation.is_set():
            raise WorkloadCancelled()
        if priming.status not in ('completed', 'skipped'):
            state.update({'status': 'error', 'reason': 'primer_failed'})
            publish()
            return CacheScenarioOutcome(
                status='error', quality='invalid', reason='primer_failed', priming=priming, cleanup_confirmed=True
            )
        if priming.status == 'completed':
            state['timestamps']['priming_completed_ns'] = time.time_ns()
        state['status'] = 'profiling'
        state['timestamps']['profiling_launch_ns'] = time.time_ns()
        publish()

        def draining() -> None:
            state['status'] = 'draining'
            state['timestamps']['draining_started_ns'] = time.time_ns()
            publish()

        execution = _run_verified(single, context, str(input_path), version, cancellation, on_draining=draining)
        state['execution'] = execution.model_dump(mode='json', exclude={'raw_artifact_dir'})
        if not execution.cleanup_confirmed:
            state.update({'status': 'error', 'reason': 'cleanup_unconfirmed'})
            publish()
            return CacheScenarioOutcome(
                status='error', quality='invalid', reason='cleanup_unconfirmed', priming=priming, execution=execution
            )
        if execution.exit_reason == 'cancelled' or cancellation.is_set():
            raise WorkloadCancelled()
        if execution.exit_reason != 'completed' or execution.artifact_status != 'available':
            state.update({'status': 'error', 'reason': 'profiling_failed'})
            if execution.raw_artifact_dir and (Path(execution.raw_artifact_dir) / 'profile_export.jsonl').is_file():
                try:
                    partial = import_aiperf_run(
                        AIPerfImportArguments(
                            raw_dir=execution.raw_artifact_dir,
                            normalized_dir=str(root / 'normalized'),
                            allow_partial_tail=True,
                            metrics_required=arguments.telemetry.required,
                            targets=[
                                MetricTargetReference(
                                    node_id=target.node_id,
                                    role=target.role,
                                    engine=target.engine,
                                    url=target.url,
                                    labels=target.labels,
                                )
                                for target in arguments.telemetry.targets
                            ],
                        ),
                        cancel_event=cancellation,
                    )
                    state.update(
                        {
                            'normalized': 'normalized',
                            'cache': observed_cache_usage(str(root / 'normalized')).model_dump(mode='json'),
                            'quality_reasons': [*partial.quality_reasons, 'profiling_failed'],
                        }
                    )
                    state['timestamps'].update(
                        {
                            'profiling_start_ns': partial.profiling_start_ns,
                            'profiling_end_ns': partial.profiling_end_ns,
                            'import_completed_ns': time.time_ns(),
                        }
                    )
                    publish_node_analysis(root / 'normalized')
                except (ValueError, OSError):
                    state['quality_reasons'] = ['profiling_failed', 'partial_import_failed']
            publish()
            return CacheScenarioOutcome(
                status='error',
                quality='invalid',
                reason='profiling_failed',
                priming=priming,
                execution=execution,
                cleanup_confirmed=True,
            )
        state['status'] = 'importing'
        publish()
        assert execution.raw_artifact_dir is not None
        normalized = root / 'normalized'
        result = import_aiperf_run(
            AIPerfImportArguments(
                raw_dir=execution.raw_artifact_dir,
                normalized_dir=str(normalized),
                metrics_required=arguments.telemetry.required,
                targets=[
                    MetricTargetReference(
                        node_id=target.node_id,
                        role=target.role,
                        engine=target.engine,
                        url=target.url,
                        labels=target.labels,
                    )
                    for target in arguments.telemetry.targets
                ],
            ),
            cancel_event=cancellation,
        )
        observation = observed_cache_usage(str(normalized))
        node_analysis = publish_node_analysis(normalized)
        state['timestamps'].update(
            {
                'profiling_start_ns': result.profiling_start_ns,
                'profiling_end_ns': result.profiling_end_ns,
                'import_completed_ns': time.time_ns(),
            }
        )
        quality_reasons = [*result.quality_reasons, 'template_match_unverified', 'routing_coverage_unknown']
        if result.profiling_requests != arguments.load.request_count:
            quality_reasons.append('planned_request_count_mismatch')
        quality_reasons.append(
            'telemetry_mapping_unverified' if arguments.telemetry.targets else 'telemetry_not_configured'
        )
        telemetry_missing = arguments.telemetry.required and (
            result.server_metrics_schema is None
            or not (Path(execution.raw_artifact_dir) / 'server_metrics_export.jsonl').is_file()
            or missing_required_nodes(node_analysis)
        )
        if telemetry_missing:
            quality_reasons.append('required_telemetry_missing')
        quality = 'invalid' if telemetry_missing or result.quality == 'invalid' else 'partial'
        status = 'error' if quality == 'invalid' else 'completed'
        state.update(
            {
                'status': status,
                'normalized': 'normalized',
                'cache': observation.model_dump(mode='json'),
                'quality': quality,
                'quality_reasons': quality_reasons,
            }
        )
        publish()
        return CacheScenarioOutcome(
            status=status,
            quality=quality,
            priming=priming,
            execution=execution,
            cache=observation,
            cleanup_confirmed=True,
        )
    except (AIPerfCancelled, WorkloadCancelled, ImportCancelled):
        state.update({'status': 'cancelled', 'reason': 'cancelled'})
        cancelled_quality = 'partial'
        if execution is not None and execution.cleanup_confirmed and execution.raw_artifact_dir:
            raw = Path(execution.raw_artifact_dir).resolve()
            if (
                raw == (root / 'raw' / 'aiperf').resolve()
                and raw.is_relative_to(root)
                and (raw / 'profile_export.jsonl').is_file()
            ):
                try:
                    partial = import_aiperf_run(
                        AIPerfImportArguments(
                            raw_dir=str(raw),
                            normalized_dir=str(root / 'normalized'),
                            allow_partial_tail=True,
                            metrics_required=arguments.telemetry.required,
                            targets=[
                                MetricTargetReference(
                                    node_id=target.node_id,
                                    role=target.role,
                                    engine=target.engine,
                                    url=target.url,
                                    labels=target.labels,
                                )
                                for target in arguments.telemetry.targets
                            ],
                        ),
                        cancel_event=_ScenarioCancellation(None),
                    )
                    cancelled_quality = 'invalid' if partial.quality == 'invalid' else 'partial'
                    state.update(
                        {
                            'normalized': 'normalized',
                            'quality': cancelled_quality,
                            'quality_reasons': [*partial.quality_reasons, 'cancelled'],
                        }
                    )
                    state['timestamps'].update(
                        {
                            'profiling_start_ns': partial.profiling_start_ns,
                            'profiling_end_ns': partial.profiling_end_ns,
                            'import_completed_ns': time.time_ns(),
                        }
                    )
                    node_analysis = publish_node_analysis(root / 'normalized')
                    if missing_required_nodes(node_analysis):
                        cancelled_quality = 'invalid'
                        state['quality'] = cancelled_quality
                        state['quality_reasons'].append('required_telemetry_missing')
                except (OSError, ValueError, ImportCancelled):
                    state['quality_reasons'] = ['cancelled', 'partial_import_failed']
        publish()
        return CacheScenarioOutcome(
            status='cancelled',
            quality=cancelled_quality,
            reason='cancelled',
            priming=priming,
            execution=execution,
            cleanup_confirmed=True,
        )
    except Exception:
        state.update({'status': 'error', 'reason': f'{state["status"]}_failed'})
        publish()
        return CacheScenarioOutcome(
            status='error', quality='invalid', reason=state['reason'], priming=priming, execution=execution
        )
