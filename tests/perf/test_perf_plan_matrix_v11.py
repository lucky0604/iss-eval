"""Schema 1.1 matrix contract: output-length axis, load mode, budgets and identity."""

import json
from pathlib import Path

import pytest

from evalscope.perf.plans import (
    PLAN_SCHEMA_VERSION,
    PlanParseError,
    compile_plan_text,
    preflight_plan,
)

FIXTURES = Path(__file__).parent / 'fixtures' / 'perf_plan'


def _matrix() -> dict:
    return json.loads((FIXTURES / 'matrix-osl-load.json').read_text())


def _text(document: dict) -> str:
    return json.dumps(document)


def _compile(document: dict):
    return compile_plan_text(_text(document))


# M01: the full declared matrix expands to one run per combination.
def test_matrix_expands_input_output_ratio_and_concurrency() -> None:
    plan = _compile(_matrix())
    assert plan.contract_version == PLAN_SCHEMA_VERSION == '1.1'
    assert plan.compiler_version == '1.3'
    assert plan.run_count == 4 * 2 * 2 * 5 * 1
    assert len({run.run_id for run in plan.runs}) == plan.run_count

    dimensions = {(run.input_tokens, run.output_token_limit, run.target_ratio, run.concurrency) for run in plan.runs}
    assert dimensions == {
        (input_tokens, output_tokens, ratio, concurrency)
        for input_tokens in (1024, 2048, 4096, 8192)
        for output_tokens in (128, 1000)
        for ratio in (0.0, 0.9)
        for concurrency in (1, 4, 8, 16, 32)
    }
    assert all(run.load_mode == 'closed_loop' and run.rate is None for run in plan.runs)
    # The output index is part of the run id, so two output lengths never collide.
    assert {'i000-o000-r000-l000-n000', 'i000-o001-r000-l000-n000'} <= {run.run_id for run in plan.runs}


def test_same_seed_and_order_reproduce_the_matrix() -> None:
    first = _compile(_matrix())
    second = _compile(_matrix())
    assert [run.run_id for run in first.runs] == [run.run_id for run in second.runs]
    assert [run.order for run in first.runs] == list(range(first.run_count))
    assert first.plan_hash == second.plan_hash


# M02: the output-token ceiling sums N×OSL per run, never N×first OSL.
def test_output_budget_sums_each_run_output_length() -> None:
    plan = _compile(_matrix())
    budgets = plan.budgets
    assert budgets.formal_requests == 80 * 128
    assert budgets.primer_requests == 40
    assert budgets.total_requests == budgets.formal_requests + budgets.primer_requests
    assert budgets.formal_output_tokens == 80 * 128 * (128 + 1000) // 2
    assert budgets.formal_output_tokens != budgets.formal_requests * 128


# M03: 10000 runs are accepted, anything above is rejected before runs are allocated.
def test_run_limit_accepts_10000_and_rejects_more_before_expansion(monkeypatch) -> None:
    from evalscope.perf.plans import compiler

    document = _matrix()
    document['execution']['order'] = 'load_then_input'
    document['workload']['input_lengths'] = list(range(1000, 1025))
    document['load']['repetitions'] = 20  # 25 ISL × 2 OSL × 2 ratio × 5 points × 20 = 10000
    accepted = _compile(document)
    assert accepted.run_count == 10000

    document['load']['repetitions'] = 21  # 10500
    calls: list[str] = []

    def fail_expansion(*args, **kwargs):
        calls.append('expanded')
        raise AssertionError('run expansion ran before the limit was checked')

    monkeypatch.setattr(compiler, '_managed_members', fail_expansion)
    with pytest.raises(PlanParseError) as error:
        _compile(document)
    assert error.value.code == 'too_many_runs'
    assert calls == []


# M04 (backend half): invalid output-length items are rejected with their field path.
@pytest.mark.parametrize(
    'limits',
    [[], ['128'], [1.5], [True], [0], [-5], [128, 128], [128, None]],
)
def test_invalid_output_token_limits_are_rejected(limits) -> None:
    document = _matrix()
    document['load']['output_token_limits'] = limits
    with pytest.raises(PlanParseError) as error:
        _compile(document)
    assert error.value.code == 'invalid_value'
    assert 'load.output_token_limits' in error.value.field_path


