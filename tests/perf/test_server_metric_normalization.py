"""Versioned upstream families remain node-scoped and fail closed on bad data."""

import json
import shutil
from pathlib import Path

import pytest

from evalscope.perf.integrations.aiperf.metrics import (
    NodeMetricsAnalysis,
    analyze_node_metrics,
    cache_lookup_ratios,
    covers_node_ids,
    project_node_metrics,
)
from evalscope.perf.results.aiperf_import import import_aiperf_run, request_observation_bounds
from evalscope.perf.results.models import AIPerfImportArguments, NodeMetricSample
from evalscope.perf.results.telemetry import MetricWindow

FIXTURES = Path(__file__).parent / 'fixtures' / 'aiperf'


def _sample(engine: str, node: str, role: str, name: str, kind: str, timestamp: int,
            observation: dict, labels: dict | None = None) -> NodeMetricSample:
    return NodeMetricSample(timestamp_ns=timestamp, node_id=node, role=role, engine=engine,
                            endpoint='http://fixture/metrics', metric_name=f'{engine}:{name}', metric_type=kind,
                            labels=labels or {'model': 'fixture'}, observation=observation)


def test_vllm_histograms_and_token_lookup_ratios_are_node_scoped() -> None:
    window = MetricWindow(start_ns=10, end_ns=20)
    samples = []
    for timestamp, hits, queries, bucket, count, total in [(10, 10, 20, 5, 10, 3), (20, 18, 30, 8, 15, 5)]:
        samples.extend([
            _sample('vllm', 'p', 'prefill', 'prefix_cache_hits', 'counter', timestamp, {'value': hits}),
            _sample('vllm', 'p', 'prefill', 'prefix_cache_queries', 'counter', timestamp, {'value': queries}),
            _sample('vllm', 'p', 'prefill', 'request_prefill_time_seconds', 'histogram', timestamp,
                    {'buckets': {'1': bucket, '+Inf': count}, 'count': float(count), 'sum': total}),
        ])
    rows = project_node_metrics(samples, window, 10, 'aiperf_phase')
    histogram = next(row for row in rows if row.metric.key == 'prefill_time')
    assert histogram.histogram.count == 5 and histogram.histogram.mean == pytest.approx(0.4)
    assert histogram.histogram.p50 == pytest.approx(2.5 / 3)
    ratio = cache_lookup_ratios(rows)[0]
    assert ratio.ratio == pytest.approx(0.8) and ratio.hit_tokens == 8 and ratio.lookup_tokens == 10
    assert all(row.verification == 'deployment_unverified' and row.attribution == 'node_window_shared_traffic'
               for row in rows)
    assert all(row.node_id == 'p' and row.role == 'prefill' for row in rows)


def test_sglang_gauge_units_queues_and_missing_do_not_get_fabricated() -> None:
    samples = [
        _sample('sglang', 'd', 'decode', 'kv_transfer_latency_ms', 'gauge', 10, {'value': 20}),
        _sample('sglang', 'd', 'decode', 'kv_transfer_latency_ms', 'gauge', 15, {'value': 40}),
        _sample('sglang', 'd', 'decode', 'num_decode_transfer_queue_reqs', 'gauge', 10, {'value': 2}),
        _sample('sglang', 'd', 'decode', 'cache_hit_rate', 'gauge', 10, {'value': 1.5}),
        _sample('sglang', 'd', 'decode', 'request_prefill_time_seconds', 'histogram', 10, {}),
    ]
    rows = project_node_metrics(samples, MetricWindow(start_ns=10, end_ns=20), 10, 'observed_request_bounds')
    by_key = {row.metric.key: row for row in rows}
    assert by_key['kv_transfer_latency'].statistic.value == pytest.approx(0.03)
    assert by_key['kv_transfer_latency'].metric.unit == 'seconds'
    assert by_key['decode_transfer_queue'].statistic.value == 2
    assert by_key['engine_cache_hit_rate'].reason == 'invalid_scalar_range'
    assert 'prefill_time' not in by_key
    assert not cache_lookup_ratios(rows)


