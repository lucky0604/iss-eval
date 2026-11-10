"""Read-only projections of AIPerf experiment artifacts."""

import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import datetime
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from evalscope.perf.executors.arguments import cache_identity_payload
from evalscope.perf.experiments.planner import CacheExperimentPlan, CacheSubrunPlan
from evalscope.perf.integrations.aiperf.metrics import NodeMetricsAnalysis
from evalscope.perf.results.aiperf_import import load_aiperf_run, observed_cache_usage
from evalscope.service.api_models import (
    CacheExperimentResponse,
    CacheExperimentSummaryResponse,
    CacheRepetitionsResponse,
    CacheScenarioResponse,
    ExperimentCoverage,
    ExperimentGroup,
)
from evalscope.service.utils import active_task_ids, validate_task_id

_SUBRUN = re.compile(r'^(i\d{3,}-)?r\d{3,}-l\d{3,}-n\d{3,}$')

# One summary build reads at most this many persisted scenarios. A larger plan
# still reports its full planned/status counts and says the request totals are
# partial instead of pretending the read scope was global.
_SUMMARY_READ_BUDGET = 500
_SUMMARY_CACHE_LIMIT = 8
_SUMMARY_CACHE: Dict[Tuple[str, str], Tuple[str, CacheExperimentSummaryResponse]] = {}
_SUMMARY_LOCK = Lock()


def _root_dir(root: Optional[str]) -> str:
    """Resolve the explicit outputs root, defaulting to the service root."""
    from evalscope.service.utils import log

    return os.path.realpath(root or log.OUTPUT_DIR)


def _is_service_root(root: Optional[str]) -> bool:
    from evalscope.service.utils import log

    return _root_dir(root) == os.path.realpath(log.OUTPUT_DIR)


def _read(root: Optional[str], task_id: str, subpath: str) -> Dict[str, Any]:
    validate_task_id(task_id)
    root_real = _root_dir(root)
    task_dir = os.path.join(root_real, task_id)
    path = os.path.join(task_dir, *subpath.split('/'))
    task_real = os.path.realpath(task_dir)
    if task_real != task_dir or not task_real.startswith(root_real + os.sep):
        raise ValueError('Experiment task escapes its outputs root')
    if os.path.realpath(path) != path or not path.startswith(task_real + os.sep):
        raise ValueError('Experiment document escapes the perf directory')
    if os.path.getsize(path) > 16 * 1024 * 1024:
        raise ValueError('Experiment document is too large')
    with open(path, encoding='utf-8') as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError('Invalid experiment document')
    return data


def load_experiment(task_id: str, root: Optional[str] = None) -> CacheExperimentResponse:
    """Project an experiment plan and its authoritative persisted status.

    ``root`` selects the outputs directory explicitly; an archive copied from
    another root is read in place and never resolved against the service root.
    """
    validate_task_id(task_id)
    raw_plan = _read(root, task_id, 'perf/experiment_plan.json')
    plan = CacheExperimentPlan.model_validate(raw_plan)
    if plan.configuration is not None:
        identity = plan.configuration.model_dump(mode='json')
        identity['cache'] = cache_identity_payload(plan.configuration.cache)
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if identity['task_id'] != task_id or digest != plan.configuration_hash:
            raise ValueError('Experiment configuration does not match its identity')
    state = _read(root, task_id, 'perf/experiment.json')
    rows = state.get('subruns')
    if (
        state.get('plan_hash') != plan.plan_hash
        or not isinstance(rows, list)
        or len(rows) != len(plan.subruns)
        or state.get('total_subruns') != len(rows)
    ):
        raise ValueError('Experiment plan and state do not match')
    identifiers = set()
    for index, (item, row) in enumerate(zip(plan.subruns, rows)):
        if not isinstance(row, dict) or row.get('subrun_id') != item.subrun_id or not _SUBRUN.fullmatch(item.subrun_id):
            raise ValueError('Experiment subrun identity mismatch')
        if item.subrun_id in identifiers or item.order != index:
            raise ValueError('Experiment subrun order or identity is invalid')
        identifiers.add(item.subrun_id)
        expected = f'runs/{item.subrun_id}/scenario.json'
        if row.get('scenario') is not None and row['scenario'] != expected:
            raise ValueError('Invalid scenario reference')
    payload = {'config_hash': raw_plan.get('configuration_hash'), 'subruns': raw_plan.get('subruns')}
    actual_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if actual_hash != plan.plan_hash:
        raise ValueError('Experiment plan contents do not match its identity')
    # Only the service root can compare against the live queue: an external
    # archive that reuses a task id must not be presented as the service task.
    if state.get('status') == 'running' and _is_service_root(root) and task_id not in active_task_ids():
        state['status'] = 'unknown'
    return CacheExperimentResponse(task_id=task_id, plan=plan, state=state)