def test_output_token_limits_axis_is_bounded_to_64() -> None:
    document = _matrix()
    document['load']['output_token_limits'] = list(range(1, 66))
    with pytest.raises(PlanParseError) as error:
        _compile(document)
    assert error.value.code == 'invalid_value'


# M05: context capacity is judged for every input/output combination.
def test_context_overflow_names_the_offending_output_length() -> None:
    document = _matrix()
    document['deployment']['declared_max_model_len'] = 9000
    outcome = preflight_plan(_compile(document))
    overflow = [item for item in outcome.plan.diagnostics if item.code == 'context_overflow']
    assert len(overflow) == 1
    assert overflow[0].parameters['input_tokens'] == 8192
    assert overflow[0].parameters['output_token_limit'] == 1000
    assert overflow[0].parameters['required_tokens'] == 9192
    assert outcome.can_submit is False
    # Both output lengths stay visible per input length; a first-limit-only check
    # would have reported 4 checks and missed the overflow.
    checks = {(item.required_tokens, item.output_token_limit, item.status) for item in outcome.context}
    assert checks == {(input_tokens + output, output, 'fits' if input_tokens + output <= 9000 else 'overflow')
                      for input_tokens in (1024, 2048, 4096, 8192) for output in (128, 1000)}


def test_output_budget_is_checked_per_output_length() -> None:
    document = _matrix()
    document['deployment']['declared_max_model_len'] = 1000
    outcome = preflight_plan(_compile(document))
    exceeded = [item for item in outcome.plan.diagnostics if item.code == 'output_budget_exceeded']
    assert len(exceeded) == 1
    assert exceeded[0].parameters['output_token_limit'] == 1000
    assert exceeded[0].field_path == 'load.output_token_limits'


# M06: closed and open loop are mutually exclusive per point.
def test_load_mode_and_point_fields_are_mutually_exclusive() -> None:
    document = _matrix()
    document['load']['points'] = [{'concurrency': 1, 'rate': 1.0}]
    with pytest.raises(PlanParseError) as mixed:
        _compile(document)
    assert mixed.value.code == 'invalid_value'
    assert 'load' in mixed.value.field_path
    assert 'points[0]' in mixed.value.message

    document = _matrix()
    document['load']['mode'] = 'open_loop'
    with pytest.raises(PlanParseError) as wrong_mode:
        _compile(document)
    assert wrong_mode.value.code == 'invalid_value'
    assert 'load' in wrong_mode.value.field_path
    assert 'points[0]' in wrong_mode.value.message

    for rate in (0, -1, True, '1.0', float('nan'), float('inf')):
        document = _matrix()
        document['load']['mode'] = 'open_loop'
        document['load']['points'] = [{'rate': rate}]
        with pytest.raises(PlanParseError):
            _compile(document)


def test_open_loop_runs_carry_rate_and_no_concurrency() -> None:
    document = _matrix()
    document['load']['mode'] = 'open_loop'
    document['load']['points'] = [{'rate': 0.5}, {'rate': 2.0}]
    plan = _compile(document)
    assert plan.run_count == 4 * 2 * 2 * 2
    assert {run.rate for run in plan.runs} == {0.5, 2.0}
    assert {run.load_mode for run in plan.runs} == {'open_loop'}
    assert all(run.concurrency is None for run in plan.runs)


def test_duplicate_load_points_are_rejected_in_both_modes() -> None:
    document = _matrix()
    document['load']['points'] = [{'concurrency': 4}, {'concurrency': 4}]
    with pytest.raises(PlanParseError) as closed:
        _compile(document)
    assert 'load point' in closed.value.message

    document = _matrix()
    document['load']['mode'] = 'open_loop'
    document['load']['points'] = [{'rate': 1.5}, {'rate': 1.5}]
    with pytest.raises(PlanParseError) as opened:
        _compile(document)
    assert 'load point' in opened.value.message


# M07: sample adequacy compares request count with concurrency only in a closed loop.
def test_sample_adequacy_applies_to_closed_loop_only() -> None:
    document = _matrix()
    document['load']['request_count'] = 2
    document['load']['points'] = [{'concurrency': 8}]
    blocked = preflight_plan(_compile(document))
    assert blocked.can_submit is False
    assert any(item.code == 'request_count_below_concurrency' for item in blocked.plan.diagnostics)

    document['load']['mode'] = 'open_loop'
    document['load']['points'] = [{'rate': 8.0}]
    opened = preflight_plan(_compile(document))
    codes = {item.code for item in opened.plan.diagnostics}
    assert 'request_count_below_concurrency' not in codes
    assert 'sample_budget_small' not in codes