def test_resets_mismatched_labels_and_types_do_not_form_valid_cache_ratios() -> None:
    samples = [
        _sample('vllm', 'p', 'prefill', 'prefix_cache_hits', 'counter', 10, {'value': 8}),
        _sample('vllm', 'p', 'prefill', 'prefix_cache_hits', 'counter', 20, {'value': 1}),
        _sample('vllm', 'p', 'prefill', 'prefix_cache_queries', 'counter', 10, {'value': 5}),
        _sample('vllm', 'p', 'prefill', 'prefix_cache_queries', 'counter', 20, {'value': 10}),
        _sample('vllm', 'other', 'prefill', 'prefix_cache_queries', 'gauge', 10, {'value': 3}),
    ]
    rows = project_node_metrics(samples, MetricWindow(start_ns=10, end_ns=20), 10, 'aiperf_phase')
    ratios = {row.node_id: row for row in cache_lookup_ratios(rows)}
    assert ratios['p'].availability == 'invalid' and ratios['p'].ratio is None
    assert ratios['other'].availability == 'missing' and ratios['other'].ratio is None


def test_sglang_newer_transfer_histogram_is_not_treated_as_old_gauge() -> None:
    samples = [
        _sample('sglang', 'd', 'decode', 'kv_transfer_latency_ms', 'histogram', 10,
                {'buckets': {'10': 1, '+Inf': 2}, 'count': 2, 'sum': 15}),
        _sample('sglang', 'd', 'decode', 'kv_transfer_latency_ms', 'histogram', 20,
                {'buckets': {'10': 3, '+Inf': 4}, 'count': 4, 'sum': 25}),
    ]
    row = project_node_metrics(samples, MetricWindow(start_ns=10, end_ns=20), 10, 'aiperf_phase')[0]
    assert row.metric.upstream_version == '0.5.23' and row.metric.kind == 'histogram'
    assert row.histogram.count == 2 and row.histogram.mean == pytest.approx(0.005)
    assert row.histogram.buckets['0.01'] == 2


def test_zero_histogram_observations_do_not_satisfy_required_node() -> None:
    samples = [
        _sample('vllm', 'p', 'prefill', 'request_prefill_time_seconds', 'histogram', t,
                {'buckets': {'1': 2, '+Inf': 2}, 'count': 2, 'sum': 1}) for t in (10, 20)
    ]
    rows = project_node_metrics(samples, MetricWindow(start_ns=10, end_ns=20), 10, 'aiperf_phase')
    analysis = NodeMetricsAnalysis(availability='partial', max_gap_ns=10, series=rows)
    assert rows[0].histogram.count == 0 and not covers_node_ids(analysis, ['p'])


def test_normalized_samples_use_phase_or_explicit_observed_request_bounds(tmp_path: Path) -> None:
    raw, normalized = tmp_path / 'raw', tmp_path / 'normalized'
    raw.mkdir()
    for name in ('profile_export_aiperf.json', 'profile_export.jsonl'):
        shutil.copyfile(FIXTURES / name, raw / name)
    definitions = {'vllm:num_requests_waiting': {'type': 'gauge'}}
    (raw / 'server_metrics_export.json').write_text(json.dumps({
        'schema_version': '1.1', 'aiperf_version': '0.13.0', 'metrics': definitions,
    }))
    records = [json.loads(line) for line in (raw / 'profile_export.jsonl').read_text().splitlines()]
    profiling = [row for row in records if row['metadata']['phase_kind'] == 'profiling']
    start = min(row['metadata']['request_start_ns'] for row in profiling)
    end = max(row['metadata']['request_end_ns'] for row in profiling)
    events = [
        {'endpoint_url': 'http://node/metrics', 'timestamp_ns': start - 10,
         'metrics': {'vllm:num_requests_waiting': [{'labels': {'model': 'fixture'}, 'value': 2}]}},
        {'endpoint_url': 'http://node/metrics', 'timestamp_ns': end + 10,
         'metrics': {'vllm:num_requests_waiting': [{'labels': {'model': 'fixture'}, 'value': 3}]}},
    ]
    (raw / 'server_metrics_export.jsonl').write_text(''.join(json.dumps(item) + '\n' for item in events))
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(normalized), targets=[{
        'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': 'http://node/metrics',
        'labels': {'model': 'fixture'},
    }])
    import_aiperf_run(args)
    bounds = request_observation_bounds(str(normalized))
    assert bounds.start_ns == start and bounds.end_ns == end and bounds.timed_requests == bounds.total_requests == 2
    analysis = analyze_node_metrics(str(normalized), max_gap_ns=end - start + 100)
    assert analysis.availability == 'partial' and analysis.window_source == 'observed_request_bounds'
    assert analysis.series[0].statistic.value == 2
    assert analyze_node_metrics(str(normalized), max_gap_ns=100, limit=1).reason == 'node_sample_limit_exceeded'
    shutil.copyfile(FIXTURES / 'phase_manifest.json', raw / 'phase_manifest.json')
    import_aiperf_run(args)
    assert analyze_node_metrics(str(normalized), max_gap_ns=end - start + 100).window_source == 'aiperf_phase'


