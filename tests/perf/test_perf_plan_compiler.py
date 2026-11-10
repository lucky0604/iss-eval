"""Matrix expansion, budgets, ordering, context preflight and acknowledgement scope."""

import json
from pathlib import Path

import pytest

from evalscope.perf.plans import compile_plan_text, preflight_plan

FIXTURES = Path(__file__).parent / 'fixtures' / 'perf_plan'


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


def _document(name: str) -> dict:
    return json.loads(_text(name))


def test_managed_matrix_expands_load_input_ratio_repetition() -> None:
    plan = compile_plan_text(_text('managed-small.json'))
    document = _document('managed-small.json')
    expected = (
        len(document['load']['points'])
        * len(document['workload']['input_lengths'])
        * len(document['workload']['target_ratios'])
        * document['load']['repetitions']
    )
    assert plan.run_count == expected
    assert plan.budgets.formal_requests == expected * document['load']['request_count']
    assert plan.budgets.primer_requests == expected
    assert plan.budgets.formal_output_tokens == plan.budgets.formal_requests * document['load']['output_token_limit']
    assert plan.budgets.total_requests == (
        plan.budgets.formal_requests + plan.budgets.warmup_requests + plan.budgets.primer_requests
    )
    assert {run.message_structure for run in plan.runs} == {'user_only'}


def test_managed_order_is_load_then_input_for_a_declared_matrix() -> None:
    document = _document('managed-small.json')
    document['execution'] = {'order': 'load_then_input'}
    document['load']['points'] = [{'concurrency': 2}, {'concurrency': 8}]
    document['workload']['input_lengths'] = [1024, 2048]
    document['workload']['target_ratios'] = [0.5, 0.9]
    plan = compile_plan_text(json.dumps(document))
    concurrencies = [run.concurrency for run in plan.runs]
    assert concurrencies == [2, 2, 2, 2, 8, 8, 8, 8]
    orders = [run.order for run in plan.runs]
    assert orders == list(range(len(orders)))


def test_seeded_shuffle_is_reproducible_and_changes_with_the_seed() -> None:
    document = _document('managed-small.json')
    document['execution'] = {'order': 'seeded_shuffle', 'seed': 7}
    first = compile_plan_text(json.dumps(document))
    second = compile_plan_text(json.dumps(document))
    assert [run.run_id for run in first.runs] == [run.run_id for run in second.runs]
    document['execution']['seed'] = 8
    third = compile_plan_text(json.dumps(document))
    assert [run.run_id for run in third.runs] != [run.run_id for run in first.runs]
    assert sorted(run.order for run in third.runs) == list(range(third.run_count))


def test_plan_hash_is_stable_and_semantic_fingerprint_ignores_the_display_name() -> None:
    text = _text('managed-small.json')
    first = compile_plan_text(text)
    assert compile_plan_text(text).plan_hash == first.plan_hash

    document = _document('managed-small.json')
    document['name'] = 'Renamed but semantically identical'
    renamed = compile_plan_text(json.dumps(document))
    assert renamed.normalized_hash != first.normalized_hash
    assert renamed.plan_hash != first.plan_hash
    assert renamed.semantic_fingerprint == first.semantic_fingerprint

    document['workload']['target_ratios'] = [0.5]
    changed = compile_plan_text(json.dumps(document))
    assert changed.semantic_fingerprint != first.semantic_fingerprint
    assert changed.plan_hash != first.plan_hash


def _synthetic(with_seed: bool = False) -> dict:
    document = _document('synthetic-small.json')
    document['warmup']['enabled'] = False
    if with_seed:
        document['execution'] = {'order': 'load_then_input', 'seed': 7}
    return document


def test_quality_policy_changes_the_plan_identity_but_not_the_request_semantics() -> None:
    for with_seed in (False, True):
        base = _synthetic(with_seed=with_seed)
        strict = json.loads(json.dumps(base))
        strict['workload']['ratio_tolerance'] = 0.2
        strict['workload']['require_observed_ratio'] = True

        relaxed_plan = compile_plan_text(json.dumps(base))
        strict_plan = compile_plan_text(json.dumps(strict))
        assert strict_plan.normalized_hash != relaxed_plan.normalized_hash
        assert strict_plan.plan_hash != relaxed_plan.plan_hash
        assert [run.seed for run in strict_plan.runs] == [run.seed for run in relaxed_plan.runs]
        assert strict_plan.semantic_fingerprint == relaxed_plan.semantic_fingerprint


