"""Plan lifecycle: one artifact layout, quality rollup, cancellation and identity."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome, ExternalRuntime
from evalscope.perf.experiments.scenario import CacheScenarioOutcome
from evalscope.perf.plans import compile_plan_text, planned_run_count
from evalscope.perf.plans import execution as execution_module
from evalscope.perf.plans.execution import run_perf_plan, summarize_plan_quality
from evalscope.perf.plans.identity import compare_effective_identities, compare_plan_semantics
from evalscope.perf.plans.provenance import (
    EffectiveIdentity,
    TokenizerIdentity,
    read_effective_identity,
    read_plan_hash,
    read_submission,
    resolve_plan_dir,
)
from evalscope.perf.plans.strict import PlanParseError
from evalscope.perf.results.aiperf_import import ImportCancelled

FIXTURES = Path(__file__).parent / 'fixtures' / 'perf_plan'


def _document(name: str = 'managed-small.json', **overrides) -> dict:
    document = json.loads((FIXTURES / name).read_text())
    document['execution'] = {'order': 'load_then_input'}
    document['workload']['input_lengths'] = overrides.get('input_lengths', [1024, 2048])
    document['load']['points'] = overrides.get('points', [{'concurrency': 2}, {'concurrency': 8}])
    return document


def _synthetic_document() -> dict:
    """A synthetic plan without warmup, so no tokenizer is needed to exercise it."""
    document = json.loads((FIXTURES / 'synthetic-small.json').read_text())
    document['warmup']['enabled'] = False
    document['load']['points'] = [{'concurrency': 1}]
    return document


def _context(root: Path) -> ExecutionContext:
    return ExecutionContext(task_id='task', output_dir=str(root), runtime=ExternalRuntime(executable='/aiperf'))


def _run(root: Path, document: dict, **kwargs):
    return run_perf_plan(
        compile_plan_text(json.dumps(document)),
        _context(root),
        tokenizer=object(),
        token_pool=[1],
        tokenizer_id='tok',
        template_id='tpl',
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Quality rollup (Q01/Q02)
# ---------------------------------------------------------------------------


def test_a_completed_matrix_with_a_partial_run_stays_partial(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    plan = compile_plan_text(json.dumps(_document()))
    qualities = ['partial', 'partial', 'partial', 'partial']

    def fake_scenario(arguments, context, scenario, tokenizer, pool, cancel_event, on_phase=None):
        return CacheScenarioOutcome(
            status='completed', quality=qualities.pop(0), cleanup_confirmed=True, reason=None
        )

    monkeypatch.setattr(execution_module, 'run_cache_scenario', fake_scenario)
    outcome = _run(root, _document())
    assert outcome.status == 'completed'
    assert outcome.quality == 'partial'
    assert outcome.completed_runs == plan.run_count
    assert any(reason.endswith(':partial') for reason in outcome.quality_reasons)
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert manifest['status'] == 'completed' and manifest['quality'] == 'partial'
    assert manifest['quality_reasons']


def test_quality_rollup_never_promotes_a_partial_result() -> None:
    rows = [
        SimpleNamespace(status='completed', quality='partial'),
        SimpleNamespace(status='completed', quality='complete'),
    ]
    summary = summarize_plan_quality(
        requested_status='completed', runs=rows, reasons=['run-0:partial'], cleanup_confirmed=True, cancelled=False
    )
    assert summary.quality == 'partial'

    complete = summarize_plan_quality(
        requested_status='completed',
        runs=[SimpleNamespace(status='completed', quality='complete')],
        reasons=[],
        cleanup_confirmed=True,
        cancelled=False,
    )
    assert complete.quality == 'complete' and complete.status == 'completed'


def test_an_unconfirmed_cleanup_is_an_error_not_a_stop() -> None:
    summary = summarize_plan_quality(
        requested_status='stopped',
        runs=[SimpleNamespace(status='cancelled', quality='partial')],
        reasons=['cancelled'],
        cleanup_confirmed=False,
        cancelled=True,
    )
    assert summary.status == 'error'
    assert summary.quality == 'invalid'
    assert 'cleanup_unconfirmed' in summary.reasons


def test_an_unconfirmed_cleanup_after_a_completed_matrix_is_an_error() -> None:
    summary = summarize_plan_quality(
        requested_status='completed',
        runs=[SimpleNamespace(status='completed', quality='complete')],
        reasons=[],
        cleanup_confirmed=False,
        cancelled=False,
    )
    assert summary.status == 'error'
    assert summary.quality == 'invalid'
    assert 'cleanup_unconfirmed' in summary.reasons


def test_an_invalid_run_is_not_downgraded_by_a_cancellation() -> None:
    summary = summarize_plan_quality(
        requested_status='stopped',
        runs=[
            SimpleNamespace(status='completed', quality='invalid'),
            SimpleNamespace(status='not_started', quality=None),
        ],
        reasons=['run-0:invalid'],
        cleanup_confirmed=True,
        cancelled=True,
    )
    assert summary.status == 'stopped'
    assert summary.quality == 'invalid'
    assert 'run-0:invalid' in summary.reasons


# ---------------------------------------------------------------------------
# Cancellation and imports (F06)
# ---------------------------------------------------------------------------


def test_a_cancelled_matrix_keeps_an_invalid_run_invalid(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()

    class CancelAfterFirstRun:
        runs = 0

        def is_set(self) -> bool:
            return self.runs >= 1

    cancel = CancelAfterFirstRun()

    def fake_scenario(*args, **kwargs):
        cancel.runs += 1
        return CacheScenarioOutcome(
            status='completed', quality='invalid', cleanup_confirmed=True, reason=None
        )

    monkeypatch.setattr(execution_module, 'run_cache_scenario', fake_scenario)
    outcome = _run(root, _document(), cancel_event=cancel)
    assert outcome.status == 'stopped'
    assert outcome.quality == 'invalid'
    assert outcome.completed_runs == 1
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert manifest['status'] == 'stopped' and manifest['quality'] == 'invalid'
    assert any(reason.endswith(':invalid') for reason in manifest['quality_reasons'])
    assert 'cancelled' in manifest['quality_reasons']


def test_a_cancelled_synthetic_run_keeps_a_partial_import(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    document = _synthetic_document()
    raw = tmp_path / 'raw'
    raw.mkdir()

    monkeypatch.setattr(
        execution_module,
        'run_synthetic',
        lambda spec, context, cancel_event: SimpleNamespace(
            exit_reason='cancelled',
            artifact_status='available',
            raw_artifact_dir=str(raw),
            executor_version='0.13.0',
            cleanup_confirmed=True,
        ),
    )
    imported: List[dict] = []

    def fake_import(arguments, cancel_event=None):
        imported.append({'raw': arguments.raw_dir, 'partial': arguments.allow_partial_tail})
        return SimpleNamespace(quality='partial', quality_reasons=['partial_tail'])

    monkeypatch.setattr(execution_module, 'import_aiperf_run', fake_import)
    outcome = run_perf_plan(compile_plan_text(json.dumps(document)), _context(root))
    assert outcome.status == 'stopped'
    assert outcome.quality == 'partial'
    assert imported and imported[0]['partial'] is True
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert manifest['status'] == 'stopped'
    assert [row['status'] for row in manifest['runs']] == ['cancelled']
    assert [row['quality'] for row in manifest['runs']] == ['partial']


def test_an_import_cancelled_by_the_user_is_a_stopped_run(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()

    def raising(*args, **kwargs):
        raise ImportCancelled()

    monkeypatch.setattr(execution_module, 'run_cache_scenario', raising)
    outcome = _run(root, _document())
    assert outcome.status == 'stopped'
    assert outcome.quality == 'partial'
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert manifest['runs'][0]['status'] == 'cancelled'


def test_a_failed_partial_import_does_not_claim_metrics(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    document = _synthetic_document()
    monkeypatch.setattr(
        execution_module,
        'run_synthetic',
        lambda spec, context, cancel_event: SimpleNamespace(
            exit_reason='cancelled',
            artifact_status='available',
            raw_artifact_dir=str(tmp_path),
            executor_version='0.13.0',
            cleanup_confirmed=True,
        ),
    )
    monkeypatch.setattr(
        execution_module,
        'import_aiperf_run',
        lambda *args, **kwargs: (_ for _ in ()).throw(ImportCancelled()),
    )
    outcome = run_perf_plan(compile_plan_text(json.dumps(document)), _context(root))
    assert outcome.status == 'stopped'
    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    row = manifest['runs'][0]
    assert row['normalized'] is None
    assert row['reason'] == 'partial_import_unavailable'


# ---------------------------------------------------------------------------
# Artifact layout and identity (F01/F08)
# ---------------------------------------------------------------------------


def test_the_archive_lives_under_the_perf_root(tmp_path: Path, monkeypatch) -> None:
    perf_root = tmp_path / 'task' / 'perf'
    perf_root.mkdir(parents=True)
    monkeypatch.setattr(
        execution_module,
        'run_cache_scenario',
        lambda *args, **kwargs: CacheScenarioOutcome(
            status='completed', quality='partial', cleanup_confirmed=True
        ),
    )
    outcome = _run(perf_root, _document())
    assert outcome.status == 'completed'
    assert (perf_root / 'plan' / 'compiled.json').is_file()
    assert (perf_root / 'runs').is_dir()
    directory, legacy = resolve_plan_dir(perf_root.parent)
    assert directory == perf_root / 'plan' and legacy is False
    assert read_plan_hash(perf_root) == outcome.plan_hash


def test_a_second_execution_of_the_same_directory_is_refused(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    monkeypatch.setattr(
        execution_module,
        'run_cache_scenario',
        lambda *args, **kwargs: CacheScenarioOutcome(
            status='completed', quality='partial', cleanup_confirmed=True
        ),
    )
    _run(root, _document())
    with pytest.raises(ValueError):
        _run(root, _document())
    assert len([name for name in os.listdir(root / 'runs')]) == 4


def test_effective_identity_records_what_actually_ran(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'task'
    root.mkdir()

    def fake_scenario(arguments, context, scenario, tokenizer, pool, cancel_event, on_phase=None):
        return CacheScenarioOutcome(
            status='completed',
            quality='partial',
            cleanup_confirmed=True,
            execution=ExecutionOutcome(
                exit_reason='completed', cleanup_confirmed=True, artifact_status='available', executor_version='0.13.0'
            ),
        )

    monkeypatch.setattr(execution_module, 'run_cache_scenario', fake_scenario)
    _run(root, _document())
    effective = read_effective_identity(root)
    assert effective is not None
    assert effective.tokenizer.status == 'resolved'
    assert effective.tokenizer.content_hash == 'tok'
    assert effective.tokenizer.template_hash == 'tpl'
    assert effective.aiperf_version == '0.13.0'
    assert effective.identity_version == '1'
    assert effective.deployment_evidence == 'declared'


def test_a_reservation_is_adopted_without_rewriting_the_plan_hash(tmp_path: Path, monkeypatch) -> None:
    from evalscope.perf.plans.provenance import reserve_submission

    perf_root = tmp_path / 'task' / 'perf'
    perf_root.mkdir(parents=True)
    plan = compile_plan_text(json.dumps(_document()))
    submission = reserve_submission(perf_root, plan, task_id='task', source_text=json.dumps(_document()))
    assert submission.state == 'reserved'
    assert read_submission(perf_root).plan_hash == plan.plan_hash

    monkeypatch.setattr(
        execution_module,
        'run_cache_scenario',
        lambda *args, **kwargs: CacheScenarioOutcome(
            status='completed', quality='partial', cleanup_confirmed=True
        ),
    )
    outcome = _run(perf_root, _document())
    assert outcome.plan_hash == plan.plan_hash
    archived = json.loads((perf_root / 'plan' / 'compiled.json').read_text())
    assert archived['plan_hash'] == plan.plan_hash


# ---------------------------------------------------------------------------
# Semantic identity (I01-I06)
# ---------------------------------------------------------------------------


def test_the_matrix_size_is_rejected_before_expansion() -> None:
    document = _document()
    document['load']['repetitions'] = 5000
    assert planned_run_count(compile_plan_text(json.dumps(_document())).normalized) == 4
    with pytest.raises(PlanParseError) as error:
        compile_plan_text(json.dumps(document))
    assert error.value.code == 'too_many_runs'
    assert '20000' in error.value.message


def test_task_identity_and_tokenizer_path_do_not_change_semantics() -> None:
    first = _document()
    second = _document()
    second['name'] = 'another name'
    second['connection']['tokenizer_path'] = '/elsewhere/tokenizer'
    left = compile_plan_text(json.dumps(first))
    right = compile_plan_text(json.dumps(second))
    assert left.plan_hash != right.plan_hash
    assert left.semantic_fingerprint == right.semantic_fingerprint
    assert compare_plan_semantics(left, right).status == 'matched'


def test_deployment_facts_change_semantics_with_a_readable_reason() -> None:
    first = _document()
    second = _document()
    second['deployment']['model_revision'] = 'rev-2'
    left = compile_plan_text(json.dumps(first))
    right = compile_plan_text(json.dumps(second))
    assert left.semantic_fingerprint != right.semantic_fingerprint
    comparison = compare_plan_semantics(left, right)
    assert comparison.status == 'different'
    assert any('model revision' in reason for reason in comparison.reasons)


def _effective(tokenizer_hash: str | None, template_hash: str | None, version: str | None = '0.13.0'):
    return EffectiveIdentity(
        plan_hash='a' * 64,
        contract_version='1.0',
        compiler_version='1.1',
        tokenizer=TokenizerIdentity(
            status='resolved' if tokenizer_hash else 'unknown',
            content_hash=tokenizer_hash,
            template_hash=template_hash,
            path='/models/tokenizer',
        ),
        aiperf_version=version,
        deployment_evidence='declared',
    )


def test_same_path_with_different_content_is_not_matched() -> None:
    comparison = compare_effective_identities(_effective('aaa', 'ttt'), _effective('bbb', 'ttt'))
    assert comparison.status == 'different'


def test_unresolved_identity_is_unknown_not_matched() -> None:
    assert compare_effective_identities(_effective(None, None), _effective(None, None)).status == 'unknown'
    assert compare_effective_identities(_effective('aaa', 'ttt'), _effective('aaa', 'ttt', None)).status == 'unknown'


def test_identity_comparison_matches_only_fully_resolved_records() -> None:
    assert compare_effective_identities(_effective('aaa', 'ttt'), _effective('aaa', 'ttt')).status == 'matched'


# ---------------------------------------------------------------------------
# Progress (U01)
# ---------------------------------------------------------------------------


def test_progress_uses_real_run_counts(tmp_path: Path) -> None:
    from evalscope.perf.plans.execution import PerfPlanManifest, PlanRunState
    from evalscope.service.perf_plans import plan_progress

    perf_root = tmp_path / 'task' / 'perf'
    plan = compile_plan_text(json.dumps(_document()))
    (perf_root / 'plan').mkdir(parents=True)
    (perf_root / 'plan' / 'compiled.json').write_text(plan.model_dump_json())
    rows = [
        PlanRunState(
            run_id=run.run_id,
            order=run.order,
            workload_kind=run.workload_kind,
            message_structure=run.message_structure,
            concurrency=run.concurrency,
            formal_requests=run.request_count,
        )
        for run in plan.runs
    ]
    rows[0].status = 'completed'
    manifest = PerfPlanManifest(
        plan_hash=plan.plan_hash,
        normalized_hash=plan.normalized_hash,
        semantic_fingerprint=plan.semantic_fingerprint,
        workload_semantics=plan.workload_semantics,
        status='running',
        phase='profiling',
        current_run=rows[1].run_id,
        completed_runs=1,
        total_runs=4,
        runs=rows,
    )
    (perf_root / 'plan' / 'manifest.json').write_text(manifest.model_dump_json())

    progress = plan_progress(perf_root)
    assert progress is not None
    assert progress['percent'] == 25.0
    assert progress['completed_subruns'] == 1
    assert progress['total_subruns'] == 4
    assert progress['current_subrun'] == rows[1].run_id
    assert progress['phase'] == 'profiling'

    manifest.status = 'completed'
    manifest.completed_runs = 4
    manifest.phase = None
    manifest.current_run = None
    (perf_root / 'plan' / 'manifest.json').write_text(manifest.model_dump_json())
    assert plan_progress(perf_root)['percent'] == 100.0


def test_the_primer_timeout_from_the_plan_is_forwarded(tmp_path: Path, monkeypatch) -> None:
    """F03: the normalized priming timeout is the value the scenario uses."""
    root = tmp_path / 'task'
    root.mkdir()
    captured: dict = {}

    def fake_scenario(arguments, context, scenario, tokenizer, pool, cancel_event, on_phase=None):
        captured['timeout'] = scenario.primer_timeout_seconds
        return CacheScenarioOutcome(status='completed', quality='partial', cleanup_confirmed=True)

    monkeypatch.setattr(execution_module, 'run_cache_scenario', fake_scenario)
    document = _document()
    document['priming']['timeout_seconds'] = 45
    _run(root, document)
    assert captured['timeout'] == 45


def test_a_failed_run_logs_its_run_id_without_prompt_text(tmp_path: Path, monkeypatch, caplog) -> None:
    """L01: the worker log names the run and the failure class, never the payload."""
    root = tmp_path / 'task'
    root.mkdir()
    plan = compile_plan_text(json.dumps(_document()))

    def raising(*args, **kwargs):
        raise RuntimeError('probe failed')

    monkeypatch.setattr(execution_module, 'run_cache_scenario', raising)
    with caplog.at_level('ERROR'):
        outcome = _run(root, _document())
    assert outcome.status == 'error'
    messages = ' '.join(record.getMessage() for record in caplog.records)
    assert plan.runs[0].run_id in messages
    assert 'RuntimeError' in messages
    assert 'prompt' not in messages.lower()
