"""Offline import and paging against captured AIPerf exports."""

import json
import shutil
from pathlib import Path

import pytest

from evalscope.perf.results.aiperf_import import (
    ImportCancelled,
    import_aiperf_run,
    load_aiperf_run,
    observed_cache_usage,
    query_aiperf_requests,
    query_node_metrics,
)
from evalscope.perf.results.models import AIPerfImportArguments

FIXTURES = Path(__file__).parent / 'fixtures' / 'aiperf'


def _copy(raw: Path, mapping: dict[str, str]) -> None:
    raw.mkdir()
    for source, target in mapping.items():
        shutil.copyfile(FIXTURES / source, raw / target)


def test_import_success_preserves_counts_units_and_paging(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    names = ('profile_export_aiperf.json', 'profile_export.jsonl', 'server_metrics_export.json', 'phase_manifest.json')
    _copy(raw, {name: name for name in names})
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))
    run = import_aiperf_run(args)
    assert run.quality == 'complete'
    assert (run.profiling_requests, run.profiling_successes, run.profiling_errors) == (2, 2, 0)
    metrics = {metric.key: metric for metric in run.metrics}
    assert metrics['request_latency'].unit == 'seconds'
    assert metrics['request_latency'].count == 2
    assert metrics['request_latency'].value == pytest.approx(0.0006758125)
    assert metrics['request_throughput'].count is None
    total, first = query_aiperf_requests(str(output), limit=1, phase='profiling')
    assert total == 2 and len(first) == 1 and first[0].index == 1
    assert first[0].prompt_tokens == 8 and first[0].prompt_tokens_source == 'server_usage'
    assert first[0].cached_tokens is None and first[0].cached_tokens_source is None
    assert first[0].ttft_seconds is not None
    pointer = (output / 'current.json').read_text()
    assert import_aiperf_run(args) == run
    assert (output / 'current.json').read_text() == pointer
    assert len(list((output / 'generations').iterdir())) == 1
    assert load_aiperf_run(str(output)) == run


def test_missing_usage_stays_estimated_and_cache_unknown(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    _copy(raw, {'missing_usage_profile_export_aiperf.json': 'profile_export_aiperf.json',
                'missing_usage_profile_export.jsonl': 'profile_export.jsonl'})
    run = import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output)))
    assert run.quality == 'partial' and 'profiling_window_unverified' in run.quality_reasons
    total, records = query_aiperf_requests(str(output))
    assert total == 2
    assert all(record.prompt_tokens_source == 'aiperf_estimate' for record in records)
    assert all(record.cached_tokens is None and record.cached_tokens_source is None for record in records)
    observation = observed_cache_usage(str(output))
    assert observation.ratio is None and observation.availability == 'unavailable'


def test_error_only_and_mixed_failures_remain_partial(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    _copy(raw, {'http_error_profile_export.jsonl': 'profile_export.jsonl'})
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))
    run = import_aiperf_run(args)
    assert run.quality == 'partial'
    assert 'summary_missing' in run.quality_reasons
    assert run.profiling_successes == 0 and run.profiling_errors == 2
    _, records = query_aiperf_requests(str(output))
    assert all(record.status == 'error' and record.error_code == 503 for record in records)
    shutil.copyfile(FIXTURES / 'partial_profile_export.jsonl', raw / 'profile_export.jsonl')
    shutil.copyfile(FIXTURES / 'partial_profile_export_aiperf.json', raw / 'profile_export_aiperf.json')
    mixed = import_aiperf_run(args)
    assert mixed.quality == 'partial' and mixed.profiling_successes == mixed.profiling_errors == 1


def test_invalid_import_never_replaces_previous_generation(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    _copy(raw, {'profile_export_aiperf.json': 'profile_export_aiperf.json',
                'profile_export.jsonl': 'profile_export.jsonl', 'phase_manifest.json': 'phase_manifest.json'})
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))
    original = import_aiperf_run(args)
    pointer = (output / 'current.json').read_text()
    summary_path = raw / 'profile_export_aiperf.json'
    summary = json.loads(summary_path.read_text())
    summary['schema_version'] = '2.0'
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match='version'):
        import_aiperf_run(args)
    assert (output / 'current.json').read_text() == pointer
    assert load_aiperf_run(str(output)) == original
    summary['schema_version'] = '1.4'
    summary_path.write_text(json.dumps(summary))
    with (raw / 'profile_export.jsonl').open('a') as file:
        file.write('{broken')
    with pytest.raises(ValueError, match='record'):
        import_aiperf_run(args)
    assert (output / 'current.json').read_text() == pointer


