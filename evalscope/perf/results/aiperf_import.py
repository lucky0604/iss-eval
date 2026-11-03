"""Offline AIPerf 0.13.0 import with immutable, paginated generations."""

import errno
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from urllib.parse import quote, urlsplit

from evalscope.perf.executors.external import Cancellation
from evalscope.perf.executors.urls import normalize_metrics_url
from evalscope.perf.results.models import (
    AIPerfImportArguments,
    CacheObservation,
    CacheRequestGroup,
    CacheRequestGroups,
    ImportManifest,
    MetricStatistics,
    MetricTargetReference,
    NodeMetricSample,
    PerfRequestRecord,
    PerfRunResult,
    RequestLatencyDistribution,
    RequestObservationBounds,
)
from evalscope.utils.uri_utils import sanitize_endpoint

_PARSER = 'aiperf-0.13.0-v4'
_FILES = (
    'profile_export_aiperf.json',
    'profile_export.jsonl',
    'server_metrics_export.json',
    'server_metrics_export.jsonl',
    'phase_manifest.json',
    'metrics_target_map.json',
)
_METRICS = {
    'request_latency': ('seconds', 'ms', 0.001),
    'time_to_first_token': ('seconds', 'ms', 0.001),
    'inter_token_latency': ('seconds', 'ms', 0.001),
    'request_throughput': ('requests/sec', 'requests/sec', 1),
    'input_token_throughput': ('tokens/sec', 'tokens/sec', 1),
    'output_token_throughput': ('tokens/sec', 'tokens/sec', 1),
    'total_token_throughput': ('tokens/sec', 'tokens/sec', 1),
    'request_error_rate': ('ratio', '%', 0.01),
    'input_sequence_length': ('tokens', 'tokens', 1),
    'output_sequence_length': ('tokens', 'tokens', 1),
}


class ImportCancelled(Exception):
    """An import stopped without publishing an incomplete generation."""


def _files(raw: Path) -> Dict[str, Path]:
    result = {}
    for name in _FILES:
        path = raw / name
        if path.exists():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(raw) or not resolved.is_file():
                raise ValueError('Raw artifact path escapes the run directory')
            result[name] = resolved
    if 'profile_export.jsonl' not in result:
        raise ValueError('AIPerf request records are missing')
    return result


def _hash_files(
    files: Dict[str, Path], allow_partial_tail: bool, target_identity: str, cancel_event: Optional[Cancellation] = None
) -> tuple[str, Dict[str, str]]:
    hashes = {}
    combined = hashlib.sha256(f'{_PARSER}:partial={allow_partial_tail}:{target_identity}'.encode())
    for name, path in sorted(files.items()):
        digest = hashlib.sha256()
        with path.open('rb') as file:
            while chunk := file.read(1024 * 1024):
                if cancel_event is not None and cancel_event.is_set():
                    raise ImportCancelled()
                digest.update(chunk)
        hashes[name] = digest.hexdigest()
        combined.update(name.encode() + b'\0' + digest.digest())
    return combined.hexdigest(), hashes


def _json(path: Path) -> dict:
    try:
        with path.open(encoding='utf-8') as file:
            value = json.load(file)
    except (ValueError, UnicodeError):
        raise ValueError(f'Invalid JSON artifact: {path.name}') from None
    if not isinstance(value, dict):
        raise ValueError(f'Invalid JSON object: {path.name}')
    return value


def _owned_file(directory: Path, name: str) -> Path:
    path = (directory / name).resolve(strict=True)
    if not path.is_relative_to(directory) or not path.is_file():
        raise ValueError('Result file escapes its generation')
    return path


def _number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid numeric value in AIPerf artifact')
    return float(value)


def _metric(metrics: dict, key: str, unit: str, scale: float = 1) -> Optional[float]:
    field = metrics.get(key)
    if field is None:
        return None
    if not isinstance(field, dict) or field.get('unit') != unit:
        raise ValueError(f'Unsupported unit or structure for {key}')
    number = _number(field.get('value'))
    if number is not None and number < 0:
        raise ValueError(f'Negative metric value for {key}')
    return None if number is None else number * scale


def _integer(value: Optional[float], name: str) -> Optional[int]:
    if value is None:
        return None
    if value < 0 or not value.is_integer():
        raise ValueError(f'Invalid integer for {name}')
    return int(value)


