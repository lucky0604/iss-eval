import json
import math
import os
from datetime import datetime
from urllib.parse import quote, urlencode

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue
from pydantic import ValidationError

from evalscope.report.data_frames import scan_report_refs
from evalscope.service.api_models import (
    ActiveTaskInfo,
    EvalInvokeResponse,
    ListActiveTasksResponse,
    TaskDetailResponse,
    TaskListResponse,
    TaskStatusResponse,
    TaskSummaryResponse,
)
from evalscope.service.api_models.common import TaskConfigSummary
from evalscope.utils.logger import get_logger
from evalscope.utils.uri_utils import sanitize_endpoint

from ..responses import json_response
from ..utils import (
    OUTPUT_DIR,
    get_task_queue,
    list_active_tasks,
    read_task_manifest,
    read_task_result,
    task_file_path,
    validate_task_id,
)

logger = get_logger()

bp_tasks = Blueprint('tasks', __name__, url_prefix='/api/v1/tasks')

# Progress tracker file location relative to the task output directory, per kind.
_PROGRESS_SUBPATH = {
    'eval': 'progress.json',
    'perf': os.path.join('perf', 'progress.json'),
}


def _read_progress_percent(task_id: str, kind: str) -> float | None:
    sub_path = _PROGRESS_SUBPATH.get(kind)
    if not sub_path:
        return None
    try:
        progress_file = task_file_path(task_id, sub_path)
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(progress, dict):
        return None
    percent = progress.get('percent')
    if type(percent) not in (int, float) or not math.isfinite(percent):
        return None
    return max(0.0, min(100.0, float(percent)))


def _experiment_progress(task_id: str) -> dict:
    try:
        path = task_file_path(task_id, os.path.join('perf', 'experiment.json'))
        if os.path.getsize(path) > 16 * 1024 * 1024:
            return {}
        with open(path, encoding='utf-8') as file:
            state = json.load(file)
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    completed, total = state.get('completed_subruns'), state.get('total_subruns')
    if type(completed) is not int or type(total) is not int or total <= 0 or not 0 <= completed <= total:
        return {}
    phase = state.get('phase') or state.get('last_phase')
    if phase not in ('preflight', 'generating', 'priming', 'profiling', 'draining', 'importing', None):
        phase = None
    current = state.get('current_subrun')
    if not isinstance(current, str) or len(current) > 100:
        current = None
    percent = 100.0 * completed / total
    if state.get('status') != 'completed':
        percent = min(percent, 99.9)
    quality = state.get('quality')
    if quality not in ('complete', 'partial', 'invalid'):
        quality = None
    return {
        'percent': percent,
        'phase': phase,
        'current_subrun': current,
        'completed_subruns': completed,
        'total_subruns': total,
        'quality': quality,
    }


def _plan_progress(task_id: str) -> dict:
    """Progress of a portable plan, read from its own manifest/submission."""
    from ..perf_plans import plan_progress, task_perf_root

    try:
        return plan_progress(task_perf_root(OUTPUT_DIR, task_id)) or {}
    except (OSError, ValueError):
        return {}


def _aiperf_progress(task_id: str) -> dict:
    progress = _plan_progress(task_id)
    if progress:
        return progress
    progress = _experiment_progress(task_id)
    if progress:
        return progress
    from evalscope.perf.results.single_run import load_single_manifest

    try:
        manifest = load_single_manifest(task_file_path(task_id, 'perf'))
        return {'quality': manifest.quality, 'percent': 100.0 if manifest.status == 'completed' else None}
    except (OSError, ValueError):
        return {}


def _progress_fields(details: dict) -> dict:
    """Keep only keys the task-info contract accepts."""
    from ..api_models import ActiveTaskInfo

    return {key: value for key, value in details.items() if key in ActiveTaskInfo.model_fields}


def _with_progress(record: dict) -> dict:
    if record.get('status') == 'queued':
        record['percent'] = 0.0
        return record
    if record.get('engine') == 'aiperf':
        record.update(_progress_fields(_aiperf_progress(record['task_id'])))
        if record.get('status') != 'cancelling':
            record['status'] = 'finalizing' if record.get('percent') == 100 else 'running'
        return record
    percent = _read_progress_percent(record['task_id'], record.get('kind', ''))
    record['percent'] = percent
    if record.get('status') == 'cancelling':
        return record
    if percent is not None and percent >= 100:
        record['status'] = 'finalizing'
    else:
        record['status'] = 'running'
    return record


def _saved_task_info(task_id: str, manifest: dict) -> ActiveTaskInfo:
    values = {key: manifest[key] for key in ('kind', 'engine', 'model', 'alias', 'started_at') if key in manifest}
    config = manifest.get('config')
    if isinstance(config, dict):
        values['config'] = {key: value for key, value in config.items() if key in TaskConfigSummary.model_fields}
    elif config is not None:
        values['config'] = config
    status = manifest.get('status')
    if status not in ('completed', 'error', 'stopped'):
        status = 'unknown'
    return ActiveTaskInfo(
        **values,
        task_id=task_id,
        endpoint=sanitize_endpoint(manifest.get('endpoint') or manifest.get('api_url')),
        status=status,
    )


