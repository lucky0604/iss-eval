"""Rebuildable persistent index of every task ever discovered under the output root.

The index is a read model: task execution never depends on it. Every write is
best-effort and a broken or unreadable index degrades the Task Center to
"index unavailable" instead of failing a task. The index can always be rebuilt
from the per-task ``task_info.json`` manifests and the directory layout.

The queue keeps authority over live state: list queries merge the persisted
index with the queue/subprocess registry by ``task_id``, so a queued or running
task is never shown from a stale persisted row, and a Redis-backed queue still
reports work after the web process restarts.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from evalscope.utils.logger import get_logger
from evalscope.utils.uri_utils import sanitize_endpoint

logger = get_logger()

INDEX_DIRNAME = '.evalscope'
INDEX_FILENAME = 'task-history.sqlite3'

# Bound the first-boot discovery pass so a huge legacy outputs tree cannot stall
# the first Task Center request. The reported completeness says so explicitly.
MAX_BACKFILL_DIRS = int(os.getenv('EVALSCOPE_TASK_INDEX_BACKFILL_LIMIT', '2000'))
MAX_ERROR_LENGTH = 500
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

RAW_STATUSES = {
    'queued',
    'preparing',
    'running',
    'cancelling',
    'finalizing',
    'completed',
    'stopped',
    'error',
    'unknown',
}
TERMINAL_STATUSES = {'completed', 'stopped', 'error'}
QUALITY_LEVELS = {'complete', 'partial', 'invalid'}
ENGINES = {'evalscope', 'aiperf'}
KINDS = {'eval', 'perf'}

STATUS_GROUP = {
    'queued': 'queued',
    'preparing': 'executing',
    'running': 'executing',
    'cancelling': 'executing',
    'finalizing': 'executing',
    'completed': 'completed',
    'stopped': 'stopped',
    'error': 'failed',
    'unknown': 'uncertain',
}

_ACTIVE_VIEW = {'executing', 'queued'}
_ATTENTION_VIEW = {'failed', 'uncertain'}
_HISTORY_VIEW = {'completed', 'stopped', 'failed'}

_WRITE_LOCK = threading.Lock()
_SCHEMA_READY: set[str] = set()
_DISABLED_ROOTS: set[str] = set()


# ---------------------------------------------------------------------------
# Small pure helpers (shared by list, summary and detail projections)
# ---------------------------------------------------------------------------


def status_group(raw: str) -> str:
    """Map a raw lifecycle status to its product status group."""
    return STATUS_GROUP.get(raw, 'uncertain')


def clamp_percent(value: Any) -> Optional[float]:
    """Return a finite 0-100 percentage or ``None``; never fabricate one."""
    if type(value) not in (int, float):
        return None
    number = float(value)
    if number != number or number in (float('inf'), float('-inf')):
        return None
    return max(0.0, min(100.0, number))


def parse_time(value: Any) -> Tuple[Optional[datetime], str]:
    """Parse an ISO timestamp into an aware datetime.

    Legacy manifests were written with ``datetime.now().isoformat()``, i.e.
    without an offset. Those are interpreted in the service's own configured
    timezone (the process timezone) and reported as ``legacy`` rather than
    silently guessed from the browser. Unparseable values return
    ``(None, 'unknown')``.
    """
    if not isinstance(value, str) or not value.strip():
        return None, 'unknown'
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, 'unknown'
    if parsed.tzinfo is None:
        return parsed.astimezone(), 'legacy'
    return parsed, 'aware'


def sanitize_error(value: Any) -> Optional[str]:
    """Keep an error summary display-safe: no credentials, bounded length."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = re.sub(r'(?i)\b(authorization|api[-_]?key|token)\b\s*[:=]\s*(?:bearer\s+)?[^\s,;]+', r'\1=<redacted>', value)
    text = re.sub(r'(?i)https?://[^\s/@]+:[^\s/@]+@', 'https://<redacted>@', text)
    text = text.replace('\x00', '')
    return text[:MAX_ERROR_LENGTH]


