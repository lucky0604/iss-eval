"""Single cache scenario from generated prompts through the primer barrier."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from evalscope.perf.executors.arguments import AIPerfArguments
from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome, ExternalRuntime
from evalscope.perf.experiments.scenario import CacheScenarioArguments, run_cache_scenario
from tests.perf.test_prefix_priming import _server
from tests.perf.test_prefix_workload_plan import _Tokenizer

FIXTURES = Path(__file__).parent / 'fixtures' / 'aiperf'


def _config(url: str, tokenizer: str = '/tokenizer') -> AIPerfArguments:
    return AIPerfArguments(
        model='mock-model', url=url, api_key='fixture-key', tokenizer_path=tokenizer,
        cache_experiment={'target_ratios': [0, 0.8, 0.9], 'total_input_tokens': 20, 'prefix_pool_size': 2},
        load={'points': [{'concurrency': 1}], 'request_count': 6, 'output_token_limit': 2},
    )


def _context(root: Path, executable: str = '/unused') -> ExecutionContext:
    return ExecutionContext(task_id='scenario', output_dir=str(root), runtime=ExternalRuntime(
        executable=executable, graceful_timeout=3, terminate_timeout=2, kill_timeout=3,
    ))


def test_scenario_rejects_changed_tokenizer_identity(tmp_path: Path) -> None:
    tokenizer = _Tokenizer()
    tokenizer.tokenizer_hash = 'changed'
    with pytest.raises(ValueError, match='Tokenizer identity'):
        run_cache_scenario(_config('http://127.0.0.1:1/v1'), _context(tmp_path), CacheScenarioArguments(
            target_ratio=0.8, namespace='run', tokenizer_id='original', template_id='fixture-template',
        ), tokenizer, list(range(200)))


@pytest.mark.parametrize('ratio', [0.0, 0.8, 0.9])
def test_scenario_primes_then_profiles_generated_input(tmp_path: Path, ratio: float,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    import evalscope.perf.experiments.scenario as module

    root = tmp_path / 'run'
    root.mkdir()
    monkeypatch.setattr(module, 'probe_version', lambda context, cancel: '0.13.0')
    observed = []

    def fake_run(arguments: AIPerfArguments, context: ExecutionContext, input_file: str, version: str,
                 cancel_event: object) -> ExecutionOutcome:
        primers_sent = capture.read_text().splitlines() if capture.exists() else []
        assert len(primers_sent) == (0 if ratio == 0 else 2)
        observed.extend(json.loads(line)['text'] for line in Path(input_file).read_text().splitlines())
        raw = root / 'raw' / 'aiperf'
        raw.mkdir(parents=True)
        for name in ('profile_export.jsonl', 'profile_export_aiperf.json'):
            shutil.copyfile(FIXTURES / name, raw / name)
        return ExecutionOutcome(exit_reason='completed', exit_code=0, cleanup_confirmed=True,
                                artifact_status='available', raw_artifact_dir=str(raw), executor_version=version)

    monkeypatch.setattr(module, '_run_verified', fake_run)
    with _server(tmp_path, 'success') as (url, capture):
        result = run_cache_scenario(_config(url), _context(root), CacheScenarioArguments(
            target_ratio=ratio, namespace='run', tokenizer_id='fixture', template_id='fixture-template',
        ), _Tokenizer(), list(range(200)))
    assert result.status == 'completed' and result.quality == 'partial'
    assert result.cache is not None and result.cache.ratio is None
    manifest = json.loads((root / 'scenario.json').read_text())
    assert manifest['workload']['target_ratio'] == ratio
    assert 'planned_request_count_mismatch' in manifest['quality_reasons']
    assert 'fixture-key' not in json.dumps(manifest)
    requests = manifest['workload']['requests']
    assert len(observed) == len(requests) == 6
    assert [hashlib.sha256(text.encode()).hexdigest() for text in observed] == [row['sha256'] for row in requests]
    assert all(row['input_tokens'] == 20 for row in requests)
    assert {row['shared_tokens'] for row in requests} == ({2} if ratio == 0 else {round(ratio * 20)})


def test_primer_error_never_starts_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evalscope.perf.experiments.scenario as module

    root = tmp_path / 'run'
    root.mkdir()
    monkeypatch.setattr(module, 'probe_version', lambda context, cancel: '0.13.0')
    called = []
    monkeypatch.setattr(module, '_run_verified', lambda *args: called.append(True))
    with _server(tmp_path, 'http_error') as (url, capture):
        result = run_cache_scenario(_config(url), _context(root), CacheScenarioArguments(
            target_ratio=0.8, namespace='run', tokenizer_id='fixture', template_id='fixture-template',
        ), _Tokenizer(), list(range(200)))
    assert result.status == 'error' and result.reason == 'primer_failed'
    assert len(capture.read_text().splitlines()) == 1 and not called


def test_failed_profile_keeps_partial_records_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evalscope.perf.experiments.scenario as module
    from evalscope.perf.results.aiperf_import import load_aiperf_run, query_aiperf_requests

    root = tmp_path / 'run'
    root.mkdir()
    monkeypatch.setattr(module, 'probe_version', lambda context, cancel: '0.13.0')

    def failed_run(*args: object) -> ExecutionOutcome:
        raw = root / 'raw' / 'aiperf'
        raw.mkdir(parents=True)
        shutil.copyfile(FIXTURES / 'http_error_profile_export.jsonl', raw / 'profile_export.jsonl')
        return ExecutionOutcome(exit_reason='error', exit_code=1, cleanup_confirmed=True,
                                artifact_status='partial', raw_artifact_dir=str(raw), executor_version='0.13.0')

    monkeypatch.setattr(module, '_run_verified', failed_run)
    with _server(tmp_path, 'success') as (url, _):
        outcome = run_cache_scenario(_config(url), _context(root), CacheScenarioArguments(
            target_ratio=0, namespace='run', tokenizer_id='fixture', template_id='fixture-template',
        ), _Tokenizer(), list(range(200)))
    assert outcome.status == 'error' and outcome.cleanup_confirmed
    state = json.loads((root / 'scenario.json').read_text())
    assert state['normalized'] == 'normalized' and 'profiling_failed' in state['quality_reasons']
    assert load_aiperf_run(str(root / 'normalized')).quality == 'partial'
    assert query_aiperf_requests(str(root / 'normalized'))[0] == 2


@pytest.mark.parametrize('required', [False, True])
def test_cancelled_profile_keeps_partial_records_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, required: bool
) -> None:
    import evalscope.perf.experiments.scenario as module
    from evalscope.perf.results.aiperf_import import query_aiperf_requests

    root = tmp_path / 'run'
    root.mkdir()
    monkeypatch.setattr(module, 'probe_version', lambda context, cancel: '0.13.0')

    def cancelled_run(*args: object) -> ExecutionOutcome:
        raw = root / 'raw' / 'aiperf'
        raw.mkdir(parents=True)
        shutil.copyfile(FIXTURES / 'http_error_profile_export.jsonl', raw / 'profile_export.jsonl')
        return ExecutionOutcome(exit_reason='cancelled', cleanup_confirmed=True,
                                artifact_status='partial', raw_artifact_dir=str(raw))

    monkeypatch.setattr(module, '_run_verified', cancelled_run)
    with _server(tmp_path, 'success') as (url, _):
        config = _config(url).model_copy(update={'telemetry': {'required': required}})
        config = AIPerfArguments.model_validate(config.model_dump())
        outcome = run_cache_scenario(config, _context(root), CacheScenarioArguments(
            target_ratio=0, namespace='run', tokenizer_id='fixture', template_id='fixture-template',
        ), _Tokenizer(), list(range(200)))
    assert outcome.status == 'cancelled' and outcome.cleanup_confirmed
    assert outcome.quality == ('invalid' if required else 'partial')
    state = json.loads((root / 'scenario.json').read_text())
    assert state['normalized'] == 'normalized' and 'cancelled' in state['quality_reasons']
    assert query_aiperf_requests(str(root / 'normalized'))[0] == 2


def test_required_missing_telemetry_is_not_completed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evalscope.perf.experiments.scenario as module

    root = tmp_path / 'run'
    root.mkdir()
    monkeypatch.setattr(module, 'probe_version', lambda context, cancel: '0.13.0')

    def completed_run(*args: object) -> ExecutionOutcome:
        raw = root / 'raw' / 'aiperf'
        raw.mkdir(parents=True)
        for name in ('profile_export.jsonl', 'profile_export_aiperf.json'):
            shutil.copyfile(FIXTURES / name, raw / name)
        return ExecutionOutcome(exit_reason='completed', cleanup_confirmed=True,
                                artifact_status='available', raw_artifact_dir=str(raw))

    monkeypatch.setattr(module, '_run_verified', completed_run)
    with _server(tmp_path, 'success') as (url, _):
        config = _config(url).model_copy(update={'telemetry': {'required': True}})
        config = AIPerfArguments.model_validate(config.model_dump())
        outcome = run_cache_scenario(config, _context(root), CacheScenarioArguments(
            target_ratio=0, namespace='run', tokenizer_id='fixture', template_id='fixture-template',
        ), _Tokenizer(), list(range(200)))
    assert outcome.status == 'error' and outcome.quality == 'invalid'
    state = json.loads((root / 'scenario.json').read_text())
    assert state['normalized'] == 'normalized' and 'required_telemetry_missing' in state['quality_reasons']


@pytest.mark.skipif(not os.environ.get('EVALSCOPE_TEST_AIPERF'), reason='requires isolated AIPerf 0.13.0')
@pytest.mark.parametrize('ratio', [0.0, 0.8, 0.9])
def test_real_aiperf_receives_the_generated_scenario(tmp_path: Path, ratio: float) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    root = tmp_path / 'run'
    root.mkdir()
    tokenizer_path = root / 'tokenizer'
    backend = Tokenizer(models.WordLevel({'[UNK]': 0, **{f't{i}': i + 1 for i in range(200)}}, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]').save_pretrained(str(tokenizer_path))
    with _server(tmp_path, 'success') as (url, capture):
        result = run_cache_scenario(_config(url, str(tokenizer_path)),
                                    _context(root, os.environ['EVALSCOPE_TEST_AIPERF']), CacheScenarioArguments(
                                        target_ratio=ratio, namespace=f'run-{ratio}', tokenizer_id='fixture',
                                        template_id='fixture-template',
                                    ), _Tokenizer(), list(range(200)))
    assert result.status == 'completed', result
    assert result.quality == 'partial' and result.cache is not None and result.cache.ratio is None
    sent = [json.loads(line) for line in capture.read_text().splitlines()]
    primers = 0 if ratio == 0 else 2
    assert len(sent) == primers + 6
    assert all(item['body']['max_completion_tokens'] == 1 for item in sent[:primers])
    assert all(item['body']['max_completion_tokens'] == 2 for item in sent[primers:])
    manifest = json.loads((root / 'scenario.json').read_text())
    hashes = {row['sha256'] for row in manifest['workload']['requests']}
    sent_hashes = {hashlib.sha256(item['body']['messages'][0]['content'].encode()).hexdigest() for item in sent[primers:]}
    assert hashes == sent_hashes and len(sent_hashes) == 6