def test_reused_generation_rejects_raw_changes_during_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evalscope.perf.results.aiperf_import as importer

    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    _copy(raw, {'profile_export_aiperf.json': 'profile_export_aiperf.json',
                'profile_export.jsonl': 'profile_export.jsonl'})
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))
    import_aiperf_run(args)
    pointer = (output / 'current.json').read_text()
    original = importer._json

    def read_and_modify(path: Path) -> dict:
        value = original(path)
        if path.name == 'profile_export_aiperf.json':
            with (raw / 'profile_export.jsonl').open('a') as file:
                file.write('{}\n')
        return value

    monkeypatch.setattr(importer, '_json', read_and_modify)
    with pytest.raises(ValueError, match='changed during import'):
        import_aiperf_run(args)
    assert (output / 'current.json').read_text() == pointer


def test_large_paging_and_path_safety(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    raw.mkdir()
    sample = (FIXTURES / 'missing_usage_profile_export.jsonl').read_text().splitlines()[0]
    (raw / 'profile_export.jsonl').write_text((sample + '\n') * 1201)
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))
    assert import_aiperf_run(args).profiling_requests == 1201
    total, page = query_aiperf_requests(str(output), offset=500, limit=3)
    assert total == 1201 and [record.index for record in page] == [500, 501, 502]
    with pytest.raises(ValueError):
        query_aiperf_requests(str(output), limit=501)
    with pytest.raises(ValueError):
        import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(raw / 'bad')))
    (output / 'current.json').write_text('{"generation":"../../escape"}')
    with pytest.raises(ValueError):
        load_aiperf_run(str(output))


def test_truncated_final_record_is_explicitly_partial(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    raw.mkdir()
    valid = (FIXTURES / 'missing_usage_profile_export.jsonl').read_text().splitlines()[0]
    (raw / 'profile_export.jsonl').write_text(valid + '\n{"metadata":')
    strict = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))
    with pytest.raises(ValueError, match='record'):
        import_aiperf_run(strict)
    assert not (output / 'current.json').exists()
    partial = strict.model_copy(update={'allow_partial_tail': True})
    run = import_aiperf_run(partial)
    assert run.quality == 'partial' and 'truncated_final_record' in run.quality_reasons
    assert query_aiperf_requests(str(output))[0] == 1
    with pytest.raises(ValueError, match='record'):
        import_aiperf_run(strict)


def test_truncated_utf8_tail_keeps_preceding_complete_records(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    raw.mkdir()
    valid = (FIXTURES / 'missing_usage_profile_export.jsonl').read_bytes().splitlines()[0]
    (raw / 'profile_export.jsonl').write_bytes(valid + b'\n{"text":"\xe4\xb8')
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output), allow_partial_tail=True)
    run = import_aiperf_run(args)
    assert run.quality == 'partial' and 'truncated_final_record' in run.quality_reasons
    assert query_aiperf_requests(str(output))[0] == 1


def test_incomplete_and_cancelled_requests_do_not_become_success_or_cache_hits(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    raw.mkdir()
    sample = json.loads((FIXTURES / 'profile_export.jsonl').read_text().splitlines()[1])
    incomplete = json.loads(json.dumps(sample))
    incomplete['metadata'].pop('request_end_ns')
    incomplete['metrics']['usage_prompt_cache_read_tokens'] = {'unit': 'tokens', 'value': 7}
    cancelled = json.loads(json.dumps(sample))
    cancelled['metadata']['was_cancelled'] = True
    failed = json.loads(json.dumps(sample))
    failed['error'] = {'code': 503}
    (raw / 'profile_export.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in
                                                       (sample, incomplete, cancelled, failed)))
    run = import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output)))
    assert (run.profiling_requests, run.profiling_successes, run.profiling_errors) == (4, 1, 1)
    assert (run.profiling_cancelled, run.profiling_incomplete) == (1, 1)
    _, records = query_aiperf_requests(str(output))
    assert [row.status for row in records] == ['success', 'incomplete', 'cancelled', 'error']
    assert records[1].status_reason == 'request_timestamps_missing'
    cache = observed_cache_usage(str(output))
    assert cache.successful_requests == 1 and cache.ratio is None