def _record(raw: dict, index: int) -> PerfRequestRecord:
    metadata, metrics = raw.get('metadata'), raw.get('metrics')
    if not isinstance(metadata, dict) or not isinstance(metrics, dict):
        raise ValueError('Request record lacks metadata or metrics')
    phase = metadata.get('phase_kind')
    if phase not in ('warmup', 'profiling'):
        raise ValueError('Request record has unknown phase')
    error = raw.get('error')
    if error is not None and not isinstance(error, dict):
        raise ValueError('Invalid request error')
    if metadata.get('was_cancelled') is not None and type(metadata['was_cancelled']) is not bool:
        raise ValueError('Invalid request cancellation status')
    if metadata.get('context_overflow_skip') is not None and type(metadata['context_overflow_skip']) is not bool:
        raise ValueError('Invalid context overflow status')
    cancelled = metadata.get('was_cancelled')
    status_reason = None
    prompt = _metric(metrics, 'usage_prompt_tokens', 'tokens')
    prompt_source = 'server_usage' if prompt is not None else None
    if prompt is None:
        prompt = _metric(metrics, 'input_sequence_length', 'tokens')
        prompt_source = 'aiperf_estimate' if prompt is not None else None
    output = _metric(metrics, 'usage_completion_tokens', 'tokens')
    output_source = 'server_usage' if output is not None else None
    if output is None:
        output = _metric(metrics, 'output_sequence_length', 'tokens')
        output_source = 'aiperf_estimate' if output is not None else None
    cached = _metric(metrics, 'usage_prompt_cache_read_tokens', 'tokens')
    error_code = error.get('code') if error else None
    if error_code is not None and (type(error_code) is not int or error_code < 0):
        error_code = None
    for key in ('request_start_ns', 'request_end_ns', 'turn_index'):
        value = metadata.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f'Invalid request metadata: {key}')
    start, end = metadata.get('request_start_ns'), metadata.get('request_end_ns')
    if cancelled is True:
        status = 'cancelled'
    elif error is not None:
        status = 'error'
    elif metadata.get('context_overflow_skip') is True:
        status, status_reason = 'incomplete', 'context_overflow_skip'
    elif cancelled is None:
        status, status_reason = 'incomplete', 'cancellation_status_missing'
    elif start is None or end is None:
        status, status_reason = 'incomplete', 'request_timestamps_missing'
    elif end < start:
        status, status_reason = 'incomplete', 'request_time_range_invalid'
    else:
        status = 'success'
    return PerfRequestRecord(
        index=index,
        request_id=metadata.get('x_request_id'),
        session_id=metadata.get('conversation_id'),
        turn_index=metadata.get('turn_index'),
        phase=phase,
        status=status,
        status_reason=status_reason,
        start_ns=metadata.get('request_start_ns'),
        end_ns=metadata.get('request_end_ns'),
        latency_seconds=_metric(metrics, 'request_latency', 'ms', 0.001),
        ttft_seconds=_metric(metrics, 'time_to_first_token', 'ms', 0.001),
        prompt_tokens=_integer(prompt, 'prompt_tokens'),
        prompt_tokens_source=prompt_source,
        output_tokens=_integer(output, 'output_tokens'),
        output_tokens_source=output_source,
        cached_tokens=_integer(cached, 'cached_tokens'),
        cached_tokens_source='server_usage' if cached is not None else None,
        error_code=error_code,
    )


@dataclass
class _ImportState:
    partial_tail: bool = False


def _records(path: Path, state: _ImportState, allow_partial_tail: bool) -> Iterator[PerfRequestRecord]:
    with path.open('rb') as file:
        index = 0
        while line := file.readline(16 * 1024 * 1024 + 1):
            if len(line) > 16 * 1024 * 1024:
                raise ValueError(f'Request record exceeds size limit at line {index + 1}')
            try:
                raw = json.loads(line.decode('utf-8'))
            except (ValueError, UnicodeError):
                if allow_partial_tail and not line.endswith(b'\n') and file.read(1) == b'':
                    state.partial_tail = True
                    break
                raise ValueError(f'Invalid request record at line {index + 1}') from None
            if not isinstance(raw, dict):
                raise ValueError(f'Invalid request record at line {index + 1}')
            yield _record(raw, index)
            index += 1