def _file_digest(path: str) -> str:
    try:
        with open(path, 'rb') as file:
            return hashlib.sha256(file.read()).hexdigest()[:16]
    except OSError:
        return '-'


def _stat_stamp(path: str) -> str:
    try:
        stat = os.stat(path)
    except OSError:
        return '-'
    return f'{stat.st_mtime_ns}:{stat.st_size}'


def _summary_stamp(task_id: str, root: str, experiment: CacheExperimentResponse) -> str:
    """Build a content-derived version: plan, state and each persisted scenario.

    Directory names and task IDs alone never decide freshness, so a rewritten
    plan or an appended subrun state invalidates the cached summary.
    """
    task_dir = os.path.join(root, task_id)
    parts = [
        experiment.plan.plan_hash,
        _file_digest(os.path.join(task_dir, 'perf', 'experiment.json')),
        _file_digest(os.path.join(task_dir, 'perf', 'experiment_plan.json')),
    ]
    for row, state in zip(experiment.plan.subruns, experiment.state.subruns):
        if state.scenario is None:
            continue
        try:
            normalized = _scenario_normalized_path(root, task_id, row)
            scenario_file = os.path.join(os.path.dirname(normalized), 'scenario.json')
        except ValueError:
            scenario_file = ''
        parts.append(f'{row.subrun_id}={_stat_stamp(scenario_file)}')
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()


def _group_key(item: CacheSubrunPlan) -> str:
    """Identity of one matrix cell; repetitions stay members of the same cell."""
    return json.dumps(
        [
            item.load_mode,
            item.load_point.concurrency,
            item.load_point.rate,
            item.target_ratio,
            item.input_tokens,
            item.output_token_limit,
        ],
        separators=(',', ':'),
    )


