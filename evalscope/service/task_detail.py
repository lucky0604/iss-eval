"""Projection of indexed task records into the Task Center contracts.

Everything here is read-only and cheap: capability detection only stats a
handful of archive markers, and the configuration snapshot is built from the
already-persisted plan/manifest files. No result archive is fully loaded, so a
task list page never pays for twenty-five experiment parses.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from evalscope.utils.logger import get_logger
from evalscope.utils.uri_utils import sanitize_endpoint

from . import task_history
from .api_models import (
    AiPerfSingleConfigSnapshot,
    AvailabilityState,
    CacheExperimentConfigSnapshot,
    EvalConfigSnapshot,
    NativePerfConfigSnapshot,
    PlanConfigSnapshot,
    ResultAvailability,
    ResultCapabilities,
    TaskDetailResponse,
    TaskSummaryItem,
)
from .utils import task_file_path

logger = get_logger()

_ACTIVE_STATUSES = {'queued', 'preparing', 'running', 'cancelling', 'finalizing'}
_MAX_JSON_BYTES = 16 * 1024 * 1024
_PERF_RUN_PREFIXES = ('parallel_', 'rate_')


def _state(availability: str, reason: Optional[str] = None) -> AvailabilityState:
    return AvailabilityState(availability=availability, reason=reason)


def _unsupported(reason: str = 'not_supported') -> AvailabilityState:
    return _state('unsupported', reason)


def _read_json(task_id: str, sub_path: str) -> Optional[Dict[str, Any]]:
    try:
        path = task_file_path(task_id, sub_path)
        if os.path.getsize(path) > _MAX_JSON_BYTES:
            return None
        with open(path, encoding='utf-8') as file:
            payload = json.load(file)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _child_dirs(path: str) -> List[str]:
    try:
        with os.scandir(path) as entries:
            return [entry.name for entry in entries if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def _has_perf_run(perf_dir: str) -> bool:
    for name in _child_dirs(perf_dir):
        if not name.startswith(_PERF_RUN_PREFIXES):
            continue
        if os.path.isfile(os.path.join(perf_dir, name, 'benchmark_summary.json')):
            return True
    return False


def _has_request_db(perf_dir: str) -> bool:
    if os.path.isfile(os.path.join(perf_dir, 'benchmark_data.db')):
        return True
    return any(
        os.path.isfile(os.path.join(perf_dir, name, 'benchmark_data.db'))
        for name in _child_dirs(perf_dir)
        if name.startswith(_PERF_RUN_PREFIXES)
    )


def _single_run_manifest(task_id: str) -> Optional[Dict[str, Any]]:
    return _read_json(task_id, 'perf/single_run.json')


def _experiment_plan(task_id: str) -> Optional[Dict[str, Any]]:
    return _read_json(task_id, 'perf/experiment_plan.json')


def _plan_location(task_id: str):
    """Return ``(perf_root, plan_directory, legacy)`` when this task holds a plan."""
    from evalscope.perf.plans.provenance import resolve_plan_dir

    try:
        perf_root = task_file_path(task_id, 'perf')
    except ValueError:
        return None
    directory, legacy = resolve_plan_dir(os.path.dirname(perf_root))
    if not directory.is_dir():
        return None
    return perf_root, directory, legacy


def _plan_manifest(task_id: str) -> Optional[Dict[str, Any]]:
    location = _plan_location(task_id)
    if location is None:
        return None
    _, directory, _ = location
    path = os.path.join(str(directory), 'manifest.json')
    try:
        if os.path.getsize(path) > _MAX_JSON_BYTES:
            return None
        with open(path, encoding='utf-8') as file:
            payload = json.load(file)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _plan_run_requests(task_id: str) -> bool:
    """Return whether any planned run published a readable request store."""
    try:
        runs = task_file_path(task_id, 'perf/runs')
    except ValueError:
        return False
    for name in _child_dirs(runs):
        normalized = os.path.join(runs, name, 'normalized')
        if os.path.isfile(os.path.join(normalized, 'current.json')):
            return True
    return False


def _experiment_dirs(task_id: str) -> List[str]:
    try:
        runs = task_file_path(task_id, 'perf/runs')
    except ValueError:
        return []
    return [os.path.join(runs, name) for name in _child_dirs(runs)]


def detect_capabilities(record: Dict[str, Any]) -> Tuple[ResultAvailability, ResultCapabilities]:
    """Resolve every result surface from the archive itself, never from the engine label."""
    task_id = record['task_id']
    kind = record.get('kind') or 'eval'
    status = record.get('status') or 'unknown'
    active = status in _ACTIVE_STATUSES
    capabilities = ResultCapabilities(
        overview=_state('unavailable', 'archive_missing'),
        sweep=_unsupported(),
        requests=_unsupported(),
        node_metrics=_unsupported(),
        cache_experiment=_unsupported(),
        configuration=_state('unavailable', 'config_not_persisted'),
        html=_state('unavailable', 'html_not_generated'),
    )

    if kind == 'eval':
        reports_dir = task_file_path(task_id, 'reports')
        has_reports = os.path.isdir(reports_dir) and bool(_child_dirs(reports_dir) or _files(reports_dir))
        if has_reports:
            capabilities.overview = _state('available')
        elif active:
            capabilities.overview = _state('pending', 'task_not_finished')
        if os.path.isfile(os.path.join(reports_dir, 'report.html')):
            capabilities.html = _state('available')
        elif active:
            capabilities.html = _state('pending', 'task_not_finished')
        result_availability = capabilities.overview
        return _as_result_availability(result_availability, active), capabilities

    perf_dir = task_file_path(task_id, 'perf')
    plan = _experiment_plan(task_id)
    single = _single_run_manifest(task_id)
    plan_manifest = _plan_manifest(task_id)
    experiment_dirs = _experiment_dirs(task_id) if plan is not None else []

    if plan_manifest is not None or _plan_location(task_id) is not None:
        # A portable plan is its own container: one archive, many runs.
        has_manifest = plan_manifest is not None
        total_runs = plan_manifest.get('total_runs') if plan_manifest else None
        total_runs = total_runs if type(total_runs) is int else 0
        completed_runs = plan_manifest.get('completed_runs') if plan_manifest else None
        completed_runs = completed_runs if type(completed_runs) is int else 0
        capabilities.cache_experiment = _state('available')
        capabilities.configuration = _state('available')
        capabilities.sweep = _state('available') if total_runs > 1 else _unsupported('single_run')
        capabilities.requests = (
            _state('available') if _plan_run_requests(task_id) else _state('unavailable', 'no_requests')
        )
        capabilities.node_metrics = _unsupported('plan_telemetry_unsupported')
        if has_manifest:
            capabilities.overview = (
                _state('available') if completed_runs or total_runs else _state('pending', 'no_results')
            )
            capabilities.html = _state('available')
        else:
            capabilities.overview = _state('pending', 'task_not_finished')
            capabilities.html = _state('pending', 'task_not_finished')
    elif plan is not None:
        capabilities.cache_experiment = _state('available')
        capabilities.overview = _state('available')
        capabilities.sweep = _state('available')
        readable_dirs = [path for path in experiment_dirs if os.path.isdir(path)]
        if any(os.path.isdir(os.path.join(path, 'normalized')) for path in readable_dirs):
            capabilities.requests = _state('available')
        else:
            capabilities.requests = _state('unavailable', 'no_requests')
        if any(os.path.isfile(os.path.join(path, 'node_analysis.json')) for path in readable_dirs):
            capabilities.node_metrics = _state('available')
        else:
            capabilities.node_metrics = _state('unavailable', 'no_node_metrics')
        capabilities.html = _state('available')
    elif single is not None:
        capabilities.sweep = _unsupported('single_run')
        normalized = single.get('normalized')
        has_normalized = False
        if normalized == 'normalized':
            has_normalized = os.path.isdir(os.path.join(perf_dir, 'normalized'))
        if has_normalized:
            capabilities.overview = _state('available')
            capabilities.requests = _state('available')
            capabilities.html = _state('available')
        else:
            capabilities.overview = _state('unavailable', 'archive_unreadable')
            capabilities.requests = _state('unavailable', 'archive_unreadable')
            capabilities.html = _state('unavailable', 'archive_unreadable')
        if os.path.isfile(os.path.join(perf_dir, 'node_analysis.json')):
            capabilities.node_metrics = _state('available')
        else:
            capabilities.node_metrics = _state('unavailable', 'no_node_metrics')
    else:
        has_runs = _has_perf_run(perf_dir)
        if has_runs:
            capabilities.overview = _state('available')
            num_runs = sum(
                1
                for name in _child_dirs(perf_dir)
                if name.startswith(_PERF_RUN_PREFIXES) and os.path.isdir(os.path.join(perf_dir, name))
            )
            capabilities.sweep = _state('available') if num_runs > 1 else _unsupported('single_run')
            capabilities.requests = (
                _state('available') if _has_request_db(perf_dir) else _state('unavailable', 'no_requests')
            )
        elif active:
            capabilities.overview = _state('pending', 'task_not_finished')
            capabilities.requests = _state('pending', 'task_not_finished')
        if os.path.isfile(os.path.join(perf_dir, 'perf_report.html')):
            capabilities.html = _state('available')
        elif active:
            capabilities.html = _state('pending', 'task_not_finished')

    result_availability = capabilities.overview
    if result_availability.availability == 'unavailable' and status == 'error':
        result_availability = ResultAvailability(availability='unavailable', reason='execution_failed')
    return _as_result_availability(result_availability, active), capabilities


def _files(path: str) -> List[str]:
    try:
        with os.scandir(path) as entries:
            return [entry.name for entry in entries if entry.is_file(follow_symlinks=False)]
    except OSError:
        return []


def _as_result_availability(state: AvailabilityState, active: bool) -> ResultAvailability:
    if active and state.availability == 'unavailable':
        return ResultAvailability(availability='pending', reason='task_not_finished')
    return ResultAvailability(availability=state.availability, reason=state.reason)


def build_config_snapshot(record: Dict[str, Any]) -> Tuple[Optional[Any], str]:
    """Return a sanitized, display-ready configuration snapshot.

    Returns:
        ``(snapshot, completeness)`` where completeness is ``full`` when the
        persisted plan/manifest was read, ``summary_only`` when only the manifest
        summary exists, and ``unavailable`` when nothing was persisted.
    """
    task_id = record['task_id']
    kind = record.get('kind') or 'eval'
    config = record.get('config') if isinstance(record.get('config'), dict) else {}
    model = str(record.get('model') or '')
    alias = record.get('alias')
    endpoint = record.get('endpoint')

    if kind == 'eval':
        if not config:
            snapshot = EvalConfigSnapshot(engine=record.get('engine'), model=model, alias=alias, endpoint=endpoint)
            return snapshot, 'summary_only'
        return EvalConfigSnapshot(
            engine=record.get('engine'),
            model=model,
            alias=alias,
            endpoint=endpoint,
            datasets=config.get('datasets') or [],
            limit=config.get('limit'),
            eval_batch_size=config.get('eval_batch_size'),
            repeats=config.get('repeats'),
            temperature=config.get('temperature'),
            top_p=config.get('top_p'),
            top_k=config.get('top_k'),
            max_tokens=config.get('max_tokens'),
        ), 'full'

    plan_snapshot = _plan_config_snapshot(task_id, record)
    if plan_snapshot is not None:
        return plan_snapshot, 'full'

    plan = _experiment_plan(task_id)
    if plan is not None:
        plan_config = plan.get('configuration') if isinstance(plan.get('configuration'), dict) else {}
        cache = plan_config.get('cache') if isinstance(plan_config.get('cache'), dict) else {}
        load = plan_config.get('load') if isinstance(plan_config.get('load'), dict) else {}
        subruns = plan.get('subruns') if isinstance(plan.get('subruns'), list) else []
        load_points = load.get('points') if isinstance(load.get('points'), list) else []
        load_modes = []
        for point in load_points:
            if not isinstance(point, dict):
                continue
            mode = 'open_loop' if point.get('rate') is not None else 'closed_loop'
            if mode not in load_modes:
                load_modes.append(mode)
        node_ids = []
        telemetry = plan_config.get('telemetry') if isinstance(plan_config.get('telemetry'), dict) else {}
        for target in telemetry.get('targets') or []:
            if isinstance(target, dict) and isinstance(target.get('node_id'), str):
                node_ids.append(target['node_id'])
        snapshot = CacheExperimentConfigSnapshot(
            engine=record.get('engine'),
            model=model,
            alias=alias,
            endpoint=endpoint,
            tokenizer_id=plan.get('tokenizer_id') or plan_config.get('tokenizer_id'),
            template_id=plan.get('template_id') or plan_config.get('template_id'),
            stream=plan_config.get('stream'),
            deployment_mode=plan_config.get('deployment_mode'),
            plan_hash=plan.get('plan_hash'),
            configuration_hash=plan.get('configuration_hash'),
            target_ratios=[float(value) for value in cache.get('target_ratios') or [] if type(value) in (int, float)],
            load_modes=load_modes,
            repetitions=load.get('repetitions'),
            total_subruns=len(subruns) or None,
            node_ids=node_ids,
        )
        return snapshot, 'full'

    single = _single_run_manifest(task_id)
    if single is not None:
        snapshot = AiPerfSingleConfigSnapshot(
            model=str(single.get('model') or model),
            alias=single.get('alias') or alias,
            endpoint=single.get('endpoint') or endpoint,
            api_type=str(single.get('api_type') or 'openai'),
            load_mode=single.get('load_mode'),
            concurrency=single.get('concurrency'),
            rate=single.get('rate'),
            planned_requests=single.get('planned_requests'),
            output_token_limit=single.get('output_token_limit'),
            input_sha256=single.get('input_sha256'),
        )
        return snapshot, 'full'

    if not config:
        return None, 'unavailable'
    return NativePerfConfigSnapshot(
        test_type=record.get('test_type') or 'unknown',
        model=model,
        alias=alias,
        endpoint=endpoint,
        api=config.get('api'),
        dataset=config.get('dataset'),
        parallel=config.get('parallel'),
        number=config.get('number'),
        rate=config.get('rate'),
        max_tokens=config.get('max_tokens'),
        min_tokens=config.get('min_tokens'),
        max_prompt_length=config.get('max_prompt_length'),
        min_prompt_length=config.get('min_prompt_length'),
    ), 'full'


def _plan_config_snapshot(task_id: str, record: Dict[str, Any]) -> Optional[PlanConfigSnapshot]:
    """Build the plan configuration view from its own archive.

    Requested parameters come from ``compiled.json`` and effective identity from
    ``effective.json``; neither is merged into the other, and an unresolved
    tokenizer stays ``None`` instead of being guessed from the path.
    """
    location = _plan_location(task_id)
    if location is None:
        return None
    perf_root, directory, _ = location
    from evalscope.perf.plans.provenance import read_compiled_plan, read_effective_identity

    try:
        compiled = read_compiled_plan(perf_root)
    except ValueError:
        return None
    effective = read_effective_identity(perf_root)
    document = compiled.normalized
    manifest = _plan_manifest(task_id) or {}
    warmup = document.warmup
    return PlanConfigSnapshot(
        model=document.connection.model,
        alias=document.name or record.get('alias'),
        endpoint=sanitize_endpoint(document.connection.url),
        name=document.name,
        intent=document.intent,
        workload_semantics=compiled.workload_semantics,
        plan_hash=manifest.get('plan_hash') or compiled.plan_hash,
        normalized_hash=manifest.get('normalized_hash') or compiled.normalized_hash,
        source_hash=compiled.source_hash,
        semantic_fingerprint=manifest.get('semantic_fingerprint') or compiled.semantic_fingerprint,
        compiler_version=compiled.compiler_version,
        contract_version=compiled.contract_version,
        tokenizer_id=effective.tokenizer.content_hash if effective else None,
        template_id=effective.tokenizer.template_hash if effective else None,
        aiperf_version=effective.aiperf_version if effective else None,
        deployment_mode=document.deployment.mode,
        declared_max_model_len=document.deployment.declared_max_model_len,
        deployment_evidence=document.deployment.evidence,
        deployment_measured=(effective.deployment_evidence == 'measured') if effective else None,
        input_lengths=[int(run.input_tokens) for run in compiled.runs if run.input_tokens is not None][:64],
        target_ratios=sorted({float(run.target_ratio) for run in compiled.runs if run.target_ratio is not None}),
        concurrencies=sorted({run.concurrency for run in compiled.runs}),
        repetitions=document.load.repetitions,
        request_count=document.load.request_count,
        output_token_limit=document.load.output_token_limit,
        warmup_enabled=warmup.enabled,
        warmup_input_tokens=warmup.input_tokens if warmup.enabled else None,
        warmup_output_token_limit=warmup.output_token_limit if warmup.enabled else None,
        primer_timeout_seconds=document.priming.timeout_seconds if document.priming.policy != 'none' else None,
        total_runs=compiled.run_count,
    )


def _report_links(record: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Cheap, always-safe report entry points (no archive parse)."""
    from urllib.parse import quote, urlencode

    from .utils import log

    task_id = record['task_id']
    kind = record.get('kind') or 'eval'
    status = record.get('status') or 'unknown'
    if status in _ACTIVE_STATUSES:
        return None, None
    root_query = urlencode({'root_path': log.OUTPUT_DIR})
    task_query = urlencode({'task_id': task_id})
    if kind == 'eval':
        return f'/reports?{root_query}&search={quote(task_id, safe="")}', (
            f'/api/v1/eval/report?{task_query}' if record.get('has_reports_dir') else None
        )
    if _plan_location(task_id) is not None:
        return f'/perf-plan/{quote(task_id, safe="")}?{root_query}', (
            f'/api/v1/perf/plans/{quote(task_id, safe="")}/report'
        )
    plan = _experiment_plan(task_id)
    if plan is not None:
        return f'/perf-experiment/{quote(task_id, safe="")}', (
            f'/api/v1/perf/experiments/{quote(task_id, safe="")}/report'
        )
    if not record.get('has_perf_dir'):
        return None, None
    perf_dir = task_file_path(task_id, 'perf')
    report_url = None
    if _single_run_manifest(task_id) is not None or _has_perf_run(perf_dir):
        params = urlencode({'root_path': log.OUTPUT_DIR, 'path': f'{task_id}/perf'})
        report_url = f'/perf-report?{params}'
    html_url = None
    if _single_run_manifest(task_id) is not None:
        html_url = f'/api/v1/perf/history/report?{urlencode({"root_path": log.OUTPUT_DIR, "path": f"{task_id}/perf"})}'
    elif os.path.isfile(os.path.join(perf_dir, 'perf_report.html')):
        html_url = f'/api/v1/perf/report?{task_query}'
    return report_url, html_url