def _targets(arguments: AIPerfImportArguments, files: Dict[str, Path]) -> tuple[Dict[str, MetricTargetReference], str]:
    mapping = {}
    if 'metrics_target_map.json' in files:
        data = _json(files['metrics_target_map.json'])
        if data.get('schema_version') != '1.0' or not isinstance(data.get('targets'), dict):
            raise ValueError('Invalid metrics relay mapping')
        mapping = data['targets']
        if set(mapping) - {target.node_id for target in arguments.targets}:
            raise ValueError('Unknown metrics relay target')
    targets = {}
    ids = set()
    original_urls = set()
    for target in arguments.targets:
        url = normalize_metrics_url(target.url)
        if url in original_urls:
            raise ValueError('Metrics endpoints and node IDs must be unique')
        original_urls.add(url)
        if target.node_id in mapping:
            item = mapping[target.node_id]
            if (
                not isinstance(item, dict)
                or item.get('endpoint_url') != url
                or not isinstance(item.get('collector_url'), str)
            ):
                raise ValueError('Invalid metrics relay attribution')
            relay = normalize_metrics_url(item['collector_url'])
            parsed = urlsplit(relay)
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.port is None:
                raise ValueError('Invalid metrics relay endpoint')
            url = relay
        if url in targets or target.node_id in ids:
            raise ValueError('Metrics endpoints and node IDs must be unique')
        targets[url] = target
        ids.add(target.node_id)
    data = {
        'required': arguments.metrics_required,
        'targets': [targets[key].model_dump(mode='json') for key in sorted(targets)],
    }
    identity = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return targets, identity


def _node_samples(
    path: Path, targets: Dict[str, MetricTargetReference], server: Optional[dict], cancel_event: Optional[Cancellation]
) -> Iterator[NodeMetricSample]:
    definitions = server.get('metrics', {}) if server else {}
    if not isinstance(definitions, dict):
        raise ValueError('Invalid server metrics definitions')
    with path.open(encoding='utf-8') as file:
        while line := file.readline(16 * 1024 * 1024 + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise ImportCancelled()
            if len(line) > 16 * 1024 * 1024:
                raise ValueError('Server metrics record exceeds size limit')
            try:
                event = json.loads(line)
            except ValueError:
                raise ValueError('Invalid server metrics record') from None
            if not isinstance(event, dict) or type(event.get('timestamp_ns')) is not int:
                raise ValueError('Server metrics timestamp is missing')
            timestamp = event['timestamp_ns']
            if (
                timestamp < 0
                or not isinstance(event.get('endpoint_url'), str)
                or not isinstance(event.get('metrics'), dict)
            ):
                raise ValueError('Invalid server metrics record')
            endpoint = normalize_metrics_url(event['endpoint_url'])
            target = targets.get(endpoint)
            sent, latency, phase = (
                event.get('request_sent_ns'),
                event.get('endpoint_latency_ns'),
                event.get('phase_kind'),
            )
            if any(value is not None and (type(value) is not int or value < 0) for value in (sent, latency)):
                raise ValueError('Invalid server metrics scrape timing')
            if phase not in (None, 'warmup', 'profiling'):
                phase = None
            for name, series in event['metrics'].items():
                if not isinstance(name, str) or not isinstance(series, list):
                    raise ValueError('Invalid server metrics series')
                definition = definitions.get(name, {})
                metric_type = definition.get('type', 'unknown') if isinstance(definition, dict) else 'unknown'
                if metric_type not in ('counter', 'gauge', 'histogram'):
                    metric_type = 'unknown'
                for observation in series:
                    if not isinstance(observation, dict) or not isinstance(observation.get('labels'), dict):
                        raise ValueError('Invalid server metrics observation')
                    labels = observation['labels']
                    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in labels.items()):
                        raise ValueError('Invalid server metrics labels')
                    if target is not None and any(labels.get(key) != value for key, value in target.labels.items()):
                        continue
                    values = {key: value for key, value in observation.items() if key != 'labels'}
                    try:
                        json.dumps(values, allow_nan=False)
                    except (ValueError, TypeError):
                        raise ValueError('Invalid server metrics values') from None
                    yield NodeMetricSample(
                        timestamp_ns=timestamp,
                        node_id=target.node_id if target else None,
                        role=target.role if target else None,
                        engine=target.engine if target else None,
                        endpoint=sanitize_endpoint(target.url if target else endpoint) or '',
                        metric_name=name,
                        metric_type=metric_type,
                        labels=labels,
                        observation=values,
                        request_sent_ns=sent,
                        scrape_latency_ns=latency,
                        phase_kind=phase,
                    )


