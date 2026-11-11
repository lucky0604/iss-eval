"""Unified performance-result entries over one explicit outputs root.

Every archived result — a native EvalScope single run/sweep, an AIPerf single
run, a prefix-cache experiment or a portable plan — is projected into the same
entry shape so
the Web UI can list and open them through one framework. The backend name is
secondary metadata; discovery and identity come from the archive layout.

The builder is strictly read-only and root-scoped: it never changes the global
``OUTPUT_DIR`` and never resolves a client-supplied task id against a different
root. A cache experiment or a portable plan appears once as a container entry;
its per-subrun archives never surface as separate top-level rows.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from evalscope.utils.logger import get_logger
from evalscope.utils.uri_utils import sanitize_endpoint

from . import perf_archive, task_history
from .api_models import AvailabilityState, PerfEntryListResponse, PerformanceResultEntry, ResultCapabilities
from .perf_archive import PerfArchiveError

logger = get_logger()

_MAX_SCAN_DEPTH = 3
_MAX_JSON_BYTES = 16 * 1024 * 1024
_TEST_TYPES = ('single', 'sweep', 'cache_experiment', 'unknown')
_ENGINES = ('evalscope', 'aiperf')
_SORTS = ('time', 'rps', 'latency')
_MAX_LIMIT = 100


def _service_root() -> str:
    from .utils import log

    return log.OUTPUT_DIR


def _resolve_root(root_path: Optional[str]) -> str:
    """Resolve the requested outputs root, rejecting a missing directory."""
    root = os.path.abspath(root_path or _service_root())
    if not os.path.isdir(root):
        raise PerfArchiveError('root_path must be an existing directory', 400)
    return root


def _state(availability: str, reason: Optional[str] = None) -> AvailabilityState:
    return AvailabilityState(availability=availability, reason=reason)


def _unsupported(reason: str = 'not_supported') -> AvailabilityState:
    return _state('unsupported', reason)


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
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


def _timestamp(value: Any) -> Tuple[Optional[str], str]:
    parsed, basis = task_history.parse_time(value)
    return (parsed.isoformat() if parsed is not None else None), basis


def _mtime_iso(path: str) -> Tuple[Optional[str], str]:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).astimezone().isoformat(), 'aware'
    except OSError:
        return None, 'unknown'


def _valid_task_id(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value or os.path.basename(value) != value or value in ('.', '..'):
        return None
    return value


def _detail_links(root: str, path: str, task_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return the compatible run-detail link and experiment-analysis link."""
    root_query = urlencode({'root_path': root, 'path': path})
    detail_path = f'/perf-report?{root_query}'
    experiment_path = None
    if task_id:
        experiment_path = f'/perf-experiment/{quote(task_id, safe="")}?{urlencode({"root_path": root})}'
    return detail_path, experiment_path


# ------------------------------------------------------------------
# Discovery
# ------------------------------------------------------------------


def _scan_containers(root: str) -> List[Tuple[str, str]]:
    """Find result containers under one root as ``(relative base, kind)``.

    A cache experiment is a directory whose ``perf/`` holds
    ``experiment_plan.json``; a portable plan is a directory whose
    ``perf/plan/`` holds ``compiled.json``. Both appear once as a container;
    their per-run archives never surface as separate top-level rows.
    """
    root_real = os.path.realpath(root)
    found: List[Tuple[str, str]] = []

    def walk(current: str, depth: int) -> None:
        if depth > _MAX_SCAN_DEPTH:
            return
        if os.path.basename(current) == 'perf':
            if os.path.isfile(os.path.join(current, 'experiment_plan.json')):
                found.append((os.path.relpath(os.path.dirname(current), root) or '.', 'experiment'))
                return
            if os.path.isfile(os.path.join(current, 'plan', 'compiled.json')):
                found.append((os.path.relpath(os.path.dirname(current), root) or '.', 'plan'))
                return
        try:
            entries = sorted(os.listdir(current))
        except OSError:
            return
        for name in entries:
            child = os.path.join(current, name)
            if not os.path.isdir(child):
                continue
            if not os.path.realpath(child).startswith(root_real + os.sep):
                continue
            walk(child, depth + 1)

    walk(root, 0)
    return found