def _build_summary(
    task_id: str, root: str, stamp: str, experiment: CacheExperimentResponse
) -> CacheExperimentSummaryResponse:
    counts_by_status: Dict[str, int] = {}
    counts_by_quality: Dict[str, int] = {}
    groups: Dict[str, ExperimentGroup] = {}
    completed = readable = unreadable = missing = skipped = observed = successful = 0
    reads = 0
    for item, state in zip(experiment.plan.subruns, experiment.state.subruns):
        counts_by_status[state.status] = counts_by_status.get(state.status, 0) + 1
        if state.quality is not None:
            counts_by_quality[state.quality] = counts_by_quality.get(state.quality, 0) + 1
        if state.status == 'completed':
            completed += 1
        elif state.status == 'not_started':
            missing += 1
        group = groups.setdefault(
            _group_key(item),
            ExperimentGroup(
                group_key=_group_key(item),
                load_mode=item.load_mode,
                concurrency=item.load_point.concurrency,
                rate=item.load_point.rate,
                target_ratio=item.target_ratio,
                input_tokens=item.input_tokens,
                output_token_limit=item.output_token_limit,
                total_subruns=0,
                readable_subruns=0,
                status_counts={},
                planned_requests=0,
                observed_requests=0,
                successful_requests=0,
            ),
        )
        group.total_subruns += 1
        group.planned_requests += item.request_count
        group.status_counts[state.status] = group.status_counts.get(state.status, 0) + 1
        if state.scenario is None:
            if state.status == 'completed':
                # A completed subrun that never published a scenario reference
                # is a defect, unlike a subrun that was never attempted.
                unreadable += 1
            continue
        if reads >= _SUMMARY_READ_BUDGET:
            skipped += 1
            continue
        reads += 1
        try:
            scenario = _load_scenario(root, task_id, item, _scenario_normalized_path(root, task_id, item))
        except (OSError, ValueError, sqlite3.Error):
            unreadable += 1
            continue
        if scenario.result is None:
            # Started but not yet published: not a bad file, and not a zero.
            continue
        readable += 1
        group.readable_subruns += 1
        observed += scenario.result.profiling_requests
        successful += scenario.result.profiling_successes
        group.observed_requests += scenario.result.profiling_requests
        group.successful_requests += scenario.result.profiling_successes
    reasons = []
    if unreadable:
        reasons.append('unreadable_subruns')
    if skipped:
        reasons.append('read_budget_exceeded')
    return CacheExperimentSummaryResponse(
        task_id=task_id,
        plan_hash=experiment.plan.plan_hash,
        configuration_hash=experiment.plan.configuration_hash,
        observed_at=datetime.now().astimezone().isoformat(),
        revision=int(hashlib.sha256(stamp.encode()).hexdigest()[:12], 16),
        execution_status=experiment.state.status,
        quality=experiment.state.quality,
        total_planned_subruns=len(experiment.plan.subruns),
        counts_by_status=counts_by_status,
        counts_by_quality=counts_by_quality,
        readable_subruns=readable,
        missing_subruns=missing,
        unreadable_subruns=unreadable,
        coverage=ExperimentCoverage(
            planned=len(experiment.plan.subruns), completed=completed, readable=readable, included=readable
        ),
        groups=[groups[key] for key in sorted(groups)],
        completeness='partial' if reasons else 'complete',
        completeness_reason=';'.join(reasons) or None,
        availability='available',
    )


def load_summary(task_id: str, root: Optional[str] = None) -> CacheExperimentSummaryResponse:
    """Plan-wide counts that never shrink to the currently paged matrix window.

    ``missing_subruns`` counts planned subruns that were never started;
    terminal failures stay visible in ``counts_by_status`` instead of being
    merged into missing. Request totals are only summed from readable run
    results, so a partial summary never reports an unfinished subrun as zero
    or as success. Group means and percentiles stay in their group records.
    """
    experiment = load_experiment(task_id, root)
    root_real = _root_dir(root)
    stamp = _summary_stamp(task_id, root_real, experiment)
    key = (root_real, task_id)
    with _SUMMARY_LOCK:
        cached = _SUMMARY_CACHE.get(key)
        if cached is not None and cached[0] == stamp:
            return cached[1]
    summary = _build_summary(task_id, root_real, stamp, experiment)
    with _SUMMARY_LOCK:
        _SUMMARY_CACHE[key] = (stamp, summary)
        while len(_SUMMARY_CACHE) > _SUMMARY_CACHE_LIMIT:
            oldest = next(iter(_SUMMARY_CACHE))
            if oldest == key:
                break
            del _SUMMARY_CACHE[oldest]
    return summary