# M08: frozen 1.0 documents keep their golden identity and compiler version.
def test_schema_10_identity_is_frozen() -> None:
    golden = {
        'managed-small.json': (
            '156ecd0bfbb6af5fe624224b89954961d0910927f4d42d1b4fd9e6b1eea0285a',
            '5bfe955c1f0e25627f179884ec09bc57764f5431d75489f81b0b124dd52708cb',
            '2f99283edd4dbe2a72db3f3e83aee546a3226df10578071fa9809179379bb0b0',
        ),
        'synthetic-small.json': (
            '48cdacc29159204fba1c3b81fb5eb7851435d06c10dfc937961f7d41b0df9e53',
            '1c99f1ff5d16397f3682680e08b712199aef47910ee46b641b0025ec266797bd',
            '42672eb44581587a20a8b04c289fa4a9c44afd45770d9df6ab3d3f96184ccba9',
        ),
    }
    for name, (normalized, semantic, plan_hash) in golden.items():
        plan = compile_plan_text((FIXTURES / name).read_text())
        assert (plan.contract_version, plan.compiler_version) == ('1.0', '1.2')
        assert plan.normalized_hash == normalized
        assert plan.semantic_fingerprint == semantic
        assert plan.plan_hash == plan_hash
        # 1.0 keeps the scalar field and exposes it as a one-item axis for shared code.
        assert plan.normalized.load.output_token_limits == [plan.normalized.load.output_token_limit]


def test_schema_versions_cannot_borrow_each_others_load_fields() -> None:
    document = _matrix()
    document['schema_version'] = '1.0'
    with pytest.raises(PlanParseError) as migrated:
        _compile(document)
    assert migrated.value.code == 'invalid_value'
    assert "'1.1'" in migrated.value.message
    assert migrated.value.field_path == 'load.output_token_limits'

    scalar = _matrix()
    scalar['schema_version'] = '1.0'
    scalar['load'].pop('output_token_limits')
    scalar['load']['output_token_limit'] = 128
    assert compile_plan_text(_text(scalar)).contract_version == '1.0'

    legacy_axis = json.loads((FIXTURES / 'managed-small.json').read_text())
    legacy_axis['load']['output_token_limits'] = [32, 64]
    with pytest.raises(PlanParseError) as rejected:
        _compile(legacy_axis)
    assert rejected.value.field_path == 'load.output_token_limits'

    unsupported = _matrix()
    unsupported['schema_version'] = '2.0'
    with pytest.raises(PlanParseError) as future:
        _compile(unsupported)
    assert future.value.code == 'unsupported_schema'


def test_output_axis_changes_run_ids_and_hashes() -> None:
    single = _matrix()
    single['load']['output_token_limits'] = [128]
    one = _compile(single)

    both = _matrix()
    two = _compile(both)

    assert one.run_count == 40 and two.run_count == 80
    one_ids = {run.run_id for run in one.runs}
    two_ids = {run.run_id for run in two.runs}
    # The first output length keeps its index in both plans; the second output
    # length adds ids that a single-limit plan cannot contain.
    assert one_ids <= two_ids
    assert any('-o001-' in run_id for run_id in two_ids - one_ids)
    assert one.normalized_hash != two.normalized_hash
    assert one.semantic_fingerprint != two.semantic_fingerprint
    assert one.plan_hash != two.plan_hash


def test_renaming_a_schema_11_plan_does_not_change_request_semantics() -> None:
    first = _compile(_matrix())
    renamed = _matrix()
    renamed['name'] = 'Renamed'
    second = _compile(renamed)
    assert second.normalized_hash != first.normalized_hash
    assert second.plan_hash != first.plan_hash
    assert second.semantic_fingerprint == first.semantic_fingerprint


def test_schema_11_export_round_trip_preserves_identity() -> None:
    first = _compile(_matrix())
    exported = json.dumps(first.normalized.model_dump(mode='json'), indent=2, sort_keys=True, ensure_ascii=False)
    again = compile_plan_text(exported)
    assert again.normalized_hash == first.normalized_hash
    assert again.plan_hash == first.plan_hash
    assert again.contract_version == '1.1'
