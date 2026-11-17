"""Report endpoints backed by one fixed snapshot per archive.

Paths are versioned separately from the existing performance routes so old
consumers keep their behaviour: these endpoints never cap a result set to the
current page, and the request page reads the whole normalized store through a
server-side index. Exports have their own lifecycle, id and files.
"""

import time
from typing import Optional

from flask import Blueprint, current_app, jsonify, redirect, request, send_file, url_for
from pydantic import ValidationError

from ..api_models.perf_exports import (
    PerfExportCapabilitiesResponse,
    PerfExportResponse,
    PerfExportSpec,
    PerfExportStatusResponse,
    PerfExportView,
)
from ..api_models.perf_reports import (
    PerfReportChartsResponse,
    PerfReportRequestsResponse,
    PerfReportRunsResponse,
    PerfReportSummaryResponse,
)
from ..perf_exports import ExportRecord, ExportSpec, pdf_runtime, runner
from ..perf_reports import PAGE_LIMIT, PerfReportError, load_report_snapshot
from ..responses import json_response
from ..utils.log import OUTPUT_DIR

bp_perf_reports = Blueprint('perf_reports', __name__, url_prefix='/api/v1/perf')


def _location() -> dict:
    return {
        'outputs_root': request.args.get('root_path') or current_app.config.get('OUTPUTS_ROOT') or OUTPUT_DIR,
        'task_id': request.args.get('task_id') or None,
        'root_path': request.args.get('root_path') or None,
        'rel_path': request.args.get('path') or None,
    }


def _int_arg(name: str, default: int) -> int:
    value = request.args.get(name)
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise PerfReportError(f'Invalid {name}', 400) from None


def _order() -> str:
    order = request.args.get('order', 'asc')
    if order not in ('asc', 'desc'):
        raise PerfReportError('Invalid order', 400)
    return order


class _SnapshotConflict(Exception):
    """The archive moved after the client last read it; reload, don't mix states."""

    def __init__(self, snapshot_id: str) -> None:
        super().__init__(snapshot_id)
        self.snapshot_id = snapshot_id


@bp_perf_reports.errorhandler(_SnapshotConflict)
def _snapshot_conflict(error: _SnapshotConflict):
    return (
        jsonify({'error': 'The archive changed; reload the report.', 'snapshot_id': error.snapshot_id}),
        409,
    )


def _snapshot():
    snapshot = load_report_snapshot(**_location())
    requested = request.args.get('snapshot_id') or None
    if requested and requested != snapshot.document.snapshot_id:
        # A client pinning an older snapshot must never receive a mix of old
        # and new values; it reloads and re-reads every endpoint.
        raise _SnapshotConflict(snapshot.document.snapshot_id)
    return snapshot


@bp_perf_reports.route('/reports', methods=['GET'])
def report_summary():
    try:
        snapshot = _snapshot()
        return json_response(
            PerfReportSummaryResponse,
            {'report': snapshot.header(), 'total_runs': len(snapshot.document.runs)},
        )
    except PerfReportError as error:
        return jsonify({'error': error.message}), error.status


@bp_perf_reports.route('/reports/runs', methods=['GET'])
def report_runs():
    try:
        limit = _int_arg('limit', 100)
        if limit > PAGE_LIMIT:
            raise PerfReportError(f'limit must be between 1 and {PAGE_LIMIT}', 400)
        snapshot = _snapshot()
        sort = request.args.get('sort', 'comparison')
        order = _order()
        rows, total = snapshot.runs_page(
            offset=_int_arg('offset', 0),
            limit=limit,
            group=request.args.get('group') or None,
            sort=sort,
            order=order,
        )
        return json_response(
            PerfReportRunsResponse,
            {
                'snapshot_id': snapshot.document.snapshot_id,
                'rows': rows,
                'total': total,
                'offset': _int_arg('offset', 0),
                'limit': limit,
                'group': request.args.get('group') or None,
                'sort': sort,
                'order': order,
            },
        )
    except PerfReportError as error:
        return jsonify({'error': error.message}), error.status


def _find_run_key(snapshot, run_key: Optional[str], run_id: Optional[str]) -> str:
    if run_key:
        return run_key
    if not run_id:
        raise PerfReportError('A run_key or run_id is required', 400)
    matches = [run.run_key for run in snapshot.document.runs if run.run_id == run_id]
    if not matches:
        raise PerfReportError('Run not found', 404)
    if len(matches) > 1:
        raise PerfReportError('run_id is ambiguous; pass run_key', 400)
    return matches[0]


