import json
import math
import os
from urllib.parse import quote, urlencode

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue
from pydantic import ValidationError

from evalscope.report.data_frames import scan_report_refs
from evalscope.service.api_models import ActiveTaskInfo, ListActiveTasksResponse
from evalscope.service.api_models.common import TaskConfigSummary
from evalscope.utils.logger import get_logger
from evalscope.utils.uri_utils import sanitize_endpoint

from ..responses import json_response
from ..utils import OUTPUT_DIR, list_active_tasks, read_task_manifest, task_file_path, validate_task_id

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


def _aiperf_progress(task_id: str) -> dict:
    progress = _experiment_progress(task_id)
    if progress:
        return progress
    from evalscope.perf.results.single_run import load_single_manifest

    try:
        manifest = load_single_manifest(task_file_path(task_id, 'perf'))
        return {'quality': manifest.quality, 'percent': 100.0 if manifest.status == 'completed' else None}
    except (OSError, ValueError):
        return {}


def _with_progress(record: dict) -> dict:
    if record.get('engine') == 'aiperf':
        record.update(_aiperf_progress(record['task_id']))
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
        for key, value in _aiperf_progress(task_id).items():
            setattr(info, key, value)
    else:
        info.percent = _read_progress_percent(task_id, info.kind)
    _set_report_links(info, run_dir)
    return json_response(ActiveTaskInfo, info.model_dump(mode='json'))


@bp_tasks.route('/active', methods=['GET'])
def list_active() -> ResponseReturnValue:
    """List tasks whose subprocess is currently registered on this service.

    The registry is in-memory: tasks vanish from this list when they finish or
    when the service restarts. This registry does not recover or supervise
    child processes left behind by an abrupt service shutdown.
    """
    try:
        tasks = []
        for record in list_active_tasks():
            tasks.append(_with_progress(record))
        tasks.sort(key=lambda t: t.get('started_at', ''), reverse=True)
        return json_response(ListActiveTasksResponse, {'tasks': tasks, 'total': len(tasks)})
    except Exception as e:
        logger.error(f'Failed to list active tasks: {e}')
        return jsonify({'error': str(e)}), 500