def _inside_container(rel_path: str, containers: List[str]) -> bool:
    normalized = rel_path.replace('\\', '/').strip('/')
    for base in containers:
        base_path = base.replace('\\', '/').strip('/')
        if base_path in ('', '.'):
            continue
        if normalized == base_path or normalized.startswith(base_path + '/'):
            return True
    return False


# ------------------------------------------------------------------
# Capability detection (root-scoped, archive markers only)
# ------------------------------------------------------------------


def _has_request_rows(run_dir: str) -> bool:
    if os.path.isfile(os.path.join(run_dir, 'benchmark_data.db')):
        return True
    normalized = os.path.join(run_dir, 'normalized')
    generations = os.path.join(normalized, 'generations')
    if os.path.isfile(os.path.join(normalized, 'current.json')) and any(
        os.path.isfile(os.path.join(generations, name, 'requests.db')) for name in _child_dirs(generations)
    ):
        return True
    return any(
        os.path.isfile(os.path.join(run_dir, name, 'benchmark_data.db'))
        for name in _child_dirs(run_dir)
        if name.startswith(('parallel_', 'rate_'))
    )


def _normalized_has_requests(normalized: str) -> bool:
    generations = os.path.join(normalized, 'generations')
    return os.path.isfile(os.path.join(normalized, 'current.json')) and any(
        os.path.isfile(os.path.join(generations, name, 'requests.db')) for name in _child_dirs(generations)
    )


def _run_capabilities(run_dir: str, num_runs: int, has_html: bool) -> ResultCapabilities:
    has_requests = _has_request_rows(run_dir)
    has_nodes = os.path.isfile(os.path.join(run_dir, 'node_analysis.json'))
    return ResultCapabilities(
        overview=_state('available'),
        sweep=_state('available') if num_runs > 1 else _unsupported('single_run'),
        requests=_state('available') if has_requests else _state('unavailable', 'no_requests'),
        node_metrics=_state('available') if has_nodes else _state('unavailable', 'no_node_metrics'),
        cache_experiment=_unsupported('not_a_cache_experiment'),
        configuration=_state('available'),
        html=_state('available') if has_html else _state('unavailable', 'html_not_generated'),
    )


def _experiment_capabilities(perf_dir: str) -> ResultCapabilities:
    runs = [os.path.join(perf_dir, 'runs', name) for name in _child_dirs(os.path.join(perf_dir, 'runs'))]
    has_requests = any(_normalized_has_requests(os.path.join(path, 'normalized')) for path in runs)
    has_nodes = any(os.path.isfile(os.path.join(path, 'node_analysis.json')) for path in runs)
    return ResultCapabilities(
        overview=_state('available'),
        sweep=_state('available'),
        requests=_state('available') if has_requests else _state('unavailable', 'no_requests'),
        node_metrics=_state('available') if has_nodes else _state('unavailable', 'no_node_metrics'),
        cache_experiment=_state('available'),
        configuration=_state('available'),
        html=_state('available'),
    )


def _plan_capabilities(perf_dir: str) -> ResultCapabilities:
    """Capabilities of one plan container, derived from its own archive only."""
    runs = os.path.join(perf_dir, 'runs')
    run_dirs = [os.path.join(runs, name) for name in _child_dirs(runs)]
    has_requests = any(_normalized_has_requests(os.path.join(path, 'normalized')) for path in run_dirs)
    has_manifest = os.path.isfile(os.path.join(perf_dir, 'plan', 'manifest.json'))
    plan = _read_json(os.path.join(perf_dir, 'plan', 'compiled.json')) or {}
    runs_planned = plan.get('runs') if isinstance(plan.get('runs'), list) else []
    return ResultCapabilities(
        overview=_state('available') if has_manifest else _state('pending', 'task_not_finished'),
        sweep=_state('available') if len(runs_planned) > 1 else _unsupported('single_run'),
        requests=_state('available') if has_requests else _state('unavailable', 'no_requests'),
        node_metrics=_unsupported('plan_telemetry_unsupported'),
        cache_experiment=_state('available'),
        configuration=_state('available'),
        html=_state('available') if has_manifest else _state('pending', 'task_not_finished'),
    )