def effective_time(record: Dict[str, Any]) -> Tuple[Optional[datetime], str]:
    """The time a list row sorts and filters on: finished, else started, else enqueued."""
    for key in ('finished_at', 'started_at', 'enqueued_at'):
        parsed, basis = parse_time(record.get(key))
        if parsed is not None:
            return parsed, basis
    return None, 'unknown'


def classify_test_type(
    kind: str, engine: Optional[str], config: Optional[Dict[str, Any]], run_dir: Optional[str]
) -> Optional[str]:
    """Classify a performance task from its archive markers, not from its engine label.

    ``engine == 'aiperf'`` covers both single runs and cache experiments, so the
    engine alone can never decide the result shape.
    """
    if kind != 'perf':
        return None
    if run_dir:
        perf_dir = os.path.join(run_dir, 'perf')
        if os.path.isfile(os.path.join(perf_dir, 'experiment_plan.json')):
            return 'cache_experiment'
        if os.path.isfile(os.path.join(perf_dir, 'single_run.json')):
            return 'single'
    if isinstance(config, dict):
        for key in ('parallel', 'rate', 'number'):
            value = config.get(key)
            if isinstance(value, list):
                return 'sweep' if len(value) > 1 else 'single'
        if any(isinstance(config.get(key), (int, float)) for key in ('parallel', 'rate')):
            return 'single'
    return 'single' if engine == 'evalscope' else 'unknown'


# ---------------------------------------------------------------------------
# SQLite plumbing
# ---------------------------------------------------------------------------


def _output_dir() -> str:
    from .utils import log

    return log.OUTPUT_DIR