def build_summary_item(record: Dict[str, Any]) -> TaskSummaryItem:
    """Map one merged index/queue record onto the list contract."""
    parsed, basis = task_history.effective_time(record)
    availability, capabilities = detect_capabilities(record)
    report_url, html_url = _report_links(record)
    status = str(record.get('status') or 'unknown')
    if status not in task_history.RAW_STATUSES:
        status = 'unknown'
    kind = record.get('kind') if record.get('kind') in task_history.KINDS else 'eval'
    engine = record.get('engine') if record.get('engine') in task_history.ENGINES else None
    quality = record.get('quality') if record.get('quality') in task_history.QUALITY_LEVELS else None
    test_type = record.get('test_type')
    if test_type not in ('single', 'sweep', 'cache_experiment', 'unknown'):
        test_type = 'unknown' if kind == 'perf' else None
    return TaskSummaryItem(
        task_id=record['task_id'],
        kind=kind,
        engine=engine,
        test_type=test_type,
        model=str(record.get('model') or ''),
        alias=record.get('alias'),
        endpoint=record.get('endpoint'),
        status=status,
        status_group=task_history.status_group(status),
        percent=task_history.clamp_percent(record.get('percent')),
        phase=record.get('phase'),
        current_subrun=record.get('current_subrun'),
        completed_subruns=record.get('completed_subruns'),
        total_subruns=record.get('total_subruns'),
        quality=quality,
        queue_position=record.get('queue_position') if type(record.get('queue_position')) is int else None,
        started_at=record.get('started_at'),
        enqueued_at=record.get('enqueued_at'),
        finished_at=record.get('finished_at'),
        observed_at=datetime.now().astimezone().isoformat(),
        effective_time=parsed.isoformat() if parsed is not None else None,
        time_basis=basis,
        result_availability=availability,
        capabilities=capabilities,
        report_url=report_url,
        html_report_url=html_url,
        error=task_history.sanitize_error(record.get('error')),
    )


def build_detail(record: Dict[str, Any]) -> TaskDetailResponse:
    """Detail projection: the list item plus its sanitized configuration snapshot."""
    item = build_summary_item(record)
    snapshot, completeness = build_config_snapshot(record)
    try:
        logs_available = os.path.isfile(task_file_path(record['task_id'], 'perf/benchmark.log')) or os.path.isdir(
            task_file_path(record['task_id'], 'logs')
        )
    except ValueError:
        logs_available = False
    return TaskDetailResponse(
        **item.model_dump(mode='json'),
        config_snapshot=snapshot,
        config_completeness=completeness,
        logs_available=logs_available,
    )
