"""The export lifecycle: claim, render, publish, cancel, recover.

Exports run on their own bounded executor and own their own on-disk state.
They never touch the load-test task's manifest, ledger or result files: the
only writes happen under ``<archive>/exports/<export_id>/`` (or the service
fallback store), and a completed record is the only thing that can expose
artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from evalscope.utils.logger import get_logger

from ..perf_reports import PerfReportError, ReportSnapshot, load_report_snapshot, resolve_report_root
from .collect import ExportDataset, collect
from .csv_export import (
    write_node_cache_csv,
    write_node_metrics_csv,
    write_requests_csv,
    write_requests_jsonl,
    write_summary_csv,
)
from .html import render_html
from .models import (
    EXPORT_TEMPLATE_VERSION,
    ExportArtifact,
    ExportCancelled,
    ExportRecord,
    ExportSpec,
)
from .pdf import PdfUnavailable, render_pdf
from .store import (
    CANCEL_NAME,
    ExportStore,
    export_dir,
    exports_base,
    lookup_export_base,
    register_export_base,
)

logger = get_logger()

WORK_DIR = 'work'
EXPORT_WORKERS_ENV = 'EVALSCOPE_EXPORT_WORKERS'

#: ``(logical name, download filename, media type)`` for every standalone artifact.
_ARTIFACTS = {
    'html': ('perf-report-{snapshot}-{locale}.html', 'text/html; charset=utf-8'),
    'pdf': ('perf-report-{snapshot}-{locale}.pdf', 'application/pdf'),
    'summary': ('performance-matrix-{snapshot}-{locale}.csv', 'text/csv; charset=utf-8'),
    'node_metrics': ('node-metrics-{snapshot}.csv', 'text/csv; charset=utf-8'),
    'node_cache': ('node-cache-{snapshot}.csv', 'text/csv; charset=utf-8'),
    'requests_csv': ('requests-{snapshot}.csv', 'text/csv; charset=utf-8'),
    'requests_jsonl': ('requests-{snapshot}.jsonl', 'application/x-ndjson'),
    'bundle': ('perf-report-{snapshot}-{locale}.zip', 'application/zip'),
}

#: Staged file name inside the work directory and inside a bundle.
_INNER_NAMES = {
    'html': 'report.html',
    'pdf': 'report.pdf',
    'summary': 'summary.csv',
    'node_metrics': 'node_metrics.csv',
    'node_cache': 'node_cache.csv',
    'requests_csv': 'requests.csv',
    'requests_jsonl': 'requests.jsonl',
    'bundle': 'bundle.zip',
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _filename(name: str, snapshot_id: str, locale: str) -> str:
    template, _media = _ARTIFACTS[name]
    return template.format(snapshot=snapshot_id, locale=locale)


def _artifact(name: str, path: Path, snapshot_id: str, locale: str) -> ExportArtifact:
    return ExportArtifact(
        name=name,
        filename=_filename(name, snapshot_id, locale),
        media_type=_ARTIFACTS[name][1],
        size=path.stat().st_size,
        sha256=_sha256(path),
    )


class ExportRunner:
    """Owns the background executor and the state machine of one service."""

    def __init__(self) -> None:
        self._executor: Optional[ThreadPoolExecutor] = None
        self._lock = threading.Lock()

    def _executor_for(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._executor is None:
                workers = max(1, int(os.environ.get(EXPORT_WORKERS_ENV, '2')))
                self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='perf-export')
            return self._executor

    # -- store resolution ------------------------------------------------
    def store_for(self, spec: ExportSpec, *, outputs_root: str) -> Tuple[ExportStore, ReportSnapshot]:
        # Mirrors the report endpoints: ``root_path`` may also carry the root
        # the archive path is relative to.
        location_root = spec.root_path or outputs_root
        root = resolve_report_root(
            outputs_root=location_root,
            task_id=spec.task_id,
            root_path=spec.root_path,
            rel_path=spec.path,
        )
        snapshot = load_report_snapshot(
            outputs_root=location_root,
            task_id=spec.task_id,
            root_path=spec.root_path,
            rel_path=spec.path,
        )
        identity = snapshot.document.source_identity or f'path:{root}'
        return ExportStore(exports_base(root, identity)), snapshot

    # -- public operations ----------------------------------------------
    def submit(self, spec: ExportSpec, *, outputs_root: str) -> Tuple[ExportRecord, bool]:
        """Create (or reuse) the export for *spec* and queue it when it is new."""
        store, snapshot = self.store_for(spec, outputs_root=outputs_root)
        record, created = store.create_or_get(
            spec,
            snapshot_id=snapshot.document.snapshot_id,
            snapshot_created_at=snapshot.document.created_at,
            source_kind=snapshot.document.source_kind,
            source_identity=snapshot.document.source_identity or snapshot.document.snapshot_id,
        )
        register_export_base(outputs_root, record.export_id, store.base)
        if record.outputs_root != outputs_root:
            record.outputs_root = outputs_root
            store.update(record)
        if created:
            # Pin the document before the job is queued: a queued export must
            # render the snapshot it was requested from even if the source
            # archive is replaced (or evicted from the cache) before it starts.
            try:
                snapshot.persist(export_dir(store.base, record.export_id))
            except OSError as error:
                logger.warning(f'Cannot pin snapshot for export {record.export_id}: {error}')
            self._executor_for().submit(self._run, store, record.export_id, outputs_root)
        return record, created

    def get(self, export_id: str, *, outputs_root: str) -> ExportRecord:
        record, _store = self._locate(export_id, outputs_root=outputs_root)
        return record

    def cancel(self, export_id: str, *, outputs_root: str) -> ExportRecord:
        record, store = self._locate(export_id, outputs_root=outputs_root)
        updated = store.request_cancel(export_id)
        return updated or record

    def retry(self, export_id: str, *, outputs_root: str) -> Tuple[ExportRecord, bool]:
        record, _store = self._locate(export_id, outputs_root=outputs_root)
        return self.submit(record.spec, outputs_root=outputs_root)

    def download_path(
        self, export_id: str, *, outputs_root: str, name: Optional[str] = None
    ) -> Tuple[Path, ExportArtifact]:
        record, store = self._locate(export_id, outputs_root=outputs_root)
        if record.status != 'completed' or not record.artifacts:
            raise PerfReportError('This export has no downloadable artifact', 409)
        chosen = (
            record.artifacts[0]
            if name is None
            else next((artifact for artifact in record.artifacts if artifact.name == name), None)
        )
        if chosen is None:
            raise PerfReportError('Unknown artifact', 404)
        path = export_dir(store.base, export_id) / chosen.filename
        if not path.is_file():
            raise PerfReportError('The exported file is missing from disk', 410)
        return path, chosen

    def _locate(self, export_id: str, *, outputs_root: str) -> Tuple[ExportRecord, ExportStore]:
        """Find an existing export by id; a request never supplies a file path."""
        indexed = lookup_export_base(outputs_root, export_id)
        bases: List[Path] = ([indexed] if indexed is not None else []) + self._candidate_bases(outputs_root)
        for base in bases:
            if base is None:
                continue
            store = ExportStore(base)
            record = store.load(export_id)
            if record is not None:
                if base != indexed:
                    register_export_base(outputs_root, export_id, base)
                return record, store
        raise PerfReportError('Export not found', 404)

    def _candidate_bases(self, outputs_root: str) -> List[Path]:
        bases: List[Path] = []
        root = Path(outputs_root)
        if root.is_dir():
            bases.extend(sorted(path for path in root.glob('*/exports') if path.is_dir()))
            bases.extend(sorted(path for path in root.glob('*/perf/exports') if path.is_dir()))
        service_cache = root / '.perf_exports'
        if service_cache.is_dir():
            bases.extend(sorted(path for path in service_cache.glob('*') if path.is_dir()))
        return bases

    # -- the render itself ----------------------------------------------
    def _run(self, store: ExportStore, export_id: str, outputs_root: str) -> None:
        record = store.claim(export_id)
        if record is None:
            return
        spec = record.spec
        directory = export_dir(store.base, export_id)
        work = directory / WORK_DIR
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        try:
            if store.cancel_requested(export_id):
                raise ExportCancelled()
            pinned = directory / ReportSnapshot.PIN_NAME
            if record.snapshot_id and pinned.is_file():
                try:
                    snapshot = ReportSnapshot.restore(pinned)
                except (OSError, ValueError) as error:
                    raise PerfReportError(f'The pinned export snapshot is unreadable: {error}', 409) from None
                if snapshot.document.snapshot_id != record.snapshot_id:
                    raise PerfReportError('The pinned export snapshot does not match this export', 409)
            else:
                snapshot = load_report_snapshot(
                    outputs_root=spec.root_path or record.outputs_root or outputs_root,
                    task_id=spec.task_id,
                    root_path=spec.root_path,
                    rel_path=spec.path,
                )
                if record.snapshot_id and snapshot.document.snapshot_id != record.snapshot_id:
                    raise PerfReportError(
                        'The archive changed after this export was queued. Retry to export the current snapshot.', 409
                    )
            dataset = collect(snapshot, group=spec.group)
            store.update(record, progress='rendering')
            artifacts, warnings, request_count = self._render(store, record, snapshot, dataset, work)
            if store.cancel_requested(export_id):
                raise ExportCancelled()
            store.update(record, progress='publishing')
            published = self._publish(directory, work, artifacts)
            store.finish(
                export_id,
                status='completed',
                artifacts=published,
                warnings=warnings,
                run_count=len(dataset.runs),
                request_count=request_count,
            )
        except ExportCancelled:
            store.finish(export_id, status='cancelled', error='Cancelled before the export finished')
        except PerfReportError as error:
            store.finish(export_id, status='failed', error=error.message)
        except PdfUnavailable as error:
            store.finish(export_id, status='failed', error=str(error))
        except Exception as error:  # noqa: BLE001 - an export always ends in a record
            logger.error(f'Export {export_id} failed: {error}\n{traceback.format_exc()}')
            store.finish(export_id, status='failed', error=f'{type(error).__name__}: {error}')
        finally:
            shutil.rmtree(work, ignore_errors=True)
            (directory / CANCEL_NAME).unlink(missing_ok=True)

    def _render(
        self,
        store: ExportStore,
        record: ExportRecord,
        snapshot: ReportSnapshot,
        dataset: ExportDataset,
        work: Path,
    ) -> Tuple[List[ExportArtifact], List[str], int]:
        spec = record.spec
        snapshot_id = snapshot.document.snapshot_id
        locale = spec.locale
        kind = spec.kind
        staged: List[ExportArtifact] = []
        warnings: List[str] = []
        request_count = dataset.request_total

        if kind in ('html', 'pdf', 'bundle'):
            (work / 'report.html').write_text(render_html(dataset, spec, generated_at=_now()), encoding='utf-8')
            staged.append(_artifact('html', work / 'report.html', snapshot_id, locale))
            if kind in ('pdf', 'bundle'):
                try:
                    render_pdf(work / 'report.html', work / 'report.pdf', locale=locale)
                    staged.append(_artifact('pdf', work / 'report.pdf', snapshot_id, locale))
                except PdfUnavailable as error:
                    if kind == 'pdf':
                        raise
                    warnings.append(f'PDF skipped: {error}')

        if kind in ('csv', 'bundle'):
            write_summary_csv(work / 'summary.csv', dataset, preset=spec.preset)
            staged.append(_artifact('summary', work / 'summary.csv', snapshot_id, locale))
            # Node telemetry is a separate row set; it never flattens into the
            # per-run matrix and it is absent when the archive has no analysis.
            if any(run.nodes is not None and run.nodes.metrics for run in dataset.runs):
                write_node_metrics_csv(work / 'node_metrics.csv', dataset)
                staged.append(_artifact('node_metrics', work / 'node_metrics.csv', snapshot_id, locale))
            if any(run.nodes is not None and run.nodes.cache_ratios for run in dataset.runs):
                write_node_cache_csv(work / 'node_cache.csv', dataset)
                staged.append(_artifact('node_cache', work / 'node_cache.csv', snapshot_id, locale))

        if kind in ('requests', 'bundle'):

            def stop(_count: int) -> bool:
                return store.cancel_requested(record.export_id)

            write_requests_csv(
                work / 'requests.csv',
                snapshot,
                group=spec.group,
                status=spec.status,
                search=spec.search,
                run_key=spec.run_key,
                phase=spec.phase,
                should_stop=stop,
            )
            write_requests_jsonl(
                work / 'requests.jsonl',
                snapshot,
                group=spec.group,
                status=spec.status,
                search=spec.search,
                run_key=spec.run_key,
                phase=spec.phase,
                should_stop=stop,
            )
            staged.append(_artifact('requests_csv', work / 'requests.csv', snapshot_id, locale))
            staged.append(_artifact('requests_jsonl', work / 'requests.jsonl', snapshot_id, locale))

        if kind == 'bundle':
            manifest = {
                'export_id': record.export_id,
                'snapshot_id': snapshot_id,
                'generated_at': _now(),
                'template_version': EXPORT_TEMPLATE_VERSION,
                'statistics_version': dataset.header.statistics_version,
                'source_kind': dataset.header.source_kind,
                'locale': locale,
                'group': spec.group,
                'preset': spec.preset,
                'run_count': len(dataset.runs),
                'request_count': request_count,
                'warnings': warnings,
                'artifacts': [
                    {
                        'name': artifact.name,
                        'path': _INNER_NAMES[artifact.name],
                        'filename': artifact.filename,
                        'size': artifact.size,
                        'sha256': artifact.sha256,
                    }
                    for artifact in staged
                ],
            }
            (work / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            with zipfile.ZipFile(work / 'bundle.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for artifact in staged:
                    inner = _INNER_NAMES[artifact.name]
                    archive.write(work / inner, arcname=inner)
                archive.write(work / 'manifest.json', arcname='manifest.json')
            staged = [_artifact('bundle', work / 'bundle.zip', snapshot_id, locale)]

        if store.cancel_requested(record.export_id):
            raise ExportCancelled()
        return staged, warnings, request_count

    @staticmethod
    def _publish(directory: Path, work: Path, artifacts: List[ExportArtifact]) -> List[ExportArtifact]:
        """Move staged files into place atomically, then report what a reader may fetch."""
        published: List[ExportArtifact] = []
        for artifact in artifacts:
            source = work / _INNER_NAMES[artifact.name]
            target = directory / artifact.filename
            os.replace(source, target)
            published.append(artifact)
        return published


runner = ExportRunner()


def recover_exports(outputs_root: str) -> int:
    """Mark exports a previous process left queued or running as interrupted."""
    bases: List[Path] = []
    root = Path(outputs_root)
    if root.is_dir():
        bases.extend(path for path in root.glob('*/exports') if path.is_dir())
        bases.extend(path for path in root.glob('*/perf/exports') if path.is_dir())
        cache = root / '.perf_exports'
        if cache.is_dir():
            bases.extend(path for path in cache.glob('*') if path.is_dir())
    recovered = 0
    for base in bases:
        try:
            recovered += ExportStore(base).recover_interrupted()
        except OSError as error:
            logger.warning(f'Could not recover exports under {base}: {error}')
    return recovered