def _statistics(summary: dict) -> list[MetricStatistics]:
    results = []
    for key, (output_unit, input_unit, scale) in _METRICS.items():
        item = summary.get(key)
        if item is None:
            continue
        if not isinstance(item, dict) or item.get('unit') != input_unit:
            raise ValueError(f'Unsupported summary metric: {key}')
        count = _integer(_number(item.get('count')), f'{key}.count')
        statistics = {}
        for stat in ('avg', 'min', 'max', 'p50', 'p95', 'p99', 'std'):
            value = _number(item.get(stat))
            if value is not None:
                if value < 0 or (key == 'request_error_rate' and value > 100):
                    raise ValueError(f'Invalid statistic for {key}')
                statistics[stat] = value * scale
        results.append(
            MetricStatistics(
                key=key,
                unit=output_unit,
                source='aiperf_client',
                scope='run',
                count=count,
                value=statistics.get('avg'),
                statistics=statistics,
            )
        )
    return results


def _phase_window(manifest: Optional[dict]) -> tuple[Optional[int], Optional[int]]:
    if manifest is None:
        return None, None
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('phases'), list):
        raise ValueError('Unsupported AIPerf phase manifest')
    if any(not isinstance(phase, dict) for phase in manifest['phases']):
        raise ValueError('Invalid AIPerf phase entry')
    phases = [phase for phase in manifest['phases'] if phase.get('phase_kind') == 'profiling']
    if len(phases) != 1:
        return None, None
    start, end = phases[0].get('start_ns'), phases[0].get('end_ns')
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        raise ValueError('Invalid profiling window')
    return start, end