def load_repetition_page(
    task_id: str,
    reference_id: str,
    metric_key: str,
    offset: int,
    limit: int,
    root: Optional[str] = None,
) -> CacheRepetitionsResponse:
    """Page all repetitions with the reference's fixed experiment dimensions."""
    if metric_key not in ('time_to_first_token', 'request_throughput') or offset < 0 or not 1 <= limit <= 100:
        raise ValueError('Invalid repetition page')
    experiment = load_experiment(task_id, root)
    reference = next((row for row in experiment.plan.subruns if row.subrun_id == reference_id), None)
    if reference is None:
        raise ValueError('Unknown reference subrun')

    def dimensions(row: CacheSubrunPlan) -> tuple:
        return (
            row.target_ratio,
            row.input_tokens,
            row.load_mode,
            row.load_point.concurrency,
            row.load_point.rate,
            row.seed,
            row.request_count,
            row.output_token_limit,
        )

    pairs = sorted(
        (
            (row, state)
            for row, state in zip(experiment.plan.subruns, experiment.state.subruns)
            if dimensions(row) == dimensions(reference)
        ),
        key=lambda pair: (pair[0].repetition, pair[0].subrun_id),
    )
    repetitions = []
    for row, state in pairs[offset : offset + limit]:
        item = {'subrun_id': row.subrun_id, 'repetition': row.repetition, 'status': state.status}
        if state.scenario is None:
            item['unavailable_reason'] = 'scenario_unavailable'
            repetitions.append(item)
            continue
        try:
            normalized = _scenario_normalized_path(root, task_id, row)
            document = _read(root, task_id, f'perf/runs/{row.subrun_id}/scenario.json')
            if (
                document.get('target_ratio') != row.target_ratio
                or (
                    row.input_tokens is not None
                    and document.get('input_tokens') is not None
                    and document.get('input_tokens') != row.input_tokens
                )
                or document.get('normalized') != 'normalized'
            ):
                raise ValueError('Invalid normalized scenario')
            result = load_aiperf_run(normalized)
            item.update(quality=result.quality, engine=result.engine, executor_version=result.executor_version)
            metrics = [metric for metric in result.metrics if metric.key == metric_key]
            if len(metrics) != 1 or metrics[0].value is None or not math.isfinite(metrics[0].value):
                item['unavailable_reason'] = 'metric_unavailable'
            else:
                item['metric'] = metrics[0]
        except (OSError, ValueError):
            item['unavailable_reason'] = 'result_unavailable'
        repetitions.append(item)
    return CacheRepetitionsResponse(
        plan_hash=experiment.plan.plan_hash, total=len(pairs), offset=offset, limit=limit, repetitions=repetitions
    )


def scenario_path(task_id: str, subrun_id: str, root: Optional[str] = None) -> Tuple[CacheSubrunPlan, str]:
    """Resolve a subrun only after checking membership in its saved plan."""
    if not _SUBRUN.fullmatch(subrun_id):
        raise ValueError('Invalid subrun ID')
    experiment = load_experiment(task_id, root)
    item = next((row for row in experiment.plan.subruns if row.subrun_id == subrun_id), None)
    if item is None:
        raise ValueError('Unknown subrun ID')
    return item, _scenario_normalized_path(root, task_id, item)


def _scenario_normalized_path(root: Optional[str], task_id: str, item: CacheSubrunPlan) -> str:
    subrun_id = item.subrun_id
    if not _SUBRUN.fullmatch(subrun_id):
        raise ValueError('Invalid subrun ID')
    validate_task_id(task_id)
    task_dir = os.path.join(_root_dir(root), task_id)
    runs = os.path.join(task_dir, 'perf', 'runs')
    run = os.path.join(runs, subrun_id)
    normalized = os.path.join(run, 'normalized')
    if not normalized.startswith(run + os.sep) or not run.startswith(runs + os.sep):
        raise ValueError('Scenario path escapes its planned run')
    # Reject symlinked task/runs/subrun components: their real location must stay
    # inside the selected outputs root.
    for path in (task_dir, runs, run, normalized):
        if os.path.realpath(path) != path:
            raise ValueError('Scenario path escapes its planned run')
    return normalized