def test_non_boolean_overflow_marker_cannot_be_treated_as_success(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    raw.mkdir()
    record = json.loads((FIXTURES / 'profile_export.jsonl').read_text().splitlines()[1])
    record['metadata']['context_overflow_skip'] = 'true'
    (raw / 'profile_export.jsonl').write_text(json.dumps(record) + '\n')
    with pytest.raises(ValueError, match='overflow'):
        import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output)))


def test_invalid_negative_client_statistics_are_rejected(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    _copy(raw, {'profile_export.jsonl': 'profile_export.jsonl',
                'profile_export_aiperf.json': 'profile_export_aiperf.json'})
    summary = json.loads((raw / 'profile_export_aiperf.json').read_text())
    summary['request_latency']['avg'] = -1
    (raw / 'profile_export_aiperf.json').write_text(json.dumps(summary))
    with pytest.raises(ValueError, match='Invalid statistic'):
        import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output)))


def test_mismatched_phase_counts_and_outside_timestamps_are_not_complete(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    names = ('profile_export.jsonl', 'profile_export_aiperf.json', 'phase_manifest.json')
    _copy(raw, {name: name for name in names})
    manifest_path = raw / 'phase_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    phase = next(row for row in manifest['phases'] if row['phase_kind'] == 'profiling')
    phase['successful_request_count'] = 1
    manifest_path.write_text(json.dumps(manifest))
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))
    run = import_aiperf_run(args)
    assert run.quality == 'partial' and 'phase_counts_unverified' in run.quality_reasons
    phase['successful_request_count'] = 2
    phase['end_ns'] -= 1000000000
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='window'):
        import_aiperf_run(args)
    phase['end_ns'] = phase['start_ns'] + 1
    manifest_path.write_text(json.dumps(manifest))
    run = import_aiperf_run(args)
    assert run.quality == 'partial' and 'profiling_records_outside_window' in run.quality_reasons


def test_actual_cache_usage_is_distinct_and_invalid_counts_degrade_quality(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    raw.mkdir()
    record = json.loads((FIXTURES / 'profile_export.jsonl').read_text().splitlines()[1])
    record['metrics']['usage_prompt_cache_read_tokens'] = {'unit': 'tokens', 'value': 7}
    (raw / 'profile_export.jsonl').write_text(json.dumps(record) + '\n')
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))
    import_aiperf_run(args)
    _, records = query_aiperf_requests(str(output))
    assert records[0].cached_tokens == 7 and records[0].cached_tokens_source == 'server_usage'
    assert records[0].prompt_tokens == 8 and records[0].prompt_tokens_source == 'server_usage'
    assert observed_cache_usage(str(output)).ratio == 7 / 8
    record['metrics']['usage_prompt_cache_read_tokens']['value'] = 0
    (raw / 'profile_export.jsonl').write_text(json.dumps(record) + '\n')
    import_aiperf_run(args)
    assert observed_cache_usage(str(output)).ratio == 0
    record['metrics']['usage_prompt_cache_read_tokens']['value'] = 9
    (raw / 'profile_export.jsonl').write_text(json.dumps(record) + '\n')
    invalid = import_aiperf_run(args)
    assert invalid.quality == 'partial' and 'invalid_cache_usage' in invalid.quality_reasons
    assert observed_cache_usage(str(output)).ratio is None


def test_result_generation_rejects_symlink_escape(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    _copy(raw, {'profile_export.jsonl': 'profile_export.jsonl'})
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))
    import_aiperf_run(args)
    identity = json.loads((output / 'current.json').read_text())['generation']
    database = output / 'generations' / identity / 'requests.db'
    database.unlink()
    outside = tmp_path / 'outside.db'
    outside.write_bytes(b'not a database')
    database.symlink_to(outside)
    with pytest.raises(ValueError, match='escapes'):
        query_aiperf_requests(str(output))
    with pytest.raises(ValueError, match='escapes'):
        import_aiperf_run(args)