def _atomic_json(path: Path, value: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(value, file, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_aiperf_run(arguments: AIPerfImportArguments, cancel_event: Optional[Cancellation] = None) -> PerfRunResult:
    """Import raw files once per content hash, then atomically publish a generation."""
    raw = Path(arguments.raw_dir).resolve(strict=True)
    destination = Path(arguments.normalized_dir).resolve()
    if not raw.is_dir() or destination == raw or destination.is_relative_to(raw):
        raise ValueError('Invalid normalized result directory')
    files = _files(raw)
    targets, target_identity = _targets(arguments, files)
    identity, hashes = _hash_files(files, arguments.allow_partial_tail, target_identity, cancel_event)
    summary = _json(files['profile_export_aiperf.json']) if 'profile_export_aiperf.json' in files else None
    if summary is not None and (
        summary.get('aiperf_version') != arguments.executor_version or summary.get('schema_version') != '1.4'
    ):
        raise ValueError('Unsupported AIPerf summary version')
    server = _json(files['server_metrics_export.json']) if 'server_metrics_export.json' in files else None
    if server is not None and (
        server.get('aiperf_version') != arguments.executor_version or server.get('schema_version') != '1.1'
    ):
        raise ValueError('Unsupported AIPerf server metrics version')
    manifest = _json(files['phase_manifest.json']) if 'phase_manifest.json' in files else None
    start, end = _phase_window(manifest)
    metrics = _statistics(summary) if summary is not None else []
    destination.mkdir(parents=True, exist_ok=True)
    generations = destination / 'generations'
    generations.mkdir(exist_ok=True)
    generations = generations.resolve(strict=True)
    if generations == destination or not generations.is_relative_to(destination):
        raise ValueError('Result generations escape the result directory')
    generation = generations / identity
    reused_generation = generation.exists()
    if reused_generation and not generation.resolve(strict=True).is_relative_to(generations):
        raise ValueError('Result generation escapes the result directory')
    if not reused_generation:
        temporary = Path(tempfile.mkdtemp(prefix='.import-', dir=generations))
        try:
            database = sqlite3.connect(temporary / 'requests.db')
            successes = errors = cancelled = incomplete = profiling = invalid_cache = outside_window = 0
            matched_nodes: set[str] = set()
            telemetry_invalid = False
            state = _ImportState()
            try:
                database.execute(
                    'CREATE TABLE requests (idx INTEGER PRIMARY KEY, phase TEXT NOT NULL, '
                    'status TEXT NOT NULL, record_json TEXT NOT NULL)'
                )
                database.execute('CREATE INDEX requests_phase_idx ON requests (phase, idx)')
                database.execute(
                    'CREATE TABLE node_samples (idx INTEGER PRIMARY KEY, node_id TEXT, '
                    'metric_name TEXT NOT NULL, timestamp_ns INTEGER NOT NULL, sample_json TEXT NOT NULL)'
                )
                database.execute(
                    'CREATE INDEX node_samples_lookup ON node_samples (node_id, metric_name, timestamp_ns, idx)'
                )
                batch = []
                for record in _records(files['profile_export.jsonl'], state, arguments.allow_partial_tail):
                    if cancel_event is not None and cancel_event.is_set():
                        raise ImportCancelled()
                    batch.append((record.index, record.phase, record.status, record.model_dump_json()))
                    if record.phase == 'profiling':
                        profiling += 1
                        successes += record.status == 'success'
                        errors += record.status == 'error'
                        cancelled += record.status == 'cancelled'
                        incomplete += record.status == 'incomplete'
                        if (
                            start is not None
                            and end is not None
                            and (
                                (record.start_ns is not None and record.start_ns < start)
                                or (record.end_ns is not None and record.end_ns > end)
                            )
                        ):
                            outside_window += 1
                        invalid_cache += (
                            record.cached_tokens is not None
                            and record.prompt_tokens_source == 'server_usage'
                            and record.prompt_tokens is not None
                            and record.cached_tokens > record.prompt_tokens
                        )
                    if len(batch) >= 500:
                        database.executemany('INSERT INTO requests VALUES (?, ?, ?, ?)', batch)
                        batch.clear()
                if batch:
                    database.executemany('INSERT INTO requests VALUES (?, ?, ?, ?)', batch)
                if 'server_metrics_export.jsonl' in files:
                    database.execute('SAVEPOINT telemetry')
                    try:
                        node_batch = []
                        for index, sample in enumerate(
                            _node_samples(files['server_metrics_export.jsonl'], targets, server, cancel_event)
                        ):
                            if sample.node_id is not None:
                                matched_nodes.add(sample.node_id)
                            node_batch.append(
                                (
                                    index,
                                    sample.node_id,
                                    sample.metric_name,
                                    sample.timestamp_ns,
                                    sample.model_dump_json(),
                                )
                            )
                            if len(node_batch) >= 500:
                                database.executemany('INSERT INTO node_samples VALUES (?, ?, ?, ?, ?)', node_batch)
                                node_batch.clear()
                        if node_batch:
                            database.executemany('INSERT INTO node_samples VALUES (?, ?, ?, ?, ?)', node_batch)
                        database.execute('RELEASE telemetry')
                    except (ValueError, UnicodeError):
                        database.execute('ROLLBACK TO telemetry')
                        database.execute('RELEASE telemetry')
                        matched_nodes.clear()
                        telemetry_invalid = True
                database.commit()
            finally:
                database.close()
            reasons = []
            if summary is None:
                reasons.append('summary_missing')
            if not profiling:
                reasons.append('profiling_records_missing')
            if errors or cancelled or incomplete:
                reasons.append('request_errors_or_cancellations')
            if state.partial_tail:
                reasons.append('truncated_final_record')
            if invalid_cache:
                reasons.append('invalid_cache_usage')
            if start is None:
                reasons.append('profiling_window_unverified')
            elif outside_window:
                reasons.append('profiling_records_outside_window')
            if manifest is not None and start is not None:
                phase = next(row for row in manifest['phases'] if row.get('phase_kind') == 'profiling')
                total_count = phase.get('total_request_count')
                success_count = phase.get('successful_request_count')
                if (
                    type(total_count) is not int
                    or type(success_count) is not int
                    or (total_count != profiling or success_count != successes)
                ):
                    reasons.append('phase_counts_unverified')
            missing_nodes = sorted(
                target.node_id for target in arguments.targets if target.node_id not in matched_nodes
            )
            if telemetry_invalid:
                reasons.append('telemetry_invalid')
            if 'server_metrics_export.jsonl' in files and server is None:
                reasons.append('telemetry_schema_unverified')
            if missing_nodes:
                reasons.append('configured_node_metrics_missing')
            if arguments.targets:
                reasons.append('telemetry_coverage_unverified')
            if arguments.metrics_required and not arguments.targets:
                reasons.append('required_targets_missing')
            if summary is not None:
                completed = summary.get('completed_request_count')
                expected = _number(completed.get('avg')) if isinstance(completed, dict) else None
                if expected is None or expected != profiling:
                    reasons.append('request_count_unverified')
                successful = summary.get('request_count')
                expected_successes = _number(successful.get('avg')) if isinstance(successful, dict) else None
                if expected_successes is None or expected_successes != successes:
                    reasons.append('success_count_unverified')
                if summary.get('is_complete') is not True:
                    reasons.append('upstream_incomplete')
            run = PerfRunResult(
                executor_version=arguments.executor_version,
                summary_schema=summary.get('schema_version') if summary else None,
                server_metrics_schema=server.get('schema_version') if server else None,
                quality=(
                    'invalid'
                    if arguments.metrics_required
                    and (missing_nodes or telemetry_invalid or not server or not arguments.targets)
                    else ('partial' if reasons else 'complete')
                ),
                quality_reasons=reasons,
                profiling_requests=profiling,
                profiling_successes=successes,
                profiling_errors=errors,
                profiling_cancelled=cancelled,
                profiling_incomplete=incomplete,
                profiling_start_ns=start,
                profiling_end_ns=end,
                metrics=metrics,
            )
            if _hash_files(files, arguments.allow_partial_tail, target_identity, cancel_event)[1] != hashes:
                raise ValueError('Raw artifacts changed during import')
            _atomic_json(temporary / 'run.json', run.model_dump(mode='json'))
            _atomic_json(
                temporary / 'import.json',
                ImportManifest(
                    input_hash=identity,
                    files=hashes,
                    allow_partial_tail=arguments.allow_partial_tail,
                    target_identity=target_identity,
                ).model_dump(),
            )
            try:
                os.rename(temporary, generation)
            except OSError as error:
                if error.errno not in (errno.EEXIST, errno.ENOTEMPTY) or not generation.is_dir():
                    raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    published = ImportManifest.model_validate(_json(_owned_file(generation, 'import.json')))
    if (
        published.input_hash != identity
        or published.parser_version != _PARSER
        or published.files != hashes
        or published.allow_partial_tail != arguments.allow_partial_tail
        or published.target_identity != target_identity
    ):
        raise ValueError('Existing result generation does not match the raw artifacts')
    _owned_file(generation, 'requests.db')
    run = PerfRunResult.model_validate(_json(_owned_file(generation, 'run.json')))
    if (
        reused_generation
        and _hash_files(files, arguments.allow_partial_tail, target_identity, cancel_event)[1] != hashes
    ):
        raise ValueError('Raw artifacts changed during import')
    if cancel_event is not None and cancel_event.is_set():
        raise ImportCancelled()
    _atomic_json(destination / 'current.json', {'generation': identity})
    return run


def _generation(normalized_dir: str) -> Path:
    root = Path(normalized_dir).resolve(strict=True)
    pointer = _json(_owned_file(root, 'current.json'))
    identity = pointer.get('generation')
    if not isinstance(identity, str) or re.fullmatch(r'[0-9a-f]{64}', identity) is None:
        raise ValueError('Invalid result generation')
    generation = (root / 'generations' / identity).resolve(strict=True)
    if not generation.is_relative_to(root / 'generations'):
        raise ValueError('Result generation escapes the result directory')
    manifest = ImportManifest.model_validate(_json(_owned_file(generation, 'import.json')))
    if manifest.input_hash != identity:
        raise ValueError('Result generation identity mismatch')
    return generation


def load_aiperf_run(normalized_dir: str) -> PerfRunResult:
    """Read the currently published normalized run."""
    generation = _generation(normalized_dir)
    run = PerfRunResult.model_validate(_json(_owned_file(generation, 'run.json')))
    manifest = ImportManifest.model_validate(_json(_owned_file(generation, 'import.json')))
    if manifest.parser_version != _PARSER:
        return run.model_copy(
            update={
                'quality': 'invalid' if run.quality == 'invalid' else 'partial',
                'quality_reasons': [*run.quality_reasons, 'parser_version_outdated'],
            }
        )
    return run


def query_aiperf_requests(
    normalized_dir: str,
    offset: int = 0,
    limit: int = 100,
    phase: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[int, list[PerfRequestRecord]]:
    """Read a bounded, stable page and total count without loading raw JSONL."""
    if type(offset) is not int or type(limit) is not int or not 0 <= offset <= 2**63 - 1 or not 1 <= limit <= 500:
        raise ValueError('Invalid request page')
    if phase is not None and phase not in ('warmup', 'profiling'):
        raise ValueError('Invalid request phase')
    if status is not None and status not in ('success', 'failed'):
        raise ValueError('Invalid request status')
    generation = _generation(normalized_dir)
    database_path = _owned_file(generation, 'requests.db')
    database = sqlite3.connect(f'file:{quote(str(database_path))}?mode=ro', uri=True)
    try:
        conditions = []
        parameters = []
        if phase is not None:
            conditions.append('phase = ?')
            parameters.append(phase)
        if status is not None:
            conditions.append('status = ?' if status == 'success' else 'status != ?')
            parameters.append('success')
        clause = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        total = database.execute('SELECT COUNT(*) FROM requests' + clause, parameters).fetchone()[0]
        rows = database.execute(
            'SELECT record_json FROM requests' + clause + ' ORDER BY idx LIMIT ? OFFSET ?', [*parameters, limit, offset]
        )
        return total, [PerfRequestRecord.model_validate_json(row[0]) for row in rows]
    finally:
        database.close()


def request_observation_bounds(normalized_dir: str) -> RequestObservationBounds:
    """Return the extent of timed profiling requests without calling it a phase window."""
    generation = _generation(normalized_dir)
    database_path = _owned_file(generation, 'requests.db')
    database = sqlite3.connect(f'file:{quote(str(database_path))}?mode=ro', uri=True)
    try:
        total = database.execute("SELECT COUNT(*) FROM requests WHERE phase = 'profiling'").fetchone()[0]
        start, end, count = database.execute(
            "SELECT MIN(json_extract(record_json, '$.start_ns')), MAX(json_extract(record_json, '$.end_ns')), COUNT(*) "
            "FROM requests WHERE phase = 'profiling' AND json_extract(record_json, '$.start_ns') IS NOT NULL "
            "AND json_extract(record_json, '$.end_ns') > json_extract(record_json, '$.start_ns')"
        ).fetchone()
        return RequestObservationBounds(start_ns=start, end_ns=end, timed_requests=count, total_requests=total)
    finally:
        database.close()


def observed_cache_usage(normalized_dir: str) -> CacheObservation:
    """Aggregate only successful requests with valid server-reported cache usage."""
    generation = _generation(normalized_dir)
    manifest = ImportManifest.model_validate(_json(_owned_file(generation, 'import.json')))
    if manifest.parser_version not in (_PARSER, 'aiperf-0.13.0-v2', 'aiperf-0.13.0-v3'):
        return CacheObservation(
            observed_requests=0,
            successful_requests=None,
            availability='unavailable',
            reason='Legacy parser cannot verify request completion; reimport raw artifacts',
        )
    database_path = _owned_file(generation, 'requests.db')
    database = sqlite3.connect(f'file:{quote(str(database_path))}?mode=ro', uri=True)
    successful = observed = cached = prompt = 0
    try:
        rows = database.execute("SELECT record_json FROM requests WHERE phase = 'profiling' AND status = 'success'")
        while batch := rows.fetchmany(500):
            for row in batch:
                record = PerfRequestRecord.model_validate_json(row[0])
                successful += 1
                if (
                    record.prompt_tokens_source != 'server_usage'
                    or record.cached_tokens_source != 'server_usage'
                    or record.prompt_tokens is None
                    or record.cached_tokens is None
                    or record.prompt_tokens <= 0
                    or record.cached_tokens > record.prompt_tokens
                ):
                    continue
                observed += 1
                prompt += record.prompt_tokens
                cached += record.cached_tokens
    finally:
        database.close()
    if not observed:
        return CacheObservation(
            observed_requests=0,
            successful_requests=successful,
            availability='unavailable',
            reason='No valid server-reported cache and prompt token pairs',
        )
    return CacheObservation(
        cached_tokens=cached,
        prompt_tokens=prompt,
        ratio=cached / prompt,
        observed_requests=observed,
        successful_requests=successful,
        source='server_usage',
        availability='available',
    )


def request_cache_groups(normalized_dir: str) -> CacheRequestGroups:
    """Calculate exact nearest-rank latency distributions for verified request cache usage."""
    coverage = observed_cache_usage(normalized_dir)
    if coverage.availability != 'available':
        return CacheRequestGroups(
            availability='unavailable',
            reason=coverage.reason,
            successful_requests=coverage.successful_requests,
            observed_requests=0,
        )
    if coverage.observed_requests > 200000:
        return CacheRequestGroups(
            availability='unavailable',
            reason='request_group_budget_exceeded',
            successful_requests=coverage.successful_requests,
            observed_requests=coverage.observed_requests,
        )
    generation = _generation(normalized_dir)
    database_path = _owned_file(generation, 'requests.db')
    database = sqlite3.connect(f'file:{quote(str(database_path))}?mode=ro', uri=True)
    valid = (
        "phase='profiling' AND status='success' "
        "AND json_extract(record_json, '$.prompt_tokens_source')='server_usage' "
        "AND json_extract(record_json, '$.cached_tokens_source')='server_usage' "
        "AND json_extract(record_json, '$.prompt_tokens')>0 "
        "AND json_extract(record_json, '$.cached_tokens')>=0 "
        "AND json_extract(record_json, '$.cached_tokens')<=json_extract(record_json, '$.prompt_tokens')"
    )

    def distribution(where: str, field: str) -> RequestLatencyDistribution:
        expression = f"json_extract(record_json, '$.{field}')"
        criterion = f"{where} AND json_type(record_json, '$.{field}') IN ('integer', 'real')"
        count, mean = database.execute(f'SELECT COUNT(*), AVG({expression}) FROM requests WHERE {criterion}').fetchone()
        values = []
        for percentile in (0.5, 0.95, 0.99):
            if not count:
                values.append(None)
                continue
            index = max(0, math.ceil(percentile * count) - 1)
            values.append(
                database.execute(
                    f'SELECT {expression} FROM requests WHERE {criterion} ORDER BY {expression} LIMIT 1 OFFSET ?',
                    (index,),
                ).fetchone()[0]
            )
        return RequestLatencyDistribution(count=count, mean=mean, p50=values[0], p95=values[1], p99=values[2])

    try:
        groups = []
        for name, operator in (('no_cached_tokens', '='), ('some_cached_tokens', '>')):
            where = f"{valid} AND json_extract(record_json, '$.cached_tokens'){operator}0"
            count = database.execute(f'SELECT COUNT(*) FROM requests WHERE {where}').fetchone()[0]
            groups.append(
                CacheRequestGroup(
                    group=name,
                    count=count,
                    latency=distribution(where, 'latency_seconds'),
                    ttft=distribution(where, 'ttft_seconds'),
                )
            )
        return CacheRequestGroups(
            availability='available',
            successful_requests=coverage.successful_requests,
            observed_requests=coverage.observed_requests,
            groups=groups,
        )
    finally:
        database.close()


def query_node_metrics(
    normalized_dir: str,
    node_id: Optional[str] = None,
    metric_name: Optional[str] = None,
    start_ns: Optional[int] = None,
    end_ns: Optional[int] = None,
    offset: int = 0,
    limit: int = 500,
) -> tuple[int, list[NodeMetricSample]]:
    """Read a bounded source-attributed node time-series page."""
    if type(offset) is not int or type(limit) is not int or not 0 <= offset <= 2**63 - 1 or not 1 <= limit <= 2000:
        raise ValueError('Invalid node metrics page')
    for value in (start_ns, end_ns):
        if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
            raise ValueError('Invalid node metrics window')
    for value in (node_id, metric_name):
        if value is not None and (not isinstance(value, str) or not value or len(value) > 512 or '\x00' in value):
            raise ValueError('Invalid node metrics dimension')
    if start_ns is not None and end_ns is not None and start_ns > end_ns:
        raise ValueError('Invalid node metrics window')
    clauses = []
    parameters = []
    for column, value, operation in (
        ('node_id', node_id, '='),
        ('metric_name', metric_name, '='),
        ('timestamp_ns', start_ns, '>='),
        ('timestamp_ns', end_ns, '<='),
    ):
        if value is not None:
            clauses.append(f'{column} {operation} ?')
            parameters.append(value)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    generation = _generation(normalized_dir)
    database_path = _owned_file(generation, 'requests.db')
    database = sqlite3.connect(f'file:{quote(str(database_path))}?mode=ro', uri=True)
    try:
        total = database.execute('SELECT COUNT(*) FROM node_samples' + where, parameters).fetchone()[0]
        rows = database.execute(
            'SELECT sample_json FROM node_samples' + where + ' ORDER BY timestamp_ns, idx LIMIT ? OFFSET ?',
            [*parameters, limit, offset],
        )
        return total, [NodeMetricSample.model_validate_json(row[0]) for row in rows]
    finally:
        database.close()