def load_scenario(task_id: str, subrun_id: str, root: Optional[str] = None) -> CacheScenarioResponse:
    """Project generated reuse separately from server-observed cache usage."""
    item, normalized = scenario_path(task_id, subrun_id, root)
    return _load_scenario(root, task_id, item, normalized)


def _load_planned_scenario(root: Optional[str], task_id: str, item: CacheSubrunPlan) -> CacheScenarioResponse:
    """Load an already validated plan item supplied by the server's report iterator."""
    return _load_scenario(root, task_id, item, _scenario_normalized_path(root, task_id, item))


def _load_scenario(root: Optional[str], task_id: str, item: CacheSubrunPlan, normalized: str) -> CacheScenarioResponse:
    subrun_id = item.subrun_id
    run_dir = os.path.dirname(normalized)
    scenario_file = os.path.join(run_dir, 'scenario.json')
    if not scenario_file.startswith(run_dir + os.sep):
        raise ValueError('Scenario document escapes its planned run')
    state = _read(root, task_id, f'perf/runs/{subrun_id}/scenario.json')
    if state.get('target_ratio') != item.target_ratio:
        raise ValueError('Scenario target does not match plan')
    if (
        item.input_tokens is not None
        and state.get('input_tokens') is not None
        and state.get('input_tokens') != item.input_tokens
    ):
        raise ValueError('Scenario input length does not match plan')
    workload = state.get('workload')
    requests = workload.get('requests', []) if isinstance(workload, dict) else []
    if not isinstance(requests, list) or len(requests) > item.request_count:
        raise ValueError('Invalid generated workload')
    numerator = denominator = count = 0
    for row in requests:
        if not isinstance(row, dict):
            raise ValueError('Invalid generated request')
        shared, total = row.get('shared_tokens'), row.get('input_tokens')
        if shared is None:
            continue
        if type(shared) is not int or type(total) is not int or total <= 0 or not 0 <= shared <= total:
            raise ValueError('Invalid generated reuse value')
        numerator += shared
        denominator += total
        count += 1
    reasons = state.get('quality_reasons', [])
    if (
        not isinstance(reasons, list)
        or len(reasons) > 100
        or any(not isinstance(v, str) or len(v) > 200 for v in reasons)
    ):
        raise ValueError('Invalid quality reasons')
    result = (
        load_aiperf_run(normalized) if state.get('normalized') == 'normalized' and os.path.isdir(normalized) else None
    )
    cache = observed_cache_usage(normalized) if result is not None else None
    analysis = None
    if state.get('node_analysis') is not None:
        if state['node_analysis'] != 'node_analysis.json':
            raise ValueError('Invalid node analysis reference')
        run_dir = os.path.dirname(normalized)
        analysis_path = os.path.join(run_dir, 'node_analysis.json')
        if not analysis_path.startswith(run_dir + os.sep):
            raise ValueError('Node analysis escapes its planned run')
        analysis = NodeMetricsAnalysis.model_validate(_read(root, task_id, f'perf/runs/{subrun_id}/node_analysis.json'))
    ratio = numerator / denominator if denominator else None
    if ratio is not None and not math.isfinite(ratio):
        raise ValueError('Invalid generated reuse ratio')
    token_count = workload.get('total_input_tokens') if isinstance(workload, dict) else None
    if type(token_count) is not int or token_count <= 0:
        token_count = None
    hashes = [row.get('sha256') for row in requests]
    fingerprint = None
    if hashes and all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) for value in hashes):
        fingerprint = hashlib.sha256('\n'.join(hashes).encode()).hexdigest()
    return CacheScenarioResponse(
        subrun_id=subrun_id,
        status=str(state.get('status', 'unknown')),
        target_ratio=item.target_ratio,
        workload_mode=state.get('workload_mode'),
        generated_ratio=ratio,
        generated_count=count,
        input_tokens_per_request=token_count,
        request_hashes_sha256=fingerprint,
        cache=cache,
        result=result,
        node_analysis=analysis,
        quality_reasons=reasons,
    )