def _unreadable_capabilities() -> ResultCapabilities:
    return ResultCapabilities(
        overview=_state('unavailable', 'archive_unreadable'),
        sweep=_state('unknown'),
        requests=_state('unknown'),
        node_metrics=_state('unknown'),
        cache_experiment=_state('unknown'),
        configuration=_state('unknown'),
        html=_state('unknown'),
    )


# ------------------------------------------------------------------
# Entry builders
# ------------------------------------------------------------------


def _is_service_root(root: str) -> bool:
    from .utils import log

    return os.path.realpath(root) == os.path.realpath(log.OUTPUT_DIR)


def _run_task_id(root: str, rel_path: str) -> Optional[str]:
    """Expose a task link only for the service root's own ``<task>/perf`` layout."""
    if not _is_service_root(root):
        return None
    parts = rel_path.replace('\\', '/').strip('/').split('/')
    if len(parts) >= 2 and parts[-1] == 'perf':
        return _valid_task_id(parts[-2])
    return None


def _unreadable_run_entry(root: str, rel_path: str, reason: str) -> PerformanceResultEntry:
    abs_path = os.path.join(root, rel_path)
    timestamp, basis = _mtime_iso(abs_path)
    title = os.path.basename(rel_path.rstrip('/')) or rel_path
    return PerformanceResultEntry(
        entry_id=f'run:{rel_path}',
        result_kind='run',
        root_path=root,
        path=rel_path,
        task_id=_run_task_id(root, rel_path),
        display_name=title,
        model=title,
        test_type='unknown',
        engine=None,
        timestamp=timestamp,
        time_basis=basis,  # type: ignore[arg-type]
        metric_scope='none',
        capabilities=_unreadable_capabilities(),
        actions=[],
        unreadable_reason=reason,
    )


def run_capabilities(root: str, rel_path: str, summary: dict) -> Dict[str, Any]:
    """Project one archived run's test type and surfaces for the detail contract."""
    abs_path = os.path.join(root, *rel_path.replace('\\', '/').strip('/').split('/'))
    num_runs = summary.get('num_runs') if type(summary.get('num_runs')) is int else 0
    return {
        'test_type': 'sweep' if num_runs > 1 else 'single',
        'capabilities': _run_capabilities(abs_path, num_runs, bool(summary.get('has_html'))).model_dump(mode='json'),
    }


def _run_entry(root: str, rel_path: str, summary: dict) -> PerformanceResultEntry:
    abs_path = os.path.join(root, rel_path)
    num_runs = summary.get('num_runs')
    num_runs = num_runs if type(num_runs) is int else None
    has_html = bool(summary.get('has_html'))
    engine = summary.get('engine') if summary.get('engine') in _ENGINES else 'evalscope'
    model = summary.get('model')
    if not isinstance(model, str) or model in ('', 'N/A'):
        model = ''
    alias = summary.get('alias') if isinstance(summary.get('alias'), str) else None
    title = alias or model or os.path.basename(rel_path.rstrip('/'))
    timestamp, basis = _timestamp(summary.get('timestamp'))
    test_type = 'sweep' if (num_runs or 0) > 1 else 'single'
    quality = summary.get('quality') if summary.get('quality') in task_history.QUALITY_LEVELS else None
    status = summary.get('status') if summary.get('status') in task_history.RAW_STATUSES else None
    endpoint = summary.get('api_host') if isinstance(summary.get('api_host'), str) else None
    if endpoint:
        endpoint = sanitize_endpoint(endpoint) or None

    task_id = _run_task_id(root, rel_path)
    report_path, _ = _detail_links(root, rel_path, None)
    actions = ['open']
    if has_html:
        actions.append('html')
    if (num_runs or 0) > 1 and engine != 'aiperf':
        actions.append('compare')
    actions.append('copy_config')
    # Arbitrary roots are read-only archives; only the service's own root may
    # delete a run.
    if _is_service_root(root):
        actions.append('delete')

    return PerformanceResultEntry(
        entry_id=f'run:{rel_path}',
        result_kind='run',
        root_path=root,
        path=rel_path,
        task_id=task_id,
        display_name=title,
        model=model,
        alias=alias,
        endpoint=endpoint,
        dataset=summary.get('dataset') if isinstance(summary.get('dataset'), str) else None,
        test_type=test_type,  # type: ignore[arg-type]
        engine=engine,  # type: ignore[arg-type]
        executor_version=summary.get('executor_version') if isinstance(summary.get('executor_version'), str) else None,
        execution_status=status,
        quality=quality,  # type: ignore[arg-type]
        timestamp=timestamp,
        time_basis=basis,  # type: ignore[arg-type]
        num_runs=num_runs,
        total_requests=summary.get('total_requests') if type(summary.get('total_requests')) is int else None,
        best_rps=summary.get('best_rps') if type(summary.get('best_rps')) in (int, float) else None,
        best_latency=summary.get('best_latency') if type(summary.get('best_latency')) in (int, float) else None,
        success_rate=summary.get('success_rate') if type(summary.get('success_rate')) in (int, float) else None,
        metric_scope='run' if (num_runs or 0) <= 1 else 'none',
        capabilities=_run_capabilities(abs_path, num_runs or 0, has_html),
        detail_path=report_path,
        actions=actions,  # type: ignore[arg-type]
    )


