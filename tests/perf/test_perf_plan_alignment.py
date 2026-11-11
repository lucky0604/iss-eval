"""Legacy migration facts and per-dimension alignment, without claiming equivalence."""

import json
from pathlib import Path

from evalscope.perf.plans import compile_plan, legacy_source_facts, plan_from_legacy, preflight_plan
from evalscope.perf.plans.legacy import LegacySnapshot

FIXTURES = Path(__file__).parent / 'fixtures' / 'perf_plan'


def _snapshot() -> LegacySnapshot:
    return LegacySnapshot.model_validate(json.loads((FIXTURES / 'legacy-snapshot.json').read_text()))


def test_snapshot_keeps_measured_integers_and_the_unknown_version() -> None:
    facts = legacy_source_facts(_snapshot())
    assert facts.input_lengths == [262_000, 1_000_000]
    assert facts.concurrencies == [1, 4, 8, 16, 32, 64, 128]
    assert facts.request_count == 10 and facts.output_tokens == 1000
    assert facts.target_prefix_ratio == 0.9
    assert facts.runtime_version is None
    assert [(case['shared'], case['unique']) for case in facts.awk_cases] == [
        (235800, 26199),
        (900000, 99999),
    ]


def test_converted_plan_reproduces_the_source_parameters() -> None:
    snapshot = _snapshot()
    facts = legacy_source_facts(snapshot)
    plan = plan_from_legacy(snapshot, model=facts.model, url=facts.url, tokenizer_path=facts.tokenizer)
    outcome = preflight_plan(compile_plan(plan), source=facts)
    parameters = outcome.alignment.by_dimension['parameters']
    assert parameters.status == 'matched', parameters.reasons
    assert outcome.alignment.overall == 'unknown'
    for dimension in ('request_semantics', 'cache', 'environment', 'version', 'statistics'):
        assert outcome.alignment.by_dimension[dimension].status == 'unknown'
    assert outcome.alignment.by_dimension['cache'].reasons


def test_conflicting_deployment_document_blocks_an_equivalence_claim() -> None:
    snapshot = _snapshot()
    facts = legacy_source_facts(snapshot)
    deployment = snapshot.deployment_document
    plan = plan_from_legacy(
        snapshot, model=deployment['model'], url=deployment['endpoint'], tokenizer_path=facts.tokenizer
    )
    outcome = preflight_plan(compile_plan(plan), source=facts)
    parameters = outcome.alignment.by_dimension['parameters']
    assert parameters.status == 'different'
    assert any('model' in reason for reason in parameters.reasons)
    assert any('endpoint' in reason for reason in parameters.reasons)
    codes = {item.code for item in outcome.plan.diagnostics}
    assert {'legacy_source_differs', 'legacy_version_unknown'} <= codes
    assert 'legacy_source_differs' in outcome.requires_acknowledgement


def test_legacy_plan_is_rejected_for_context_even_after_conversion() -> None:
    snapshot = _snapshot()
    facts = legacy_source_facts(snapshot)
    plan = plan_from_legacy(snapshot, model=facts.model, url=facts.url, tokenizer_path=facts.tokenizer)
    outcome = preflight_plan(compile_plan(plan), source=facts)
    assert outcome.can_submit is False
    overflow = [item for item in outcome.plan.diagnostics if item.code == 'context_overflow']
    assert {item.case_id for item in overflow} == {'legacy-262000', 'legacy-1000000'}
    assert outcome.plan.budgets.formal_requests == 140
    assert outcome.plan.run_count == 14


def test_standalone_plan_never_reports_a_matched_dimension() -> None:
    from evalscope.perf.plans import compile_plan_text

    plan = compile_plan_text((FIXTURES / 'managed-small.json').read_text())
    outcome = preflight_plan(plan)
    assert {item.status for item in outcome.alignment.dimensions} == {'unknown'}
    assert outcome.alignment.overall == 'unknown'
    assert outcome.alignment.source is None