def test_request_parameters_still_change_the_seed_and_the_fingerprint() -> None:
    base = _synthetic()
    first = compile_plan_text(json.dumps(base))

    split = json.loads(json.dumps(base))
    split['workload']['cases'][0]['shared_system_tokens'] = 800
    split['workload']['cases'][0]['unique_user_tokens'] = 200
    second = compile_plan_text(json.dumps(split))
    assert [run.seed for run in second.runs] != [run.seed for run in first.runs]
    assert second.semantic_fingerprint != first.semantic_fingerprint

    longer_output = json.loads(json.dumps(base))
    longer_output['load']['output_token_limit'] = 64
    third = compile_plan_text(json.dumps(longer_output))
    assert [run.seed for run in third.runs] != [run.seed for run in first.runs]
    assert third.semantic_fingerprint != first.semantic_fingerprint


def test_run_ids_and_namespaces_are_deterministic_per_plan_content() -> None:
    plan = compile_plan_text(_text('managed-small.json'))
    run_ids = [run.run_id for run in plan.runs]
    assert len(set(run_ids)) == len(run_ids)
    assert all(run.namespace and len(run.namespace) == 64 for run in plan.runs)


def test_legacy_example_is_hard_rejected_for_context_overflow() -> None:
    plan = compile_plan_text(_text('legacy-context-rejected.json'))
    outcome = preflight_plan(plan)
    assert outcome.can_submit is False
    overflow = [item for item in outcome.plan.diagnostics if item.code == 'context_overflow']
    assert len(overflow) == 2
    cases = {item.case_id for item in overflow}
    assert cases == {'legacy-262000', 'legacy-1000000'}
    assert all(
        item.parameters['required_tokens'] > item.parameters['limit_tokens'] for item in overflow
    )
    assert any(item.code == 'request_count_below_concurrency' for item in outcome.plan.diagnostics)
    assert outcome.plan.runs[0].message_structure == 'system_and_user'
    assert outcome.plan.budgets.formal_requests == 140


def test_request_count_below_concurrency_blocks_benchmark_but_only_warns_for_diagnostics() -> None:
    document = _document('managed-small.json')
    document['intent'] = 'benchmark'
    document['load']['request_count'] = 2
    document['load']['points'] = [{'concurrency': 8}]
    blocked = preflight_plan(compile_plan_text(json.dumps(document)))
    codes = {item.code: item.severity for item in blocked.plan.diagnostics}
    assert codes['request_count_below_concurrency'] == 'error'
    assert blocked.can_submit is False

    document['intent'] = 'diagnostic'
    allowed = preflight_plan(compile_plan_text(json.dumps(document)))
    codes = {item.code: item.severity for item in allowed.plan.diagnostics}
    assert codes['request_count_below_concurrency'] == 'warning'
    assert allowed.can_submit is True
    assert 'request_count_below_concurrency' in allowed.requires_acknowledgement


def test_unknown_context_limit_blocks_any_capacity_claim() -> None:
    document = _document('managed-small.json')
    document['deployment'].pop('declared_max_model_len')
    outcome = preflight_plan(compile_plan_text(json.dumps(document)))
    assert any(item.code == 'context_limit_unknown' and item.severity == 'error' for item in outcome.plan.diagnostics)
    assert outcome.can_submit is False


def test_managed_context_uses_input_plus_output_and_reports_the_basis() -> None:
    document = _document('managed-small.json')
    document['deployment']['declared_max_model_len'] = 100
    outcome = preflight_plan(compile_plan_text(json.dumps(document)))
    assert [item.status for item in outcome.context] == ['overflow']
    assert {item.required_tokens for item in outcome.context} == {1056}
    assert all(item.overhead == 'included' for item in outcome.context)


def test_synthetic_context_is_a_lower_bound_without_template_overhead() -> None:
    plan = compile_plan_text(_text('synthetic-small.json'))
    unknown = preflight_plan(plan)
    formal = [item for item in unknown.context if item.run_id != 'warmup']
    assert {item.status for item in formal} == {'unknown'}
    assert {item.overhead for item in formal} == {'unknown'}
    # Warmup is rendered locally, so its length is exact even for synthetic plans.
    assert [item.status for item in unknown.context if item.run_id == 'warmup'] == ['fits']
    assert 'synthetic_semantics_unverified' in unknown.requires_acknowledgement

    known = preflight_plan(plan, template_overhead=10)
    formal = [item for item in known.context if item.run_id != 'warmup']
    assert {item.status for item in formal} == {'fits'}
    assert {item.required_tokens for item in formal} == {1042}


