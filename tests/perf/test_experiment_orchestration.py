"""Serial matrix execution and cancellation using deterministic doubles."""

import json
import os
import threading
from pathlib import Path

import pytest

from evalscope.perf.executors.models import ExecutionContext, ExternalRuntime
from evalscope.perf.experiments.orchestrator import run_cache_experiment
from evalscope.perf.experiments.planner import plan_cache_experiment
from evalscope.perf.experiments.scenario import CacheScenarioOutcome
from tests.perf.test_experiment_planner import _config
from tests.perf.test_prefix_workload_plan import _Tokenizer


@pytest.mark.parametrize('failure_at,cancel_at', [(None, None), (3, None), (None, 3)])
def test_matrix_preserves_all_planned_subruns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               failure_at: int | None, cancel_at: int | None) -> None:
    import evalscope.perf.experiments.orchestrator as module

    arguments = _config()
    context = ExecutionContext(task_id='task', output_dir=str(tmp_path), runtime=ExternalRuntime(executable='/aiperf'))
    preview = plan_cache_experiment(arguments, 'task', 'tokenizer', 'template')
    event = threading.Event()
    calls = []

    def scenario(args: object, ctx: object, plan: object, tokenizer: object, pool: object,
                 cancel: object, on_phase: object) -> CacheScenarioOutcome:
        calls.append((args.load.points[0], plan.target_ratio, plan.namespace))
        on_phase('priming')
        on_phase('profiling')
        if len(calls) == failure_at:
            return CacheScenarioOutcome(status='error', quality='invalid', reason='injected', cleanup_confirmed=True)
        if len(calls) == cancel_at:
            event.set()
            return CacheScenarioOutcome(status='cancelled', quality='partial', cleanup_confirmed=True)
        on_phase('importing')
        return CacheScenarioOutcome(status='completed', quality='partial', cleanup_confirmed=True)

    monkeypatch.setattr(module, 'run_cache_scenario', scenario)
    outcome = run_cache_experiment(arguments, context, _Tokenizer(), list(range(200)), 'tokenizer', 'template', event)
    expected_status = 'error' if failure_at else ('stopped' if cancel_at else 'completed')
    assert outcome.status == expected_status and outcome.plan_hash == preview.plan_hash
    manifest = json.loads((tmp_path / 'experiment.json').read_text())
    assert manifest['plan_hash'] == preview.plan_hash and len(manifest['subruns']) == 18
    assert json.loads((tmp_path / 'experiment_plan.json').read_text())['plan_hash'] == preview.plan_hash
    if failure_at or cancel_at:
        assert len(calls) == 3 and outcome.completed_subruns == 2
        assert manifest['subruns'][3]['status'] == 'not_started'
    else:
        assert len(calls) == outcome.completed_subruns == 18
        assert all(row['status'] == 'completed' for row in manifest['subruns'])
    assert [(point, ratio, namespace) for point, ratio, namespace in calls] == [
        (item.load_point, item.target_ratio, item.namespace) for item in preview.subruns[:len(calls)]
    ]
    with pytest.raises(FileExistsError):
        run_cache_experiment(arguments, context, _Tokenizer(), list(range(200)), 'tokenizer', 'template', event)


@pytest.mark.parametrize('phase', ['generating', 'priming', 'profiling', 'draining', 'importing'])
def test_cancellation_in_each_phase_never_starts_next_subrun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    import evalscope.perf.experiments.orchestrator as module

    event = threading.Event()
    calls = []

    def scenario(args, context, plan, tokenizer, pool, cancel, on_phase):
        calls.append(plan.namespace)
        on_phase(phase)
        event.set()
        return CacheScenarioOutcome(status='cancelled', quality='partial', cleanup_confirmed=True)

    monkeypatch.setattr(module, 'run_cache_scenario', scenario)
    context = ExecutionContext(task_id='task', output_dir=str(tmp_path), runtime=ExternalRuntime(executable='/aiperf'))
    outcome = run_cache_experiment(_config(), context, _Tokenizer(), list(range(200)), 'tokenizer', 'template', event)
    state = json.loads((tmp_path / 'experiment.json').read_text())
    assert outcome.status == 'stopped' and len(calls) == 1
    assert state['subruns'][0]['status'] == 'cancelled'
    assert all(row['status'] == 'not_started' for row in state['subruns'][1:])