def test_cancelled_import_publishes_nothing_and_can_be_retried(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    _copy(raw, {'profile_export.jsonl': 'profile_export.jsonl'})
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))

    class _CancelAfterTwoChecks:
        def __init__(self) -> None:
            self.calls = 0

        def is_set(self) -> bool:
            self.calls += 1
            return self.calls > 2

    with pytest.raises(ImportCancelled):
        import_aiperf_run(args, cancel_event=_CancelAfterTwoChecks())
    assert not (output / 'current.json').exists()
    assert import_aiperf_run(args).profiling_requests == 2


def test_legacy_parser_results_remain_readable_but_cache_requires_reimport(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    raw.mkdir()
    record = json.loads((FIXTURES / 'profile_export.jsonl').read_text().splitlines()[1])
    record['metrics']['usage_prompt_cache_read_tokens'] = {'unit': 'tokens', 'value': 4}
    (raw / 'profile_export.jsonl').write_text(json.dumps(record) + '\n')
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output))
    import_aiperf_run(args)
    current = json.loads((output / 'current.json').read_text())['generation']
    legacy_id = '0' * 64
    legacy = output / 'generations' / legacy_id
    shutil.copytree(output / 'generations' / current, legacy)
    manifest_path = legacy / 'import.json'
    manifest = json.loads(manifest_path.read_text())
    manifest.update({'input_hash': legacy_id, 'parser_version': 'aiperf-0.13.0-v1'})
    manifest_path.write_text(json.dumps(manifest))
    (output / 'current.json').write_text(json.dumps({'generation': legacy_id}))
    assert 'parser_version_outdated' in load_aiperf_run(str(output)).quality_reasons
    observation = observed_cache_usage(str(output))
    assert observation.availability == 'unavailable' and observation.successful_requests is None
    assert observation.ratio is None
    import_aiperf_run(args)
    assert observed_cache_usage(str(output)).ratio == 0.5


def test_node_samples_retain_target_roles_without_claiming_verified_semantics(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    _copy(raw, {'profile_export.jsonl': 'profile_export.jsonl',
                'profile_export_aiperf.json': 'profile_export_aiperf.json',
                'server_metrics_export.json': 'server_metrics_export.json',
                'multi_server_metrics_export.jsonl': 'server_metrics_export.jsonl',
                'phase_manifest.json': 'phase_manifest.json'})
    targets = [
        {'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': 'http://127.0.0.1:18081/metrics'},
        {'node_id': 'd', 'role': 'decode', 'engine': 'sglang', 'url': 'http://127.0.0.1:18082/metrics'},
    ]
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output), targets=targets, metrics_required=True)
    run = import_aiperf_run(args)
    assert run.quality == 'partial' and 'telemetry_coverage_unverified' in run.quality_reasons
    total, samples = query_node_metrics(str(output), node_id='p', metric_name='mock_requests')
    assert total > 0 and all(sample.node_id == 'p' and sample.role == 'prefill' for sample in samples)
    assert all(sample.engine == 'vllm' and sample.metric_type == 'counter' for sample in samples)
    assert all(sample.semantics == 'unverified' and sample.scope == 'node_window' for sample in samples)
    _, decoded = query_node_metrics(str(output), node_id='d')
    assert decoded and all(sample.role == 'decode' and sample.engine == 'sglang' for sample in decoded)
    assert query_node_metrics(str(output), node_id="p' OR 1=1 --")[0] == 0
    with pytest.raises(ValueError):
        query_node_metrics(str(output), limit=2001)


def test_required_missing_labels_degrade_quality_without_losing_requests(tmp_path: Path) -> None:
    raw, output = tmp_path / 'raw', tmp_path / 'normalized'
    _copy(raw, {'profile_export.jsonl': 'profile_export.jsonl',
                'server_metrics_export.json': 'server_metrics_export.json',
                'multi_server_metrics_export.jsonl': 'server_metrics_export.jsonl'})
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(output), metrics_required=True, targets=[
        {'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': 'http://127.0.0.1:18081/metrics',
         'labels': {'model': 'not-present'}},
    ])
    run = import_aiperf_run(args)
    assert run.quality == 'invalid' and 'configured_node_metrics_missing' in run.quality_reasons
    assert query_node_metrics(str(output), node_id='p')[0] == 0
    assert query_aiperf_requests(str(output), phase='profiling')[0] == 2
