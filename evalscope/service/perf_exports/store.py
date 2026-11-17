"""Filesystem layout and atomic persistence for report exports.

Every export owns ``<base>/<export_id>/``. Records are written through a
temporary file plus an atomic rename, so a reader never sees half a record and
a crashed writer leaves the previous state intact. A finished export publishes
its artifacts the same way, which keeps "completed" from ever pointing at a
partial file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from evalscope.utils.logger import get_logger

from ..utils import log as task_log
from .models import EXPORT_TEMPLATE_VERSION, ExportArtifact, ExportRecord, ExportSpec

logger = get_logger()

RECORD_NAME = 'export.json'
CANCEL_NAME = 'cancel.requested'
LOCK_NAME = '.lock'
LOCK_TIMEOUT_SECONDS = 60.0
LOCK_STALE_SECONDS = 120.0
EXPORT_ID_LENGTH = 20
INDEX_NAME = 'index.json'


def _now() -> str:
    return datetime.now().astimezone().isoformat()


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """A cross-process exclusive lock around a small state transition."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f'{os.getpid()}'.encode())
            os.close(descriptor)
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                age = 0.0
            if age > LOCK_STALE_SECONDS:
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise OSError('Timed out waiting for the export lock') from None
            time.sleep(0.05)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def canonical_spec(spec: ExportSpec, *, snapshot_id: str) -> str:
    payload = {
        'template': EXPORT_TEMPLATE_VERSION,
        'snapshot_id': snapshot_id,
        'spec': spec.model_dump(mode='json'),
    }
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def export_id_for_key(key: str) -> str:
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:EXPORT_ID_LENGTH]


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f'.write-probe-{os.getpid()}'
        probe.write_bytes(b'ok')
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def exports_base(root: Path, source_identity: str) -> Path:
    """Prefer the archive's own ``exports/`` directory, fall back to service storage.

    A shared or read-only archive must not receive writes: the fallback is keyed
    by the source identity so two archives with the same task name never share
    a directory.
    """
    preferred = root / 'exports'
    if _writable(preferred):
        return preferred
    identity = hashlib.sha256(source_identity.encode('utf-8')).hexdigest()[:16]
    fallback = Path(task_log.OUTPUT_DIR) / '.perf_exports' / identity
    if not _writable(fallback):
        raise OSError('No writable location for report exports')
    return fallback


def export_dir(base: Path, export_id: str) -> Path:
    if not export_id or any(char not in '0123456789abcdef' for char in export_id):
        raise ValueError('Invalid export id')
    return base / export_id


# -- lookup index ------------------------------------------------------
# Exports may live beside an archive anywhere on disk (a custom ``root_path``),
# while status and download requests carry only the export id. The index maps
# an id to the directory that owns its record; losing it degrades to scanning
# the outputs root, never to a wrong file.


def _index_path(outputs_root: str) -> Path:
    return Path(outputs_root) / '.perf_exports' / INDEX_NAME


def register_export_base(outputs_root: str, export_id: str, base: Path) -> None:
    path = _index_path(outputs_root)
    with _file_lock(path.parent / '.index.lock'):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if data.get(export_id) == str(base):
            return
        data[export_id] = str(base)
        _atomic_write(path, json.dumps(data, ensure_ascii=True, sort_keys=True).encode('utf-8'))