@bp_perf_reports.route('/reports/requests', methods=['GET'])
def report_requests():
    try:
        limit = _int_arg('limit', 100)
        if limit > PAGE_LIMIT:
            raise PerfReportError(f'limit must be between 1 and {PAGE_LIMIT}', 400)
        offset = _int_arg('offset', 0)
        snapshot = _snapshot()
        run_key = _find_run_key(snapshot, request.args.get('run_key') or None, request.args.get('run_id') or None)
        status = request.args.get('status') or None
        phase = request.args.get('phase', 'profiling')
        if phase in ('', 'all'):
            phase = None
        search = request.args.get('search') or None
        sort = request.args.get('sort', 'index')
        order = _order()
        total, rows = snapshot.requests_page(
            run_key=run_key,
            offset=offset,
            limit=limit,
            status=status,
            phase=phase,
            search=search,
            sort=sort,
            order=order,
        )
        run = next((item for item in snapshot.document.runs if item.run_key == run_key), None)
        if run is None:
            raise PerfReportError('Run not found', 404)
        return json_response(
            PerfReportRequestsResponse,
            {
                'snapshot_id': snapshot.document.snapshot_id,
                'run_key': run_key,
                'run_id': run.run_id,
                'total': total,
                'offset': offset,
                'limit': limit,
                'status': status,
                'phase': phase,
                'search': search,
                'sort': sort,
                'order': order,
                'requests': rows,
            },
        )
    except PerfReportError as error:
        return jsonify({'error': error.message}), error.status


@bp_perf_reports.route('/reports/charts', methods=['GET'])
def report_charts():
    try:
        snapshot = _snapshot()
        metric = request.args.get('metric', 'ttft')
        statistic = request.args.get('statistic', 'p95')
        series = snapshot.charts(metric=metric, statistic=statistic)
        return json_response(
            PerfReportChartsResponse,
            {
                'snapshot_id': snapshot.document.snapshot_id,
                'metric': metric,
                'statistic': statistic,
                'series': series,
            },
        )
    except PerfReportError as error:
        return jsonify({'error': error.message}), error.status


# ---------------------------------------------------------------------------
# Exports: an independent lifecycle, never the load-test task's own state
# ---------------------------------------------------------------------------

EXPORT_WAIT_SECONDS = 25.0
_EXPORT_FORMATS = {'html': 'html', 'pdf': 'pdf', 'csv': 'csv', 'zip': 'bundle', 'requests': 'requests'}


def _outputs_root() -> str:
    return current_app.config.get('OUTPUTS_ROOT') or OUTPUT_DIR


def _build_export_spec(payload: dict) -> ExportSpec:
    try:
        model = PerfExportSpec.model_validate(payload)
    except ValidationError as error:
        raise PerfReportError(f'Invalid export request: {error.errors()[0]["msg"]}', 400) from None
    return ExportSpec(**model.model_dump())


def _export_view(record: ExportRecord) -> dict:
    return {
        'export_id': record.export_id,
        'status': record.status,
        'created_at': record.created_at,
        'started_at': record.started_at,
        'finished_at': record.finished_at,
        'attempts': record.attempts,
        'progress': record.progress,
        'error': record.error,
        'warnings': record.warnings,
        'snapshot_id': record.snapshot_id,
        'snapshot_created_at': record.snapshot_created_at,
        'source_kind': record.source_kind,
        'run_count': record.run_count,
        'request_count': record.request_count,
        'spec': record.spec.model_dump(),
        'artifacts': [
            {
                'name': artifact.name,
                'filename': artifact.filename,
                'media_type': artifact.media_type,
                'size': artifact.size,
                'sha256': artifact.sha256,
                'url': url_for('perf_reports.export_download', export_id=record.export_id, file=artifact.name),
            }
            for artifact in record.artifacts
        ],
    }


@bp_perf_reports.route('/reports/exports', methods=['POST'])
def export_create():
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise PerfReportError('A JSON export specification is required', 400)
        record, created = runner.submit(_build_export_spec(payload), outputs_root=_outputs_root())
        return json_response(
            PerfExportResponse,
            {'export': _export_view(record), 'created': created},
            status=202 if created else 200,
        )
    except PerfReportError as error:
        return jsonify({'error': error.message}), error.status
    except OSError as error:
        return jsonify({'error': f'Export storage is unavailable: {error}'}), 503