def _set_report_links(info: ActiveTaskInfo, run_dir: str) -> None:
    if info.kind == 'perf' and info.engine == 'aiperf':
        from .. import perf_archive_aiperf

        perf_dir = os.path.join(run_dir, 'perf')
        if perf_archive_aiperf.is_single_archive(perf_dir):
            try:
                perf_archive_aiperf.build_summary(info.task_id + '/perf', perf_dir)
                params = urlencode({'root_path': OUTPUT_DIR, 'path': info.task_id + '/perf'})
                info.report_url = f'/perf-report?{params}'
                info.html_report_url = f'/api/v1/perf/history/report?{params}'
            except (ValueError, OSError, perf_archive_aiperf.PerfArchiveError):
                pass
            return
        from ..perf_experiments import load_experiment, load_scenario

        try:
            experiment = load_experiment(info.task_id)
            readable = False
            for row in experiment.state.subruns:
                if row.scenario is None:
                    continue
                try:
                    if load_scenario(info.task_id, row.subrun_id).result is not None:
                        readable = True
                        break
                except (ValueError, OSError):
                    continue
            if readable:
                info.report_url = f'/perf-experiment/{quote(info.task_id, safe="")}'
                info.html_report_url = f'/api/v1/perf/experiments/{quote(info.task_id, safe="")}/report'
        except (ValueError, OSError):
            pass
        return
    if info.status not in ('completed', 'unknown'):
        return
    task_id = info.task_id
    root_query = urlencode({'root_path': OUTPUT_DIR})
    task_query = urlencode({'task_id': task_id})
    if info.kind == 'perf':
        from ..perf_archive import build_run_summary

        perf_dir = os.path.join(run_dir, 'perf')
        if os.path.isfile(os.path.join(perf_dir, 'perf_report.html')):
            info.html_report_url = f'/api/v1/perf/report?{task_query}'
        summary = build_run_summary(task_id + '/perf', perf_dir)
        if summary and summary['num_runs'] > 0:
            path_query = urlencode({'path': task_id + '/perf'})
            info.report_url = f'/perf-report?{root_query}&{path_query}'
        return

    refs = [ref for ref in scan_report_refs(OUTPUT_DIR) if ref.run_id == task_id]
    if len(refs) == 1:
        run_id = quote(task_id, safe='')
        model_id = quote(refs[0].model_id, safe='')
        info.report_url = f'/reports/{run_id}/{model_id}?{root_query}'
    elif refs:
        search_query = urlencode({'search': task_id})
        info.report_url = f'/reports?{root_query}&{search_query}'
    if os.path.isfile(os.path.join(run_dir, 'reports', 'report.html')):
        info.html_report_url = f'/api/v1/eval/report?{task_query}'


@bp_tasks.route('', methods=['GET'])
def list_tasks() -> ResponseReturnValue:
    """List tasks from the persistent history index merged with live queue state.

    Query params:
        view (str): ``all`` | ``active`` | ``attention`` | ``history``
        kind (str): ``eval`` | ``perf``
        engine (str): ``evalscope`` | ``aiperf``
        status (str): repeatable or comma-separated raw status filter
        q (str): free-text match over task id / model / alias / endpoint
        from, to (str): ISO time range applied to the effective time
        offset (int), limit (int, 1-100), sort (str): ``recent`` | ``queue``
    """
    from .. import task_detail, task_history

    try:
        offset = max(0, int(request.args.get('offset', '0')))
    except ValueError:
        return jsonify({'error': 'Invalid offset'}), 400
    try:
        limit = int(request.args.get('limit', str(task_history.DEFAULT_PAGE_SIZE)))
    except ValueError:
        return jsonify({'error': 'Invalid limit'}), 400
    limit = max(1, min(task_history.MAX_PAGE_SIZE, limit))
    statuses = [item for value in request.args.getlist('status') for item in value.split(',') if item.strip()]
    try:
        result = task_history.query_records(
            view=request.args.get('view', 'all'),
            kind=request.args.get('kind') or None,
            engine=request.args.get('engine') or None,
            statuses=statuses,
            query=request.args.get('q', ''),
            start=request.args.get('from') or None,
            end=request.args.get('to') or None,
            sort=request.args.get('sort', 'recent'),
        )
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    records = result['records']
    page = records[offset : offset + limit]
    items = []
    for record in page:
        if record.get('status') in ('queued', 'preparing', 'running', 'cancelling', 'finalizing'):
            record = _with_progress(dict(record))
        items.append(task_detail.build_summary_item(record).model_dump(mode='json'))
    return json_response(
        TaskListResponse,
        {
            'items': items,
            'total': len(records),
            'known_total': result['known_total'],
            'completeness': result['completeness'],
            'offset': offset,
            'limit': limit,
            'observed_at': datetime.now().astimezone().isoformat(),
            'revision': result['revision'],
            'indexing': result['indexing'],
            'excluded_without_time': result['excluded_without_time'],
        },
    )


