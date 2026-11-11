"""Plan execution: declared order, one-off warmup, fail-stop, cancellation and provenance."""

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import pytest

from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome, ExternalRuntime
from evalscope.perf.experiments.scenario import CacheScenarioOutcome
from evalscope.perf.plans import compile_plan_text
from evalscope.perf.plans import execution as execution_module
from evalscope.perf.plans.execution import PlanWarmupState, run_perf_plan

FIXTURES = Path(__file__).parent / 'fixtures' / 'perf_plan'


class _Cancel:
    def __init__(self, after: int = 0) -> None:
        self.calls = 0
        self.after = after

    def is_set(self) -> bool:
        self.calls += 1
        return self.calls > self.after


def _context(root: Path, plan_hash: str = None) -> ExecutionContext:
    return ExecutionContext(
        task_id='task',
        output_dir=str(root),
        runtime=ExternalRuntime(executable='/aiperf'),
        expected_plan_hash=plan_hash,
    )


def _managed_plan(name: str = 'managed-small.json', **overrides):
    document = json.loads((FIXTURES / name).read_text())
    document['execution'] = {'order': 'load_then_input'}
    document['workload']['input_lengths'] = overrides.get('input_lengths', [1024, 2048])
    document['workload']['target_ratios'] = overrides.get('target_ratios', [0.5])
    document['load']['points'] = overrides.get('points', [{'concurrency': 2}, {'concurrency': 8}])
    return document


def test_each_planned_run_is_executed_once_in_declared_order(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    plan = compile_plan_text(json.dumps(_managed_plan()))
    seen: List[tuple] = []

    def fake_scenario(arguments, context, scenario, tokenizer, pool, cancel_event, on_phase=None):
        seen.append((Path(context.output_dir).name, scenario.target_ratio, scenario.input_tokens))
        if on_phase is not None:
            on_phase('priming')
            on_phase('profiling')
        return CacheScenarioOutcome(status='completed', quality='partial', cleanup_confirmed=True)

    monkeypatch.setattr(execution_module, 'run_cache_scenario', fake_scenario)
    outcome = run_perf_plan(plan, _context(root), tokenizer=object(), token_pool=[1], tokenizer_id='tok', template_id='tpl')
    assert outcome.status == 'completed'
    assert outcome.completed_runs == plan.run_count == 4
    assert outcome.quality == 'partial'
    assert [item[0] for item in seen] == [run.run_id for run in plan.runs]
    assert [item[1] for item in seen] == [0.5] * 4

    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert manifest['status'] == 'completed'
    assert manifest['plan_hash'] == plan.plan_hash
    assert [row['status'] for row in manifest['runs']] == ['completed'] * 4
    assert manifest['completed_runs'] == 4

    for name in ('source.redacted.json', 'normalized.json', 'compiled.json', 'environment.json'):
        path = root / 'plan' / name
        assert path.is_file()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / 'plan').stat().st_mode) == 0o700
    compiled = json.loads((root / 'plan' / 'compiled.json').read_text())
    assert compiled['plan_hash'] == plan.plan_hash
    assert compiled['semantic_fingerprint'] == plan.semantic_fingerprint


