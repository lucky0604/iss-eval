"""Read-only projections of AIPerf experiment artifacts."""

import hashlib
import json
import math
import os
import re
from typing import Any, Dict, Tuple

from evalscope.perf.experiments.planner import CacheExperimentPlan, CacheSubrunPlan
from evalscope.perf.integrations.aiperf.metrics import NodeMetricsAnalysis
from evalscope.perf.results.aiperf_import import load_aiperf_run, observed_cache_usage
from evalscope.service.api_models import CacheExperimentResponse, CacheRepetitionsResponse, CacheScenarioResponse
from evalscope.service.utils import active_task_ids, task_file_path, validate_task_id

_SUBRUN = re.compile(r'^r\d{3,}-l\d{3,}-n\d{3,}$')


def _read(task_id: str, subpath: str) -> Dict[str, Any]:
    from evalscope.service.utils import log

    path = task_file_path(task_id, subpath)
    perf_dir = task_file_path(task_id, 'perf')
    expected = os.path.join(os.path.realpath(os.path.join(log.OUTPUT_DIR, task_id)), 'perf')
    if perf_dir != expected or not path.startswith(perf_dir + os.sep):
        raise ValueError('Experiment document escapes the perf directory')
    if os.path.getsize(path) > 16 * 1024 * 1024:
        raise ValueError('Experiment document is too large')
    with open(path, encoding='utf-8') as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError('Invalid experiment document')
    return data


def load_experiment(task_id: str) -> CacheExperimentResponse:
    """Project an experiment plan and its authoritative persisted status."""
    validate_task_id(task_id)
    raw_plan = _read(task_id, 'perf/experiment_plan.json')
    plan = CacheExperimentPlan.model_validate(raw_plan)
    if plan.configuration is not None:
        identity = plan.configuration.model_dump(mode='json')
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if identity['task_id'] != task_id or digest != plan.configuration_hash:
            raise ValueError('Experiment configuration does not match its identity')
    state = _read(task_id, 'perf/experiment.json')
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
    if state.get('status') == 'running' and task_id not in active_task_ids():
        state['status'] = 'unknown'
    return CacheExperimentResponse(task_id=task_id, plan=plan, state=state)


def load_repetition_page(
    task_id: str,
    reference_id: str,
    metric_key: str,
    offset: int,
    limit: int,
) -> CacheRepetitionsResponse:
    """Page all repetitions with the reference's fixed experiment dimensions."""
    if metric_key not in ('time_to_first_token', 'request_throughput') or offset < 0 or not 1 <= limit <= 100:
        raise ValueError('Invalid repetition page')
    experiment = load_experiment(task_id)
    reference = next((row for row in experiment.plan.subruns if row.subrun_id == reference_id), None)
    if reference is None:
        raise ValueError('Unknown reference subrun')

    def dimensions(row: CacheSubrunPlan) -> tuple:
        return (
            row.target_ratio,
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
            normalized = _scenario_normalized_path(task_id, row)
            document = _read(task_id, f'perf/runs/{row.subrun_id}/scenario.json')
            if document.get('target_ratio') != row.target_ratio or document.get('normalized') != 'normalized':
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


def scenario_path(task_id: str, subrun_id: str) -> Tuple[CacheSubrunPlan, str]:
    """Resolve a subrun only after checking membership in its saved plan."""
    if not _SUBRUN.fullmatch(subrun_id):
        raise ValueError('Invalid subrun ID')
    experiment = load_experiment(task_id)
    item = next((row for row in experiment.plan.subruns if row.subrun_id == subrun_id), None)
    if item is None:
        raise ValueError('Unknown subrun ID')
    return item, _scenario_normalized_path(task_id, item)


def _scenario_normalized_path(task_id: str, item: CacheSubrunPlan) -> str:
    subrun_id = item.subrun_id
    if not _SUBRUN.fullmatch(subrun_id):
        raise ValueError('Invalid subrun ID')
    runs = task_file_path(task_id, 'perf/runs')
    if runs != os.path.join(task_file_path(task_id, 'perf'), 'runs'):
        raise ValueError('Scenario runs directory cannot be redirected')
    run = task_file_path(task_id, f'perf/runs/{subrun_id}')
    normalized = task_file_path(task_id, f'perf/runs/{subrun_id}/normalized')
    if run != os.path.join(runs, subrun_id) or not normalized.startswith(run + os.sep):
        raise ValueError('Scenario path escapes its planned run')
    return normalized


def load_scenario(task_id: str, subrun_id: str) -> CacheScenarioResponse:
    """Project generated reuse separately from server-observed cache usage."""
    item, normalized = scenario_path(task_id, subrun_id)
    return _load_scenario(task_id, item, normalized)


def _load_planned_scenario(task_id: str, item: CacheSubrunPlan) -> CacheScenarioResponse:
    """Load an already validated plan item supplied by the server's report iterator."""
    return _load_scenario(task_id, item, _scenario_normalized_path(task_id, item))


def _load_scenario(task_id: str, item: CacheSubrunPlan, normalized: str) -> CacheScenarioResponse:
    subrun_id = item.subrun_id
    scenario_file = task_file_path(task_id, f'perf/runs/{subrun_id}/scenario.json')
    if not scenario_file.startswith(os.path.dirname(normalized) + os.sep):
        raise ValueError('Scenario document escapes its planned run')
    state = _read(task_id, f'perf/runs/{subrun_id}/scenario.json')
    if state.get('target_ratio') != item.target_ratio:
        raise ValueError('Scenario target does not match plan')
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
        analysis_path = task_file_path(task_id, f'perf/runs/{subrun_id}/node_analysis.json')
        if not analysis_path.startswith(run_dir + os.sep):
            raise ValueError('Node analysis escapes its planned run')
        analysis = NodeMetricsAnalysis.model_validate(_read(task_id, f'perf/runs/{subrun_id}/node_analysis.json'))
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
        generated_ratio=ratio,
        generated_count=count,
        input_tokens_per_request=token_count,
        request_hashes_sha256=fingerprint,
        cache=cache,
        result=result,
        node_analysis=analysis,
        quality_reasons=reasons,
    )