@bp_perf_reports.route('/reports/exports/<export_id>', methods=['GET'])
def export_status(export_id: str):
    try:
        record = runner.get(export_id, outputs_root=_outputs_root())
        return json_response(PerfExportStatusResponse, {'export': _export_view(record)})
    except PerfReportError as error:
        return jsonify({'error': error.message}), error.status
    except ValueError:
        return jsonify({'error': 'Invalid export id'}), 404


@bp_perf_reports.route('/reports/exports/<export_id>/download', methods=['GET'])
def export_download(export_id: str):
    try:
        path, artifact = runner.download_path(
            export_id, outputs_root=_outputs_root(), name=request.args.get('file') or None
        )
    except PerfReportError as error:
        return jsonify({'error': error.message}), error.status
    except ValueError:
        return jsonify({'error': 'Invalid export id'}), 404
    return send_file(
        path,
        mimetype=artifact.media_type.split(';')[0],
        as_attachment=True,
        download_name=artifact.filename,
        conditional=True,
        max_age=0,
    )


@bp_perf_reports.route('/reports/exports/<export_id>/cancel', methods=['POST'])
def export_cancel(export_id: str):
    try:
        record = runner.cancel(export_id, outputs_root=_outputs_root())
        return json_response(PerfExportStatusResponse, {'export': _export_view(record)})
    except PerfReportError as error:
        return jsonify({'error': error.message}), error.status
    except ValueError:
        return jsonify({'error': 'Invalid export id'}), 404


@bp_perf_reports.route('/reports/exports/<export_id>/retry', methods=['POST'])
def export_retry(export_id: str):
    try:
        record, created = runner.retry(export_id, outputs_root=_outputs_root())
        return json_response(
            PerfExportResponse,
            {'export': _export_view(record), 'created': created},
            status=202 if created else 200,
        )
    except PerfReportError as error:
        return jsonify({'error': error.message}), error.status
    except OSError as error:
        return jsonify({'error': f'Export storage is unavailable: {error}'}), 503


@bp_perf_reports.route('/reports/export-capabilities', methods=['GET'])
def export_capabilities():
    available, renderer, message = pdf_runtime()
    return json_response(
        PerfExportCapabilitiesResponse,
        {
            'pdf_available': available,
            'pdf_renderer': renderer,
            'pdf_message': message,
            'kinds': ['html', 'pdf', 'csv', 'requests', 'bundle'],
        },
    )


def _query_spec(kind: str) -> ExportSpec:
    payload = {
        'kind': kind,
        'task_id': request.args.get('task_id') or None,
        'path': request.args.get('path') or None,
        'root_path': request.args.get('root_path') or None,
        'group': request.args.get('group') or None,
        'preset': request.args.get('preset') or 'core',
        'locale': request.args.get('locale') or 'en',
        'status': request.args.get('status') or None,
        'search': request.args.get('search') or None,
        'run_key': request.args.get('run_key') or None,
        'phase': request.args.get('phase') or 'profiling',
    }
    return _build_export_spec({key: value for key, value in payload.items() if value is not None})


def _wait_and_download(kind: str, artifact_name: Optional[str] = None):
    """Compatibility for links that predate the export surface.

    The export is created through the same independent lifecycle; this route
    only waits for it briefly and then redirects to the id-addressed download.
    """
    try:
        record, _created = runner.submit(_query_spec(kind), outputs_root=_outputs_root())
        deadline = time.monotonic() + EXPORT_WAIT_SECONDS
        while record.status in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(0.2)
            record = runner.get(record.export_id, outputs_root=_outputs_root())
        if record.status == 'completed':
            name = artifact_name or record.artifacts[0].name
            return redirect(url_for('perf_reports.export_download', export_id=record.export_id, file=name))
        if record.status in ('failed', 'cancelled', 'interrupted'):
            return jsonify({'error': record.error or record.status, 'export_id': record.export_id}), 503
        return json_response(
            PerfExportResponse,
            {'export': _export_view(record), 'created': True},
            status=202,
        )
    except PerfReportError as error:
        return jsonify({'error': error.message}), error.status
    except OSError as error:
        return jsonify({'error': f'Export storage is unavailable: {error}'}), 503


@bp_perf_reports.route('/reports/export.<fmt>', methods=['GET'])
def export_convenience(fmt: str):
    kind = _EXPORT_FORMATS.get(fmt)
    if kind is None:
        return jsonify({'error': 'Unsupported export format'}), 404
    name = 'requests_csv' if kind == 'requests' else None
    return _wait_and_download(kind, name)


@bp_perf_reports.route('/reports/requests.csv', methods=['GET'])
def requests_csv():
    """Every matching request of one run as CSV, through the export store."""
    return _wait_and_download('requests', 'requests_csv')