def test_a_failed_run_stops_the_matrix_without_retry(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    plan = compile_plan_text(json.dumps(_managed_plan()))
    calls = []

    def fake_scenario(arguments, context, scenario, tokenizer, pool, cancel_event, on_phase=None):
        calls.append(context.output_dir)
        if len(calls) == 1:
            return CacheScenarioOutcome(status='error', quality='invalid', reason='profiling_failed', cleanup_confirmed=True)
        return CacheScenarioOutcome(status='completed', quality='partial', cleanup_confirmed=True)

    monkeypatch.setattr(execution_module, 'run_cache_scenario', fake_scenario)
    outcome = run_perf_plan(plan, _context(root), tokenizer=object(), token_pool=[1], tokenizer_id='tok', template_id='tpl')
    assert len(calls) == 1
    assert outcome.status == 'error'
    assert outcome.quality == 'invalid'
    assert outcome.completed_runs == 0
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert [row['status'] for row in manifest['runs']] == ['error', 'not_started', 'not_started', 'not_started']


def test_cancellation_between_runs_keeps_partial_results(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    plan = compile_plan_text(json.dumps(_managed_plan()))
    calls = []

    def fake_scenario(arguments, context, scenario, tokenizer, pool, cancel_event, on_phase=None):
        calls.append(1)
        return CacheScenarioOutcome(status='completed', quality='partial', cleanup_confirmed=True)

    monkeypatch.setattr(execution_module, 'run_cache_scenario', fake_scenario)
    cancel = _Cancel(after=1)
    outcome = run_perf_plan(
        plan, _context(root), tokenizer=object(), token_pool=[1], tokenizer_id='tok', template_id='tpl', cancel_event=cancel
    )
    assert outcome.status == 'stopped'
    assert outcome.quality == 'partial'
    assert outcome.completed_runs == 1
    assert len(calls) == 1
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert manifest['runs'][0]['status'] == 'completed'
    assert manifest['runs'][1]['status'] == 'not_started'


def test_ordinary_warmup_runs_once_before_the_matrix(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    document = _managed_plan()
    document['warmup'] = {
        'enabled': True,
        'request_count': 3,
        'input_tokens': 16000,
        'output_token_limit': 1000,
        'concurrency': 1,
        'failure_policy': 'record_and_continue',
    }
    plan = compile_plan_text(json.dumps(document))
    warmups = []

    def fake_warmup(*args, **kwargs):
        warmups.append(1)
        return PlanWarmupState(
            status='completed', request_count=3, completed_requests=3, total_input_tokens=16000, output_token_limit=1000
        )

    monkeypatch.setattr(execution_module, '_warmup', fake_warmup)
    monkeypatch.setattr(
        execution_module,
        'run_cache_scenario',
        lambda *args, **kwargs: CacheScenarioOutcome(status='completed', quality='partial', cleanup_confirmed=True),
    )
    outcome = run_perf_plan(plan, _context(root), tokenizer=object(), token_pool=[1], tokenizer_id='tok', template_id='tpl')
    assert len(warmups) == 1
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert manifest['warmup']['status'] == 'completed'
    assert manifest['warmup']['request_count'] == 3
    assert manifest['status'] == 'completed'
    assert outcome.status == 'completed'


def test_warmup_failure_aborts_without_formal_requests(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    document = _managed_plan()
    document['warmup'] = {
        'enabled': True,
        'request_count': 3,
        'input_tokens': 16000,
        'output_token_limit': 1000,
        'concurrency': 1,
        'failure_policy': 'abort',
    }
    plan = compile_plan_text(json.dumps(document))

    monkeypatch.setattr(
        execution_module, '_warmup', lambda *args, **kwargs: PlanWarmupState(status='failed', reason='warmup_failed')
    )
    monkeypatch.setattr(
        execution_module,
        'run_cache_scenario',
        lambda *args, **kwargs: pytest.fail('formal runs must not start after an aborted warmup'),
    )
    outcome = run_perf_plan(plan, _context(root), tokenizer=object(), token_pool=[1], tokenizer_id='tok', template_id='tpl')
    assert outcome.status == 'error'
    assert outcome.quality == 'invalid'
    assert 'warmup_failed' in outcome.quality_reasons
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert manifest['status'] == 'error'
    assert all(row['status'] == 'not_started' for row in manifest['runs'])


def test_a_changed_plan_is_refused_before_creating_artifacts(tmp_path: Path) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    plan = compile_plan_text(json.dumps(_managed_plan()))
    tampered = plan.model_copy(update={'plan_hash': 'f' * 64})
    with pytest.raises(ValueError, match='does not match this compiler version'):
        run_perf_plan(tampered, _context(root), tokenizer=object(), token_pool=[1], tokenizer_id='tok', template_id='tpl')
    assert not (root / 'plan').exists()

    with pytest.raises(ValueError, match='does not match'):
        run_perf_plan(
            plan, _context(root, 'e' * 64), tokenizer=object(), token_pool=[1], tokenizer_id='tok', template_id='tpl'
        )
    assert not (root / 'plan').exists()


def test_synthetic_run_records_the_system_and_user_structure(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    document = json.loads((FIXTURES / 'synthetic-small.json').read_text())
    document['load']['points'] = [{'concurrency': 1}]
    document['warmup']['enabled'] = False
    plan = compile_plan_text(json.dumps(document))

    def fake_run_synthetic(spec, context, cancel_event=None):
        artifacts = Path(context.output_dir) / 'raw' / 'aiperf'
        artifacts.mkdir(parents=True)
        return ExecutionOutcome(
            exit_reason='completed',
            artifact_status='available',
            raw_artifact_dir=str(artifacts),
            executor_version='0.13.0',
            cleanup_confirmed=True,
        )

    monkeypatch.setattr(execution_module, 'run_synthetic', fake_run_synthetic)
    monkeypatch.setattr(
        execution_module,
        'import_aiperf_run',
        lambda *args, **kwargs: SimpleNamespace(quality='partial', quality_reasons=['partial_tail']),
    )
    monkeypatch.setattr(
        execution_module,
        'observed_cache_usage',
        lambda path: SimpleNamespace(ratio=0.9, model_dump=lambda mode: {'observed': 0}),
    )
    outcome = run_perf_plan(
        plan, _context(root), source_text=json.dumps(document), tokenizer=object(), token_pool=[1], tokenizer_id='tok', template_id='tpl'
    )
    assert outcome.status == 'completed'
    assert outcome.completed_runs == 1

    run = plan.runs[0]
    scenario = json.loads((root / 'runs' / run.run_id / 'scenario.json').read_text())
    assert scenario['message_structure'] == 'system_and_user'
    assert scenario['shared_system_tokens'] == 900 and scenario['unique_user_tokens'] == 100
    assert scenario['declared_total_input_tokens'] == 1000
    assert scenario['request_count'] == plan.budgets.formal_requests
    assert scenario['cache'] == {'observed': 0}
    assert scenario['cache_verification']['status'] == 'verified'
    assert scenario['cache_verification']['observed_ratio'] == 0.9
    source = json.loads((root / 'plan' / 'source.redacted.json').read_text())
    assert source['kind'] == 'submitted_text'
    assert source['document']['workload']['cases'][0]['shared_system_tokens'] == 900


def test_managed_plan_requires_a_tokenizer_identity(tmp_path: Path) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    plan = compile_plan_text(json.dumps(_managed_plan()))
    with pytest.raises(ValueError, match='tokenizer identity'):
        run_perf_plan(plan, _context(root), tokenizer=object(), token_pool=[1])
    assert not os.path.exists(root / 'plan')


def _synthetic_document(**workload) -> dict:
    document = json.loads((FIXTURES / 'synthetic-small.json').read_text())
    document['load']['points'] = [{'concurrency': 1}]
    document['warmup']['enabled'] = False
    document['workload'].update(workload)
    return document


def _run_synthetic_plan(
    tmp_path: Path,
    monkeypatch,
    document: dict,
    observed_ratio: Optional[float] = None,
    import_quality: str = 'complete',
    import_reasons: Optional[List[str]] = None,
):
    root = tmp_path / 'task'
    root.mkdir()

    def fake_run_synthetic(spec, context, cancel_event=None):
        artifacts = Path(context.output_dir) / 'raw' / 'aiperf'
        artifacts.mkdir(parents=True)
        return ExecutionOutcome(
            exit_reason='completed',
            artifact_status='available',
            raw_artifact_dir=str(artifacts),
            executor_version='0.13.0',
            cleanup_confirmed=True,
        )

    def fake_observation(path: str):
        if observed_ratio is None:
            raise ValueError('server usage is unavailable')
        return SimpleNamespace(ratio=observed_ratio, model_dump=lambda mode: {'ratio': observed_ratio})

    monkeypatch.setattr(execution_module, 'run_synthetic', fake_run_synthetic)
    monkeypatch.setattr(
        execution_module,
        'import_aiperf_run',
        lambda *args, **kwargs: SimpleNamespace(quality=import_quality, quality_reasons=list(import_reasons or [])),
    )
    monkeypatch.setattr(execution_module, 'observed_cache_usage', fake_observation)
    plan = compile_plan_text(json.dumps(document))
    outcome = run_perf_plan(plan, _context(root))
    run_dir = root / 'runs' / plan.runs[0].run_id
    scenario = json.loads((run_dir / 'scenario.json').read_text())
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    return outcome, scenario, manifest['runs'][0]


def test_a_missing_ratio_observation_does_not_degrade_a_synthetic_run(tmp_path: Path, monkeypatch) -> None:
    outcome, scenario, row = _run_synthetic_plan(tmp_path, monkeypatch, _synthetic_document(), observed_ratio=None)
    assert outcome.status == 'completed'
    assert outcome.quality == 'complete'
    assert not outcome.quality_reasons
    assert scenario['cache_verification']['status'] == 'unavailable'
    assert scenario['cache_verification']['required'] is False
    assert scenario['cache_verification']['declared_ratio'] == 0.9
    assert 'cache' not in scenario
    assert row['quality'] == 'complete'


def test_a_required_ratio_observation_is_a_defect_when_missing(tmp_path: Path, monkeypatch) -> None:
    document = _synthetic_document(require_observed_ratio=True)
    outcome, scenario, row = _run_synthetic_plan(tmp_path, monkeypatch, document, observed_ratio=None)
    assert outcome.quality == 'partial'
    assert any(reason.endswith(':target_ratio_unverified') for reason in outcome.quality_reasons)
    assert scenario['cache_verification']['status'] == 'unavailable'
    assert scenario['cache_verification']['required'] is True
    assert row['quality'] == 'partial'


def test_a_ratio_mismatch_marks_the_run_partial(tmp_path: Path, monkeypatch) -> None:
    outcome, scenario, row = _run_synthetic_plan(tmp_path, monkeypatch, _synthetic_document(), observed_ratio=0.3)
    assert outcome.status == 'completed'
    assert outcome.quality == 'partial'
    assert any(reason.endswith(':target_ratio_mismatch') for reason in outcome.quality_reasons)
    assert scenario['cache_verification']['status'] == 'mismatch'
    assert scenario['cache_verification']['delta'] == pytest.approx(-0.6)
    assert scenario['cache_verification']['tolerance'] == 0.05
    assert row['quality'] == 'partial'


def test_a_ratio_within_tolerance_stays_complete(tmp_path: Path, monkeypatch) -> None:
    outcome, scenario, row = _run_synthetic_plan(tmp_path, monkeypatch, _synthetic_document(), observed_ratio=0.87)
    assert outcome.quality == 'complete'
    assert scenario['cache_verification']['status'] == 'verified'
    assert row['quality'] == 'complete'


@pytest.mark.skipif(not os.environ.get('EVALSCOPE_TEST_AIPERF'), reason='requires isolated AIPerf 0.13.0')
def test_a_real_synthetic_plan_executes_and_records_the_cache_verification(tmp_path: Path) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    from evalscope.perf.executors.models import ExternalRuntime
    from tests.perf.test_prefix_priming import _server

    tokenizer_path = tmp_path / 'tokenizer'
    backend = Tokenizer(models.WordLevel({'[UNK]': 0, 'alpha': 1, 'beta': 2, 'gamma': 3}, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]').save_pretrained(tokenizer_path)

    root = tmp_path / 'task'
    root.mkdir()
    with _server(tmp_path / 'inference', 'success') as (url, capture):
        document = _synthetic_document()
        document['connection'].update({'url': url, 'tokenizer_path': str(tokenizer_path)})
        document['workload']['cases'] = [
            {
                'case_id': 'input-250',
                'declared_total_input_tokens': 250,
                'shared_system_tokens': 200,
                'unique_user_tokens': 50,
                'target_prefix_ratio': 0.8,
            }
        ]
        document['load'].update({'request_count': 2, 'output_token_limit': 4})
        plan = compile_plan_text(json.dumps(document))
        context = ExecutionContext(
            task_id='task',
            output_dir=str(root),
            runtime=ExternalRuntime(executable=os.environ['EVALSCOPE_TEST_AIPERF']),
        )
        outcome = run_perf_plan(plan, context)

    assert outcome.status == 'completed'
    assert outcome.completed_runs == 1
    assert capture.exists() and capture.stat().st_size > 0
    scenario = json.loads((root / 'runs' / plan.runs[0].run_id / 'scenario.json').read_text())
    verification = scenario['cache_verification']
    assert verification['status'] in {'verified', 'mismatch', 'unavailable'}
    assert verification['declared_ratio'] == 0.8
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert manifest['runs'][0]['cache_verification'] == verification


@pytest.mark.parametrize(
    ('import_quality', 'observed_ratio', 'required', 'expected_quality', 'expected_status'),
    [
        ('complete', 0.9, False, 'complete', 'completed'),
        ('complete', 0.3, False, 'partial', 'completed'),
        ('complete', None, True, 'partial', 'completed'),
        ('complete', None, False, 'complete', 'completed'),
        ('partial', 0.9, False, 'partial', 'completed'),
        ('partial', 0.3, False, 'partial', 'completed'),
        ('invalid', 0.9, False, 'invalid', 'error'),
        ('invalid', 0.3, False, 'invalid', 'error'),
        ('invalid', None, True, 'invalid', 'error'),
    ],
)
def test_import_quality_can_only_degrade_when_cache_verification_runs(
    tmp_path: Path,
    monkeypatch,
    import_quality: str,
    observed_ratio: Optional[float],
    required: bool,
    expected_quality: str,
    expected_status: str,
) -> None:
    document = _synthetic_document(require_observed_ratio=required)
    outcome, scenario, row = _run_synthetic_plan(
        tmp_path,
        monkeypatch,
        document,
        observed_ratio=observed_ratio,
        import_quality=import_quality,
        import_reasons=['structural_damage'] if import_quality == 'invalid' else [],
    )
    assert row['quality'] == expected_quality
    assert scenario['quality'] == expected_quality
    assert outcome.status == expected_status
    assert outcome.quality == expected_quality
    if import_quality == 'invalid':
        assert any(reason.endswith(':invalid') for reason in outcome.quality_reasons)
        assert any(reason.endswith(':structural_damage') for reason in outcome.quality_reasons)
    if expected_quality == 'partial':
        assert outcome.quality_reasons