def test_warmup_context_is_checked_against_the_declared_limit() -> None:
    """F04: 8192 limit with 16000+1000 warmup is rejected; exactly fitting is accepted."""
    import json

    document = _document('managed-small.json')
    document['warmup'] = {
        'enabled': True,
        'input_tokens': 16000,
        'output_token_limit': 1000,
        'request_count': 1,
        'concurrency': 1,
        'failure_policy': 'abort',
    }
    rejected = preflight_plan(compile_plan_text(json.dumps(document)))
    warmup = next(item for item in rejected.context if item.run_id == 'warmup')
    assert warmup.status == 'overflow'
    assert warmup.required_tokens == 17000
    assert any(
        item.code == 'context_overflow' and item.field_path == 'warmup.input_tokens'
        for item in rejected.plan.diagnostics
    )
    assert rejected.can_submit is False

    document['warmup']['input_tokens'] = 8192 - 1000
    exact = next(
        item for item in preflight_plan(compile_plan_text(json.dumps(document))).context if item.run_id == 'warmup'
    )
    assert exact.status == 'fits' and exact.required_tokens == 8192

    document['warmup']['input_tokens'] += 1
    over = next(
        item for item in preflight_plan(compile_plan_text(json.dumps(document))).context if item.run_id == 'warmup'
    )
    assert over.status == 'overflow' and over.required_tokens == 8193

    document['warmup']['enabled'] = False
    disabled = preflight_plan(compile_plan_text(json.dumps(document)))
    assert all(item.run_id != 'warmup' for item in disabled.context)


def test_output_budget_equal_to_the_context_limit_is_rejected() -> None:
    document = _document('managed-small.json')
    document['load']['output_token_limit'] = document['deployment']['declared_max_model_len']
    outcome = preflight_plan(compile_plan_text(json.dumps(document)))
    assert any(item.code == 'output_budget_exceeded' for item in outcome.plan.diagnostics)
    assert outcome.can_submit is False


def test_legacy_rounding_difference_keeps_explicit_integers() -> None:
    plan = compile_plan_text(_text('legacy-context-rejected.json'))
    outcome = preflight_plan(plan)
    warnings = [item for item in outcome.plan.diagnostics if item.code == 'legacy_rounding_difference']
    assert len(warnings) == 2
    assert {item.parameters['difference'] for item in warnings} == {-1}
    assert {item.parameters['declared_total'] for item in warnings} == {262_000, 1_000_000}
    assert 'legacy_rounding_difference' in outcome.requires_acknowledgement


def test_export_round_trip_preserves_the_normalized_hash() -> None:
    document = _document('managed-small.json')
    first = compile_plan_text(json.dumps(document))
    exported = json.dumps(first.normalized.model_dump(mode='json'), indent=2, sort_keys=True, ensure_ascii=False)
    again = compile_plan_text(exported)
    assert again.normalized_hash == first.normalized_hash
    assert again.plan_hash == first.plan_hash


def test_warmup_budget_is_reported_separately_from_formal_requests() -> None:
    document = _document('managed-small.json')
    document['warmup'] = {
        'enabled': True,
        'request_count': 3,
        'input_tokens': 16000,
        'output_token_limit': 1000,
        'concurrency': 1,
        'failure_policy': 'record_and_continue',
    }
    outcome = preflight_plan(compile_plan_text(json.dumps(document)))
    budgets = outcome.plan.budgets
    assert budgets.warmup_requests == 3
    assert budgets.warmup_output_tokens == 3000
    assert budgets.formal_requests == 32
    assert budgets.total_requests == budgets.formal_requests + 3 + budgets.primer_requests
    assert 'warmup_budget_large' in {item.code for item in outcome.plan.diagnostics}
    assert 'warmup_budget_large' in outcome.requires_acknowledgement


def test_plan_expansion_limit_is_explicit() -> None:
    document = _document('managed-small.json')
    document['workload']['input_lengths'] = list(range(1000, 1010))
    document['workload']['target_ratios'] = [0.1, 0.2, 0.3, 0.4, 0.5]
    document['load']['points'] = [{'concurrency': value} for value in range(1, 11)]
    document['load']['repetitions'] = 200
    from evalscope.perf.plans import PlanParseError

    with pytest.raises(PlanParseError) as error:
        compile_plan_text(json.dumps(document))
    assert error.value.code == 'too_many_runs'


def test_prefix_pool_larger_than_the_request_count_is_blocked() -> None:
    document = _document('managed-small.json')
    document['workload']['prefix_pool_size'] = 32
    document['load']['request_count'] = 8
    outcome = preflight_plan(compile_plan_text(json.dumps(document)))
    assert outcome.can_submit is False
    assert any(item.code == 'invalid_value' and 'prefix pool' in item.message for item in outcome.plan.diagnostics)