def test_untrusted_relay_mapping_cannot_reassign_node_identity(tmp_path: Path) -> None:
    raw = tmp_path / 'raw'
    raw.mkdir()
    shutil.copyfile(FIXTURES / 'profile_export.jsonl', raw / 'profile_export.jsonl')
    (raw / 'metrics_target_map.json').write_text(json.dumps({'schema_version': '1.0', 'targets': {
        'p': {'collector_url': 'http://127.0.0.1:1234/token/metrics', 'endpoint_url': 'http://other/metrics'}
    }}))
    args = AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(tmp_path / 'normalized'), targets=[{
        'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': 'http://node/metrics',
    }])
    with pytest.raises(ValueError, match='attribution'):
        import_aiperf_run(args)


def test_scenario_publishes_node_analysis_from_fixed_aiperf(tmp_path: Path) -> None:
    import os

    executable = os.environ.get('EVALSCOPE_TEST_AIPERF')
    if not executable:
        pytest.skip('requires isolated AIPerf 0.13.0')
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    from evalscope.perf.executors.models import ExecutionContext, ExternalRuntime
    from evalscope.perf.experiments.scenario import CacheScenarioArguments, run_cache_scenario
    from tests.perf.test_cache_scenario import _config
    from tests.perf.test_prefix_priming import _server
    from tests.perf.test_prefix_workload_plan import _Tokenizer

    root = tmp_path / 'run'
    root.mkdir()
    tokenizer_path = root / 'tokenizer'
    backend = Tokenizer(models.WordLevel({'[UNK]': 0, **{f't{i}': i + 1 for i in range(200)}}, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]').save_pretrained(str(tokenizer_path))
    runtime = ExternalRuntime(executable=executable, graceful_timeout=3, terminate_timeout=2, kill_timeout=3)
    context = ExecutionContext(task_id='pd', output_dir=str(root), runtime=runtime)
    with _server(tmp_path / 'inference', 'slow_success') as (url, _), \
         _server(tmp_path / 'prefill', 'success', 'pd_vllm', 'p-secret') as (prefill, _), \
         _server(tmp_path / 'decode', 'success', 'pd_sglang', 'd-secret') as (decode, _):
        config = _config(url, str(tokenizer_path)).model_dump()
        config['telemetry'] = {'required': True, 'sample_interval_seconds': 0.1, 'targets': [
            {'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': prefill.removesuffix('/v1') + '/metrics',
             'labels': {'model': 'fixture'}, 'headers': {'Authorization': 'Bearer p-secret'}},
            {'node_id': 'd', 'role': 'decode', 'engine': 'sglang', 'url': decode.removesuffix('/v1') + '/metrics',
             'labels': {'model': 'fixture'}, 'headers': {'Authorization': 'Bearer d-secret'}},
        ]}
        from evalscope.perf.executors.arguments import AIPerfArguments
        outcome = run_cache_scenario(AIPerfArguments.model_validate(config), context,
                                     CacheScenarioArguments(target_ratio=0, namespace='pd', tokenizer_id='fixture',
                                                            template_id='fixture-template'),
                                     _Tokenizer(), list(range(200)))
    assert outcome.status == 'completed', outcome
    for node in ('prefill', 'decode'):
        scrapes = [json.loads(line) for line in (tmp_path / node / 'metrics.jsonl').read_text().splitlines()]
        assert scrapes and all(row['authorization_matches'] for row in scrapes)
    manifest = json.loads((root / 'scenario.json').read_text())
    assert manifest['node_analysis'] == 'node_analysis.json'
    analysis = json.loads((root / 'node_analysis.json').read_text())
    assert analysis['window_source'] == 'observed_request_bounds'
    assert {row['node_id'] for row in analysis['scrapes']} == {'p', 'd'}
    assert all(row['observed_successful_scrapes'] > 0 and row['failed_scrapes'] is None for row in analysis['scrapes'])
    assert all(row['mean_latency_seconds'] is not None for row in analysis['scrapes'])
    keys = {(row['node_id'], row['metric']['key']) for row in analysis['series']}
    assert {('p', 'prefill_time'), ('p', 'prefix_cache_hit_tokens'), ('d', 'kv_transfer_latency'),
            ('d', 'decode_transfer_queue')} <= keys
    transfer = next(row for row in analysis['series'] if row['metric']['key'] == 'kv_transfer_latency')
    assert transfer['metric']['source_unit'] == 'milliseconds' and transfer['metric']['unit'] == 'seconds'
    from evalscope.perf.results.aiperf_import import query_node_metrics
    from evalscope.utils.uri_utils import sanitize_endpoint
    _, samples = query_node_metrics(str(root / 'normalized'), limit=500)
    assert {sample.endpoint for sample in samples if sample.node_id == 'p'} == {sanitize_endpoint(prefill)}
    assert {sample.endpoint for sample in samples if sample.node_id == 'd'} == {sanitize_endpoint(decode)}
    for path in (root / 'scenario.json', root / 'node_analysis.json', root / 'benchmark.log',
                 root / 'raw' / 'aiperf' / 'metrics_target_map.json'):
        text = path.read_text()
        assert 'p-secret' not in text and 'd-secret' not in text
    assert (root / 'raw' / 'aiperf' / 'metrics_target_map.json').stat().st_mode & 0o077 == 0
    assert not any(secret in path.read_bytes() for path in (root / 'raw' / 'aiperf').rglob('*') if path.is_file()
                   for secret in (b'p-secret', b'd-secret', b'fixture-key'))


def test_required_nodes_with_only_unknown_metrics_are_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evalscope.perf.experiments.scenario as module
    from evalscope.perf.executors.arguments import AIPerfArguments
    from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome, ExternalRuntime
    from evalscope.perf.experiments.scenario import CacheScenarioArguments, run_cache_scenario
    from tests.perf.test_cache_scenario import _config
    from tests.perf.test_prefix_workload_plan import _Tokenizer

    root = tmp_path / 'run'
    root.mkdir()
    monkeypatch.setattr(module, 'probe_version', lambda context, cancel: '0.13.0')

    def execute(*args, **kwargs):
        raw = root / 'raw' / 'aiperf'
        raw.mkdir(parents=True)
        for name in ('profile_export.jsonl', 'profile_export_aiperf.json', 'server_metrics_export.json'):
            shutil.copyfile(FIXTURES / name, raw / name)
        shutil.copyfile(FIXTURES / 'multi_server_metrics_export.jsonl', raw / 'server_metrics_export.jsonl')
        return ExecutionOutcome(exit_reason='completed', cleanup_confirmed=True, artifact_status='available',
                                raw_artifact_dir=str(raw))

    monkeypatch.setattr(module, '_run_verified', execute)
    config = _config('http://localhost/v1').model_dump()
    config['telemetry'] = {'required': True, 'targets': [
        {'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': 'http://127.0.0.1:18081/metrics'},
        {'node_id': 'd', 'role': 'decode', 'engine': 'sglang', 'url': 'http://127.0.0.1:18082/metrics'},
    ]}
    context = ExecutionContext(task_id='pd', output_dir=str(root), runtime=ExternalRuntime(executable='/unused'))
    outcome = run_cache_scenario(AIPerfArguments.model_validate(config), context,
                                 CacheScenarioArguments(target_ratio=0, namespace='pd', tokenizer_id='fixture',
                                                        template_id='fixture-template'),
                                 _Tokenizer(), list(range(200)))
    assert outcome.status == 'error' and outcome.quality == 'invalid'
    state = json.loads((root / 'scenario.json').read_text())
    assert 'required_telemetry_missing' in state['quality_reasons']