def _experiment_entry(
    root: str, base_rel: str, plan: Dict[str, Any], state: Optional[Dict[str, Any]]
) -> PerformanceResultEntry:
    abs_base = root if base_rel in ('', '.') else os.path.join(root, base_rel)
    perf_dir = os.path.join(abs_base, 'perf')
    configuration = plan.get('configuration') if isinstance(plan.get('configuration'), dict) else {}
    state = state if isinstance(state, dict) else {}

    directory_id = _valid_task_id(os.path.basename(abs_base))
    plan_task_id = _valid_task_id(configuration.get('task_id'))
    identity_ok = plan_task_id is not None and plan_task_id == directory_id

    model = configuration.get('model') if isinstance(configuration.get('model'), str) else ''
    endpoint = sanitize_endpoint(configuration.get('url')) if configuration.get('url') else None
    subruns = plan.get('subruns') if isinstance(plan.get('subruns'), list) else []
    total_subruns = state.get('total_subruns')
    if type(total_subruns) is not int:
        total_subruns = len(subruns) if subruns else None
    status = state.get('status') if state.get('status') in ('running', 'completed', 'stopped', 'error') else None
    quality = state.get('quality') if state.get('quality') in task_history.QUALITY_LEVELS else None
    state_file = os.path.join(perf_dir, 'experiment.json')
    timestamp, basis = _mtime_iso(
        state_file if os.path.isfile(state_file) else os.path.join(perf_dir, 'experiment_plan.json')
    )

    detail_path = None
    actions: List[str] = ['copy_config']
    if identity_ok:
        _, detail_path = _detail_links(root, f'{base_rel}/perf' if base_rel not in ('', '.') else 'perf', plan_task_id)
        actions.insert(0, 'open')
        actions.append('html')
    # A task link is only valid inside the service root; an external archive that
    # reuses the id must never open or control the service's own task.
    service_task = plan_task_id if identity_ok and os.path.realpath(root) == os.path.realpath(_service_root()) else None

    return PerformanceResultEntry(
        entry_id=f'experiment:{base_rel}',
        result_kind='experiment',
        root_path=root,
        path=os.path.join(base_rel, 'perf') if base_rel not in ('', '.') else 'perf',
        task_id=service_task,
        display_name=model or directory_id or base_rel,
        model=model,
        endpoint=endpoint,
        test_type='cache_experiment',
        engine='aiperf',
        execution_status=status,
        quality=quality,
        timestamp=timestamp,
        time_basis=basis,  # type: ignore[arg-type]
        num_runs=total_subruns,
        metric_scope='none',
        capabilities=_experiment_capabilities(perf_dir) if identity_ok else _unreadable_capabilities(),
        detail_path=detail_path,
        actions=actions,  # type: ignore[arg-type]
        unreadable_reason=None if identity_ok else 'task_identity_mismatch',
    )