def _db_path() -> str:
    return os.path.join(_output_dir(), INDEX_DIRNAME, INDEX_FILENAME)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA synchronous=NORMAL')
    if path not in _SCHEMA_READY:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_index (
                task_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'unknown',
                engine TEXT,
                test_type TEXT,
                model TEXT NOT NULL DEFAULT '',
                alias TEXT,
                endpoint TEXT,
                status TEXT NOT NULL DEFAULT 'unknown',
                quality TEXT,
                completed_subruns INTEGER,
                total_subruns INTEGER,
                started_at TEXT,
                enqueued_at TEXT,
                finished_at TEXT,
                error TEXT,
                config_json TEXT,
                has_perf_dir INTEGER NOT NULL DEFAULT 0,
                has_reports_dir INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS index_meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        _SCHEMA_READY.add(path)
    return connection


def _meta_get(connection: sqlite3.Connection, key: str, default: str = '') -> str:
    row = connection.execute('SELECT value FROM index_meta WHERE key = ?', (key,)).fetchone()
    return str(row['value']) if row is not None else default


def _meta_set(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        'INSERT INTO index_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (key, value),
    )


def _bump_revision(connection: sqlite3.Connection) -> None:
    current = _meta_get(connection, 'revision', '0')
    try:
        value = int(current) + 1
    except ValueError:
        value = 1
    _meta_set(connection, 'revision', str(value))


def revision() -> int:
    try:
        with _connect() as connection:
            return int(_meta_get(connection, 'revision', '0'))
    except (OSError, sqlite3.Error, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _normalize_manifest(task_id: str, manifest: Dict[str, Any], run_dir: Optional[str] = None) -> Dict[str, Any]:
    kind = manifest.get('kind')
    if kind not in KINDS:
        kind = 'perf' if run_dir and os.path.isdir(os.path.join(run_dir, 'perf')) else 'eval'
    engine = manifest.get('engine')
    if engine not in ENGINES:
        engine = None
    status = manifest.get('status')
    if status not in RAW_STATUSES:
        status = 'unknown'
    quality = manifest.get('quality')
    if quality not in QUALITY_LEVELS:
        quality = None
    config = manifest.get('config')
    config_json = None
    if isinstance(config, dict):
        config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)[:65536]
    has_perf = bool(run_dir and os.path.isdir(os.path.join(run_dir, 'perf')))
    has_reports = bool(run_dir and os.path.isdir(os.path.join(run_dir, 'reports')))
    declared_type = manifest.get('test_type')
    test_type = (
        declared_type
        if declared_type in ('single', 'sweep', 'cache_experiment', 'unknown')
        else classify_test_type(kind, engine, config if isinstance(config, dict) else None, run_dir)
    )
    return {
        'task_id': task_id,
        'kind': kind,
        'engine': engine,
        'test_type': test_type,
        'model': str(manifest.get('model') or ''),
        'alias': manifest.get('alias') if isinstance(manifest.get('alias'), str) else None,
        'endpoint': sanitize_endpoint(manifest.get('endpoint') or manifest.get('api_url')),
        'status': status,
        'quality': quality,
        'completed_subruns': manifest.get('completed_subruns')
        if type(manifest.get('completed_subruns')) is int
        else None,
        'total_subruns': manifest.get('total_subruns') if type(manifest.get('total_subruns')) is int else None,
        'started_at': manifest.get('started_at') if isinstance(manifest.get('started_at'), str) else None,
        'enqueued_at': manifest.get('enqueued_at') if isinstance(manifest.get('enqueued_at'), str) else None,
        'finished_at': manifest.get('finished_at') if isinstance(manifest.get('finished_at'), str) else None,
        'error': sanitize_error(manifest.get('error')),
        'config_json': config_json,
        'has_perf_dir': 1 if has_perf else 0,
        'has_reports_dir': 1 if has_reports else 0,
        'updated_at': datetime.now().astimezone().isoformat(),
    }


def record_manifest(task_id: str, manifest: Dict[str, Any], run_dir: Optional[str] = None) -> bool:
    """Upsert one task into the index. Returns ``False`` when the index is unavailable.

    Never raises: an index failure must not turn a successful task submission or
    completion into an error.
    """
    root = _output_dir()
    if root in _DISABLED_ROOTS:
        return False
    try:
        row = _normalize_manifest(task_id, manifest, run_dir or os.path.join(root, task_id))
        with _WRITE_LOCK, _connect() as connection:
            connection.execute(
                """
                INSERT INTO task_index (
                    task_id, kind, engine, test_type, model, alias, endpoint, status, quality,
                    completed_subruns, total_subruns, started_at, enqueued_at, finished_at,
                    error, config_json, has_perf_dir, has_reports_dir, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    kind = excluded.kind,
                    engine = excluded.engine,
                    test_type = excluded.test_type,
                    model = excluded.model,
                    alias = excluded.alias,
                    endpoint = excluded.endpoint,
                    status = excluded.status,
                    quality = excluded.quality,
                    completed_subruns = excluded.completed_subruns,
                    total_subruns = excluded.total_subruns,
                    started_at = excluded.started_at,
                    enqueued_at = excluded.enqueued_at,
                    finished_at = excluded.finished_at,
                    error = excluded.error,
                    config_json = excluded.config_json,
                    has_perf_dir = excluded.has_perf_dir,
                    has_reports_dir = excluded.has_reports_dir,
                    updated_at = excluded.updated_at
                """,
                (
                    row['task_id'],
                    row['kind'],
                    row['engine'],
                    row['test_type'],
                    row['model'],
                    row['alias'],
                    row['endpoint'],
                    row['status'],
                    row['quality'],
                    row['completed_subruns'],
                    row['total_subruns'],
                    row['started_at'],
                    row['enqueued_at'],
                    row['finished_at'],
                    row['error'],
                    row['config_json'],
                    row['has_perf_dir'],
                    row['has_reports_dir'],
                    row['updated_at'],
                ),
            )
            _bump_revision(connection)
        return True
    except Exception as error:  # noqa: BLE001 - writes are best-effort by contract
        _note_index_error(root, error)
        return False


def _note_index_error(root: str, error: Exception) -> None:
    """Disable the index for a root only when retrying would keep failing."""
    locked = isinstance(error, sqlite3.OperationalError) and 'locked' in str(error).lower()
    if not locked:
        _DISABLED_ROOTS.add(root)
    logger.warning(f'Task history index unavailable under {root}: {error}')


def ensure_backfill(force: bool = False) -> Dict[str, Any]:
    """Discover task directories not yet indexed; returns coverage information.

    The pass is budgeted. ``complete`` is false when more directories exist than
    the budget or when the index itself is unavailable, and callers must report
    that instead of presenting the discovered subset as the whole history.
    """
    root = _output_dir()
    state: Dict[str, Any] = {'state': 'ready', 'scanned': 0, 'complete': False, 'reason': None, 'known_total': 0}
    if root in _DISABLED_ROOTS:
        return {**state, 'state': 'unavailable', 'reason': 'index_unavailable'}
    try:
        with _connect() as connection:
            indexed = int(_meta_get(connection, 'backfill_scanned', '0'))
            complete = _meta_get(connection, 'backfill_complete', '0') == '1'
            if complete and not force:
                rows = connection.execute('SELECT COUNT(*) AS total FROM task_index').fetchone()
                return {**state, 'scanned': indexed, 'complete': True, 'known_total': int(rows['total'])}
            names: List[str] = []
            with os.scandir(root) as entries:
                for entry in entries:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if entry.name == INDEX_DIRNAME or entry.name.startswith('.'):
                        continue
                    names.append(entry.name)
            names.sort(reverse=True)
            scanned = 0
            for name in names[:MAX_BACKFILL_DIRS]:
                run_dir = os.path.join(root, name)
                from .utils import log

                manifest = log.read_task_manifest(run_dir)
                if manifest is None:
                    has_perf = os.path.isdir(os.path.join(run_dir, 'perf'))
                    has_reports = os.path.isdir(os.path.join(run_dir, 'reports'))
                    if not has_perf and not has_reports:
                        continue
                    manifest = {'kind': 'perf' if has_perf else 'eval'}
                record_manifest(name, manifest, run_dir=run_dir)
                scanned += 1
            complete = len(names) <= MAX_BACKFILL_DIRS
            with _connect() as connection:
                _meta_set(connection, 'backfill_scanned', str(scanned))
                _meta_set(connection, 'backfill_complete', '1' if complete else '0')
                rows = connection.execute('SELECT COUNT(*) AS total FROM task_index').fetchone()
            return {
                'state': 'ready',
                'scanned': scanned,
                'complete': complete,
                'reason': None if complete else 'backfill_budget_exceeded',
                'known_total': int(rows['total']),
            }
    except (OSError, sqlite3.Error, ValueError, TypeError) as error:
        _note_index_error(root, error)
        return {**state, 'state': 'unavailable', 'reason': type(error).__name__}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _row_to_record(row: sqlite3.Row) -> Dict[str, Any]:
    record = dict(row)
    config = record.pop('config_json', None)
    if config:
        try:
            parsed = json.loads(config)
        except ValueError:
            parsed = None
        record['config'] = parsed if isinstance(parsed, dict) else None
    else:
        record['config'] = None
    record['has_perf_dir'] = bool(record.get('has_perf_dir'))
    record['has_reports_dir'] = bool(record.get('has_reports_dir'))
    return record


def _active_records() -> List[Dict[str, Any]]:
    """Live queue + subprocess records, overlaid on the persisted index."""
    from .utils import get_task_queue, list_active_tasks

    records: Dict[str, Dict[str, Any]] = {}
    try:
        for record in get_task_queue().list():
            records[record['task_id']] = dict(record)
    except Exception as error:  # noqa: BLE001 - queue backend outage must not hide history
        logger.warning(f'Queue listing failed while reading task history: {error}')
    try:
        for record in list_active_tasks():
            records[record['task_id']] = {**records.get(record['task_id'], {}), **record}
    except Exception as error:  # noqa: BLE001
        logger.warning(f'Active-task listing failed while reading task history: {error}')
    return list(records.values())


def _demote_stale(record: Dict[str, Any], live: bool) -> None:
    """A persisted transient status with no live owner is uncertain, not running.

    Showing a crashed or restarted task as permanently "running" is exactly the
    failure mode the Task Center has to avoid: its status is unknown, not zero
    and not successful.
    """
    if live:
        return
    status = record.get('status')
    if status in ('queued', 'preparing', 'running', 'cancelling', 'finalizing'):
        record['last_known_status'] = status
        record['status'] = 'unknown'
        record['queue_position'] = None


def collect_records() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Merge persisted rows with live records (live wins per task_id)."""
    ensure = ensure_backfill()
    rows: List[Dict[str, Any]] = []
    try:
        with _connect() as connection:
            rows = [_row_to_record(row) for row in connection.execute('SELECT * FROM task_index')]
            current_revision = int(_meta_get(connection, 'revision', '0'))
    except (OSError, sqlite3.Error):
        current_revision = 0
    merged: Dict[str, Dict[str, Any]] = {row['task_id']: row for row in rows}
    live_ids: set[str] = set()
    for live in _active_records():
        live_ids.add(live['task_id'])
        previous = merged.get(live['task_id'])
        record = {**(previous or {}), **live}
        record.setdefault('kind', 'eval')
        record.setdefault('model', '')
        merged[live['task_id']] = record
    for task_id, record in merged.items():
        _demote_stale(record, task_id in live_ids)
    return list(merged.values()), {
        'indexing': {'state': ensure['state'], 'scanned': ensure['scanned'], 'reason': ensure['reason']},
        'completeness': 'complete' if ensure['complete'] and ensure['state'] == 'ready' else 'partial',
        'revision': current_revision,
        'known_total': len(merged),
    }


def _sort_key_active(record: Dict[str, Any]) -> Tuple[int, int, float, str]:
    group = status_group(str(record.get('status') or 'unknown'))
    if group == 'executing':
        bucket = 0
    elif group == 'queued':
        bucket = 1
    else:
        bucket = 2
    position = record.get('queue_position')
    queue_slot = position if type(position) is int else 10**6
    parsed, _ = effective_time(record)
    stamp = -parsed.timestamp() if parsed is not None else 0.0
    return bucket, queue_slot, stamp, record['task_id']


def _sort_key_recent(record: Dict[str, Any]) -> Tuple[float, str]:
    parsed, _ = effective_time(record)
    # Descending: newest first. Records without a usable time sort last.
    return -(parsed.timestamp()) if parsed is not None else float('inf'), record['task_id']


def _parse_bound(value: Optional[str], name: str) -> Optional[datetime]:
    if value is None or value == '':
        return None
    parsed, basis = parse_time(value)
    if parsed is None or basis == 'unknown':
        raise ValueError(f'Invalid {name}')
    return parsed


def query_records(
    *,
    view: str = 'all',
    kind: Optional[str] = None,
    engine: Optional[str] = None,
    statuses: Iterable[str] = (),
    query: str = '',
    start: Optional[str] = None,
    end: Optional[str] = None,
    sort: str = 'recent',
) -> Dict[str, Any]:
    """Filter the merged task set. Pagination is applied by the caller."""
    if view not in ('active', 'attention', 'history', 'all'):
        raise ValueError('Invalid view')
    if kind not in (None, 'eval', 'perf'):
        raise ValueError('Invalid kind')
    if engine not in (None, 'evalscope', 'aiperf'):
        raise ValueError('Invalid engine')
    if sort not in ('recent', 'queue'):
        raise ValueError('Invalid sort')
    status_filter = {value for value in statuses if value}
    if any(value not in RAW_STATUSES for value in status_filter):
        raise ValueError('Invalid status')
    from_time = _parse_bound(start, 'from')
    to_time = _parse_bound(end, 'to')

    records, meta = collect_records()
    filtered: List[Dict[str, Any]] = []
    excluded_without_time = 0
    q = query.strip().lower()

    for record in records:
        status = str(record.get('status') or 'unknown')
        group = status_group(status)
        if view == 'active' and group not in _ACTIVE_VIEW:
            continue
        if view == 'attention' and group not in _ATTENTION_VIEW:
            continue
        if view == 'history' and group not in _HISTORY_VIEW:
            continue
        if kind and record.get('kind') != kind:
            continue
        live_engine = record.get('engine')
        if engine and live_engine != engine:
            continue
        if status_filter and status not in status_filter:
            continue
        if q:
            haystack = ' '.join(
                str(record.get(key) or '') for key in ('task_id', 'model', 'alias', 'endpoint', 'dataset', 'api')
            ).lower()
            if q not in haystack:
                continue
        if from_time is not None or to_time is not None:
            parsed, _ = effective_time(record)
            if parsed is None:
                excluded_without_time += 1
                continue
            if from_time is not None and parsed < from_time:
                continue
            if to_time is not None and parsed >= to_time:
                continue
        filtered.append(record)

    if sort == 'queue' and view == 'active':
        filtered.sort(key=_sort_key_active)
    else:
        filtered.sort(key=_sort_key_recent)
    return {
        'records': filtered,
        'excluded_without_time': excluded_without_time,
        'completeness': meta['completeness'],
        'revision': meta['revision'],
        'known_total': meta['known_total'],
        'indexing': meta['indexing'],
    }


def summarize(*, start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    """Global counts; ``completed_in_range`` uses finished_at only."""
    from_time = _parse_bound(start, 'from')
    to_time = _parse_bound(end, 'to')
    records, meta = collect_records()
    executing = queued = attention = completed_in_range = unknown_time = excluded = 0
    for record in records:
        status = str(record.get('status') or 'unknown')
        group = status_group(status)
        if group == 'executing':
            executing += 1
        elif group == 'queued':
            queued += 1
        if group in _ATTENTION_VIEW:
            attention += 1
        if status == 'completed':
            if from_time is None and to_time is None:
                completed_in_range += 1
            else:
                parsed, basis = parse_time(record.get('finished_at'))
                if parsed is None:
                    unknown_time += 1
                elif (from_time is None or parsed >= from_time) and (to_time is None or parsed < to_time):
                    completed_in_range += 1
        elif from_time is not None or to_time is not None:
            parsed, basis = effective_time(record)
            if parsed is None:
                excluded += 1
    from .utils import queue_health

    try:
        health = queue_health()
        queue = {'backend': str(health.get('backend', 'unknown')), 'reachable': True, 'reason': None}
    except Exception as error:  # noqa: BLE001 - connectivity is reported, not fatal
        from .utils.queue_config import queue_backend

        queue = {'backend': queue_backend(), 'reachable': False, 'reason': type(error).__name__}
    return {
        'executing': executing,
        'queued': queued,
        'attention': attention,
        'completed_in_range': completed_in_range,
        'observed_at': datetime.now().astimezone().isoformat(),
        'scope': 'global+range' if (from_time or to_time) else 'global',
        'completeness': meta['completeness'],
        'revision': meta['revision'],
        'queue': queue,
        'unknown_timestamp_count': unknown_time,
        'excluded_from_range': excluded,
    }


def find_record(task_id: str) -> Optional[Dict[str, Any]]:
    """Look up one task: live state first, then the persisted index."""
    for record in _active_records():
        if record['task_id'] == task_id:
            persisted, _ = _persisted_record(task_id)
            return {**(persisted or {}), **record}
    persisted, _ = _persisted_record(task_id)
    if persisted is None:
        ensure_backfill()
        persisted, _ = _persisted_record(task_id)
    if persisted is None:
        persisted = _record_from_disk(task_id)
        if persisted is None:
            return None
    _demote_stale(persisted, False)
    return persisted


def _record_from_disk(task_id: str) -> Optional[Dict[str, Any]]:
    """Direct-manifest fallback for tasks the index has not discovered yet."""
    from .utils import log

    root = _output_dir()
    if not task_id or os.path.basename(task_id) != task_id or task_id in ('.', '..'):
        return None
    run_dir = os.path.join(root, task_id)
    if not os.path.isdir(run_dir):
        return None
    manifest = log.read_task_manifest(run_dir)
    if manifest is None:
        has_perf = os.path.isdir(os.path.join(run_dir, 'perf'))
        if not has_perf and not os.path.isdir(os.path.join(run_dir, 'reports')):
            return None
        manifest = {'kind': 'perf' if has_perf else 'eval'}
    record_manifest(task_id, manifest, run_dir=run_dir)
    return _normalize_manifest(task_id, manifest, run_dir)


def _persisted_record(task_id: str) -> Tuple[Optional[Dict[str, Any]], int]:
    try:
        with _connect() as connection:
            row = connection.execute('SELECT * FROM task_index WHERE task_id = ?', (task_id,)).fetchone()
            current = int(_meta_get(connection, 'revision', '0'))
        return (_row_to_record(row) if row is not None else None), current
    except (OSError, sqlite3.Error):
        return None, 0