def lookup_export_base(outputs_root: str, export_id: str) -> Optional[Path]:
    try:
        data = json.loads(_index_path(outputs_root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    value = data.get(export_id) if isinstance(data, dict) else None
    if not isinstance(value, str):
        return None
    base = Path(value)
    return base if base.is_dir() else None


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp')
    with open(temporary, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    try:
        directory = os.open(path.parent, os.O_RDONLY)
    except OSError:  # pragma: no cover - directory fsync is unavailable on some platforms
        return
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class ExportStore:
    """Reads and writes export records under one base directory."""

    def __init__(self, base: Path):
        self.base = base

    # -- locking -------------------------------------------------------
    @contextmanager
    def lock(self, export_id: str) -> Iterator[None]:
        """A cross-process exclusive lock for one export's state transitions."""
        directory = export_dir(self.base, export_id)
        directory.mkdir(parents=True, exist_ok=True)
        with _file_lock(directory / LOCK_NAME):
            yield

    # -- records -------------------------------------------------------
    def load(self, export_id: str) -> Optional[ExportRecord]:
        path = export_dir(self.base, export_id) / RECORD_NAME
        try:
            return ExportRecord.model_validate_json(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            logger.warning(f'Cannot read export record {path}: {error}')
            return None

    def save(self, record: ExportRecord) -> None:
        directory = export_dir(self.base, record.export_id)
        directory.mkdir(parents=True, exist_ok=True)
        record.updated_at = _now()
        _atomic_write(directory / RECORD_NAME, record.model_dump_json().encode('utf-8'))

    def list(self) -> List[ExportRecord]:
        records: List[ExportRecord] = []
        try:
            entries = list(self.base.iterdir())
        except OSError:
            return records
        for entry in entries:
            if not entry.is_dir():
                continue
            record = self.load(entry.name)
            if record is not None:
                records.append(record)
        records.sort(key=lambda item: item.created_at, reverse=True)
        return records

    # -- lifecycle -----------------------------------------------------
    def create_or_get(
        self, spec: ExportSpec, *, snapshot_id: str, snapshot_created_at: str, source_kind: str, source_identity: str
    ) -> tuple[ExportRecord, bool]:
        key = canonical_spec(spec, snapshot_id=snapshot_id)
        export_id = export_id_for_key(key)
        with self.lock(export_id):
            existing = self.load(export_id)
            if existing is not None and existing.status in ('queued', 'running', 'completed'):
                return existing, False
            record = ExportRecord(
                export_id=export_id,
                spec_key=key,
                spec=spec,
                status='queued',
                created_at=_now(),
                queued_at=_now(),
                snapshot_id=snapshot_id,
                snapshot_created_at=snapshot_created_at,
                source_kind=source_kind,
                source_identity=source_identity,
                attempts=(existing.attempts if existing is not None else 0),
            )
            self.save(record)
            return record, True

    def claim(self, export_id: str) -> Optional[ExportRecord]:
        """Move a queued export to running; the caller that wins owns the render."""
        with self.lock(export_id):
            record = self.load(export_id)
            if record is None or record.status != 'queued':
                return None
            record.status = 'running'
            record.started_at = _now()
            record.progress = 'starting'
            record.attempts += 1
            record.error = None
            record.warnings = []
            record.artifacts = []
            self.save(record)
            return record

    def update(self, record: ExportRecord, *, progress: Optional[str] = None) -> ExportRecord:
        if progress is not None:
            record.progress = progress
        self.save(record)
        return record

    def finish(
        self,
        export_id: str,
        *,
        status: str,
        artifacts: Optional[List[ExportArtifact]] = None,
        error: Optional[str] = None,
        warnings: Optional[List[str]] = None,
        run_count: Optional[int] = None,
        request_count: Optional[int] = None,
    ) -> Optional[ExportRecord]:
        with self.lock(export_id):
            record = self.load(export_id)
            if record is None:
                return None
            record.status = status  # type: ignore[assignment]
            record.finished_at = _now()
            record.progress = None
            record.error = error
            record.artifacts = artifacts or []
            if warnings:
                record.warnings = warnings
            if run_count is not None:
                record.run_count = run_count
            if request_count is not None:
                record.request_count = request_count
            self.save(record)
            return record

    def request_cancel(self, export_id: str) -> Optional[ExportRecord]:
        with self.lock(export_id):
            record = self.load(export_id)
            if record is None:
                return None
            if record.status == 'queued':
                record.status = 'cancelled'
                record.finished_at = _now()
                record.error = 'Cancelled before it started'
                self.save(record)
                return record
            if record.status == 'running':
                (export_dir(self.base, export_id) / CANCEL_NAME).write_text('1', encoding='utf-8')
            return record

    def cancel_requested(self, export_id: str) -> bool:
        return (export_dir(self.base, export_id) / CANCEL_NAME).exists()

    def clear_artifacts(self, export_id: str) -> None:
        directory = export_dir(self.base, export_id)
        for entry in directory.iterdir():
            if entry.name in (RECORD_NAME, LOCK_NAME):
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def recover_interrupted(self) -> int:
        """Mark exports a previous process left queued or running as interrupted.

        Their partial files are removed: only a completed record may expose
        artifacts, so a restart can never publish half a PDF.
        """
        recovered = 0
        for record in self.list():
            if record.status not in ('queued', 'running'):
                continue
            self.clear_artifacts(record.export_id)
            updated = self.finish(
                record.export_id,
                status='interrupted',
                error='The service restarted before this export finished. Retry to render it again.',
            )
            if updated is not None:
                recovered += 1
        return recovered