def _plan_entry(
    root: str, base_rel: str, compiled: Dict[str, Any], manifest: Optional[Dict[str, Any]]
) -> PerformanceResultEntry:
    """Project one plan container into the unified entry shape.

    The container keeps the real requested matrix counts, the archived
    lifecycle/quality and a link to the plan report. A task link is only valid
    inside the service root, as for any other archive.
    """
    abs_base = root if base_rel in ('', '.') else os.path.join(root, base_rel)
    perf_dir = os.path.join(abs_base, 'perf')
    normalized = compiled.get('normalized') if isinstance(compiled.get('normalized'), dict) else {}
    connection = normalized.get('connection') if isinstance(normalized.get('connection'), dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    runs = compiled.get('runs') if isinstance(compiled.get('runs'), list) else []
    total_runs = manifest.get('total_runs')
    if type(total_runs) is not int:
        total_runs = len(runs) or None
    name = normalized.get('name') if isinstance(normalized.get('name'), str) else None
    model = connection.get('model') if isinstance(connection.get('model'), str) else ''
    endpoint = sanitize_endpoint(connection.get('url')) if connection.get('url') else None
    status = manifest.get('status') if manifest.get('status') in ('running', 'completed', 'stopped', 'error') else None
    quality = manifest.get('quality') if manifest.get('quality') in task_history.QUALITY_LEVELS else None
    rel_perf = f'{base_rel}/perf' if base_rel not in ('', '.') else 'perf'
    timestamp, basis = _mtime_iso(os.path.join(perf_dir, 'plan', 'manifest.json'))

    service_root = os.path.realpath(root) == os.path.realpath(_service_root())
    directory_id = _valid_task_id(os.path.basename(abs_base))
    task_id = directory_id if service_root else None
    detail_path = f'/perf-plan?{urlencode({"root_path": root, "path": rel_perf})}'
    actions: List[str] = ['open', 'html', 'copy_config']
    return PerformanceResultEntry(
        entry_id=f'plan:{base_rel}',
        result_kind='experiment',
        root_path=root,
        path=rel_perf,
        task_id=task_id,
        display_name=name or model or directory_id or base_rel,
        model=model,
        endpoint=endpoint,
        test_type='cache_experiment',
        engine='aiperf',
        execution_status=status,
        quality=quality,
        timestamp=timestamp,
        time_basis=basis,  # type: ignore[arg-type]
        num_runs=total_runs,
        metric_scope='none',
        capabilities=_plan_capabilities(perf_dir),
        detail_path=detail_path,
        actions=actions,  # type: ignore[arg-type]
    )


def _unreadable_plan_entry(root: str, base_rel: str, reason: str) -> PerformanceResultEntry:
    abs_base = root if base_rel in ('', '.') else os.path.join(root, base_rel)
    timestamp, basis = _mtime_iso(os.path.join(abs_base, 'perf', 'plan', 'compiled.json'))
    title = os.path.basename(abs_base) or base_rel
    return PerformanceResultEntry(
        entry_id=f'plan:{base_rel}',
        result_kind='experiment',
        root_path=root,
        path=os.path.join(base_rel, 'perf'),
        display_name=title,
        model=title,
        test_type='cache_experiment',
        engine='aiperf',
        timestamp=timestamp,
        time_basis=basis,  # type: ignore[arg-type]
        metric_scope='none',
        capabilities=_unreadable_capabilities(),
        actions=[],
        unreadable_reason=reason,
    )


def _unreadable_experiment_entry(root: str, base_rel: str, reason: str) -> PerformanceResultEntry:
    abs_base = root if base_rel in ('', '.') else os.path.join(root, base_rel)
    timestamp, basis = _mtime_iso(os.path.join(abs_base, 'perf', 'experiment_plan.json'))
    title = os.path.basename(abs_base) or base_rel
    return PerformanceResultEntry(
        entry_id=f'experiment:{base_rel}',
        result_kind='experiment',
        root_path=root,
        path=os.path.join(base_rel, 'perf'),
        display_name=title,
        model=title,
        test_type='cache_experiment',
        engine='aiperf',
        timestamp=timestamp,
        time_basis=basis,  # type: ignore[arg-type]
        metric_scope='none',
        capabilities=_unreadable_capabilities(),
        actions=[],
        unreadable_reason=reason,
    )


# ------------------------------------------------------------------
# Query
# ------------------------------------------------------------------


def _collect(root: str) -> Tuple[List[PerformanceResultEntry], int, int]:
    """Return ``(entries, scanned, skipped)`` for one root."""
    containers = _scan_containers(root)
    container_bases = [base for base, _ in containers]
    entries: List[PerformanceResultEntry] = []
    skipped = 0
    scanned = 0

    for base, kind in containers:
        scanned += 1
        perf_dir = os.path.join(root, base, 'perf') if base not in ('', '.') else os.path.join(root, 'perf')
        if kind == 'plan':
            compiled = _read_json(os.path.join(perf_dir, 'plan', 'compiled.json'))
            manifest = _read_json(os.path.join(perf_dir, 'plan', 'manifest.json'))
            if compiled is None:
                skipped += 1
                entries.append(_unreadable_plan_entry(root, base, 'archive_unreadable'))
                continue
            entries.append(_plan_entry(root, base, compiled, manifest))
            continue
        plan = _read_json(os.path.join(perf_dir, 'experiment_plan.json'))
        state = _read_json(os.path.join(perf_dir, 'experiment.json'))
        if plan is None:
            skipped += 1
            entries.append(_unreadable_experiment_entry(root, base, 'archive_unreadable'))
            continue
        entry = _experiment_entry(root, base, plan, state)
        if entry.unreadable_reason:
            skipped += 1
        entries.append(entry)

    for rel_path in perf_archive.scan_perf_runs(root):
        if _inside_container(rel_path, container_bases):
            continue
        scanned += 1
        abs_path = os.path.join(root, rel_path)
        try:
            summary = perf_archive.build_run_summary(rel_path, abs_path)
        except Exception as error:  # noqa: BLE001 - one bad archive must not fail the list
            logger.warning(f'Unreadable perf archive {rel_path}: {type(error).__name__}')
            summary = None
        if summary is None:
            skipped += 1
            entries.append(_unreadable_run_entry(root, rel_path, 'archive_unreadable'))
            continue
        entries.append(_run_entry(root, rel_path, summary))

    return entries, scanned, skipped


def _matches(entry: PerformanceResultEntry, test_type: Optional[str], engine: Optional[str], query: str) -> bool:
    if test_type and entry.test_type != test_type:
        return False
    if engine and entry.engine != engine:
        return False
    if query:
        haystack = ' '.join(
            value
            for value in (entry.display_name, entry.model, entry.alias, entry.endpoint, entry.task_id, entry.path)
            if value
        ).lower()
        if query not in haystack:
            return False
    return True


def _sort_key(sort: str):
    if sort == 'rps':
        return lambda entry: (entry.best_rps is None, -(entry.best_rps or 0.0), entry.entry_id)
    if sort == 'latency':
        return lambda entry: (entry.best_latency is None, entry.best_latency or 0.0, entry.entry_id)
    return lambda entry: (entry.timestamp is None, _negated_time(entry.timestamp), entry.entry_id)


def _negated_time(value: Optional[str]) -> float:
    """Descending time order with a numeric key that survives mixed offsets."""
    parsed, _ = task_history.parse_time(value)
    return -parsed.timestamp() if parsed is not None else 0.0


def list_entries(
    root_path: Optional[str],
    test_type: Optional[str],
    engine: Optional[str],
    query: str,
    sort: str,
    offset: int,
    limit: int,
) -> Dict[str, Any]:
    """Build the filtered, paginated unified entry page for *root_path*."""
    if test_type and test_type not in _TEST_TYPES:
        raise PerfArchiveError('Invalid test_type', 400)
    if engine and engine not in _ENGINES:
        raise PerfArchiveError('Invalid engine', 400)
    if sort not in _SORTS:
        raise PerfArchiveError('Invalid sort', 400)
    if offset < 0 or not 1 <= limit <= _MAX_LIMIT:
        raise PerfArchiveError('Invalid page', 400)

    root = _resolve_root(root_path)
    entries, scanned, skipped = _collect(root)
    normalized_query = query.strip().lower()
    filtered = [entry for entry in entries if _matches(entry, test_type or None, engine or None, normalized_query)]
    filtered.sort(key=_sort_key(sort))
    page = filtered[offset : offset + limit]
    return PerfEntryListResponse(
        entries=page,
        total=len(filtered),
        known_total=len(entries),
        completeness='partial' if skipped else 'complete',
        offset=offset,
        limit=limit,
        observed_at=datetime.now().astimezone().isoformat(),
        root_path=root,
        scanned=scanned,
        skipped=skipped,
    ).model_dump(mode='json')