@bp_tasks.route('/summary', methods=['GET'])
def summarize_tasks() -> ResponseReturnValue:
    """Global Task Center counts, computed over the whole history (never one page)."""
    from .. import task_history

    try:
        summary = task_history.summarize(
            start=request.args.get('from') or None,
            end=request.args.get('to') or None,
        )
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    return json_response(TaskSummaryResponse, summary)


@bp_tasks.route('/<task_id>/detail', methods=['GET'])
def get_task_detail(task_id: str) -> ResponseReturnValue:
    """Task detail: list projection plus the sanitized configuration snapshot."""
    from .. import task_detail, task_history

    try:
        validate_task_id(task_id)
    except ValueError:
        return jsonify({'error': 'Invalid task_id'}), 400
    record = task_history.find_record(task_id)
    if record is None:
        return jsonify({'error': 'Task not found'}), 404
    if record.get('status') in ('queued', 'preparing', 'running', 'cancelling', 'finalizing'):
        record = _with_progress(record)
    return json_response(TaskDetailResponse, task_detail.build_detail(record).model_dump(mode='json'))


@bp_tasks.route('/<task_id>', methods=['GET'])
def get_task(task_id: str) -> ResponseReturnValue:
    try:
        validate_task_id(task_id)
        task_file_path(task_id, 'perf')
        task_file_path(task_id, 'reports')
    except ValueError:
        return jsonify({'error': 'Invalid task_id'}), 400
    active = next((item for item in list_active_tasks() if item['task_id'] == task_id), None)
    if active:
        return json_response(ActiveTaskInfo, _with_progress(active))
    queued = get_task_queue().get(task_id)
    if queued is not None:
        return json_response(ActiveTaskInfo, _with_progress(queued))
    run_dir = os.path.join(OUTPUT_DIR, task_id)
    manifest = read_task_manifest(run_dir)
    if manifest is None:
        if not os.path.isdir(run_dir):
            return jsonify({'error': 'Task not found'}), 404
        manifest = {'kind': 'perf' if os.path.isdir(os.path.join(run_dir, 'perf')) else 'eval'}
    try:
        info = _saved_task_info(task_id, manifest)
    except ValidationError:
        return jsonify({'error': 'Task metadata is unreadable'}), 400
    if info.engine == 'aiperf':
        for key, value in _progress_fields(_aiperf_progress(task_id)).items():
            setattr(info, key, value)
    else:
        info.percent = _read_progress_percent(task_id, info.kind)
    _set_report_links(info, run_dir)
    return json_response(ActiveTaskInfo, info.model_dump(mode='json'))


@bp_tasks.route('/active', methods=['GET'])
def list_active() -> ResponseReturnValue:
    """List tasks that are queued or whose subprocess is registered on this service.

    Queued work lives in the configured queue backend: in memory for the
    ``inprocess`` backend (empty after a restart), or in Redis for the
    ``redis`` backend (survives restarts of the web process and the worker).
    Running tasks are reported by the local subprocess registry, so a worker's
    tasks are not visible from the web process unless the queue backend
    reports them.
    """
    try:
        records = {task['task_id']: task for task in get_task_queue().list()}
        for record in list_active_tasks():
            records[record['task_id']] = record
        tasks = [_with_progress(record) for record in records.values()]
        tasks.sort(key=lambda t: t.get('started_at') or t.get('enqueued_at') or '', reverse=True)
        return json_response(ListActiveTasksResponse, {'tasks': tasks, 'total': len(tasks)})
    except Exception as e:
        logger.error(f'Failed to list active tasks: {e}')
        return jsonify({'error': str(e)}), 500


@bp_tasks.route('/<task_id>/result', methods=['GET'])
def get_task_result(task_id: str) -> ResponseReturnValue:
    """Return the persisted invoke payload of a finished task.

    Clients use this after a reload to recover the result of a task that
    completed while the page was closed. Responds 202 while the task is still
    queued or running and 404 when no result was persisted.
    """
    try:
        validate_task_id(task_id)
    except ValueError:
        return jsonify({'error': 'Invalid task_id'}), 400
    # The active check comes first: reusing a task id keeps the previous run's
    # result.json on disk, which must not be served while the new run is active.
    active = get_task_queue().get(task_id) or next(
        (item for item in list_active_tasks() if item['task_id'] == task_id), None
    )
    if active is not None:
        return json_response(
            TaskStatusResponse, {'status': active.get('status', 'running'), 'task_id': task_id}, status=202
        )
    payload = read_task_result(task_id)
    if payload is not None:
        return json_response(EvalInvokeResponse, payload)
    return jsonify({'error': f'Result not found for task_id: {task_id}'}), 404