def test_unconfirmed_cleanup_stops_matrix_with_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evalscope.perf.experiments.orchestrator as module

    calls = []

    def scenario(*args, **kwargs):
        calls.append(True)
        return CacheScenarioOutcome(status='error', quality='invalid', cleanup_confirmed=False)

    monkeypatch.setattr(module, 'run_cache_scenario', scenario)
    context = ExecutionContext(task_id='task', output_dir=str(tmp_path), runtime=ExternalRuntime(executable='/aiperf'))
    outcome = run_cache_experiment(_config(), context, _Tokenizer(), list(range(200)), 'tokenizer', 'template')
    assert outcome.status == 'error' and not outcome.cleanup_confirmed and len(calls) == 1


def test_manifest_updates_are_atomically_readable(tmp_path: Path) -> None:
    from evalscope.perf.experiments.storage import write_experiment_json

    path = tmp_path / 'experiment.json'
    write_experiment_json(path, {'sequence': 0, 'payload': 'x' * 10000})

    def write_updates() -> None:
        for index in range(1, 101):
            write_experiment_json(path, {'sequence': index, 'payload': 'x' * 10000})

    writer = threading.Thread(target=write_updates)
    writer.start()
    while writer.is_alive():
        state = json.loads(path.read_text())
        assert len(state['payload']) == 10000 and 0 <= state['sequence'] <= 100
    writer.join()
    assert json.loads(path.read_text())['sequence'] == 100


@pytest.mark.skipif(not os.environ.get('EVALSCOPE_TEST_AIPERF'), reason='requires isolated AIPerf 0.13.0')
def test_real_three_ratio_matrix_matches_requests(tmp_path: Path) -> None:
    import hashlib

    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    from tests.perf.test_cache_scenario import _config as scenario_config
    from tests.perf.test_prefix_priming import _server

    root = tmp_path / 'run'
    root.mkdir()
    tokenizer_path = root / 'tokenizer'
    backend = Tokenizer(models.WordLevel({'[UNK]': 0, **{f't{i}': i + 1 for i in range(200)}}, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]').save_pretrained(str(tokenizer_path))
    context = ExecutionContext(task_id='matrix', output_dir=str(root), runtime=ExternalRuntime(
        executable=os.environ['EVALSCOPE_TEST_AIPERF'], graceful_timeout=3, terminate_timeout=2, kill_timeout=3,
    ))
    with _server(tmp_path, 'success') as (url, capture):
        args = scenario_config(url, str(tokenizer_path))
        plan = plan_cache_experiment(args, 'matrix', 'fixture', 'fixture-template')
        result = run_cache_experiment(args, context, _Tokenizer(), list(range(200)), 'fixture', 'fixture-template')
    assert result.status == 'completed' and result.completed_subruns == result.total_subruns == 3
    assert result.plan_hash == plan.plan_hash
    sent = [json.loads(line) for line in capture.read_text().splitlines()]
    cursor = 0
    for item in plan.subruns:
        manifest = json.loads((root / 'runs' / item.subrun_id / 'scenario.json').read_text())
        cursor += item.primer_requests
        measured = sent[cursor:cursor + item.request_count]
        cursor += item.request_count
        hashes = {row['sha256'] for row in manifest['workload']['requests']}
        assert {hashlib.sha256(row['body']['messages'][0]['content'].encode()).hexdigest() for row in measured} == hashes
        assert manifest['target_ratio'] == item.target_ratio
    assert cursor == len(sent)
