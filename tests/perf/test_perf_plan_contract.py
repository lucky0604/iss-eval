"""Portable plan contract: strict decoding, schema versioning and rejection codes."""

import json
import re
from pathlib import Path

import pytest

from evalscope.perf.plans import (
    PLAN_SCHEMA_VERSION,
    PlanParseError,
    compile_plan_text,
    parse_document_text,
    parse_plan_text,
)

FIXTURES = Path(__file__).parent / 'fixtures' / 'perf_plan'


def _managed() -> dict:
    return json.loads((FIXTURES / 'managed-small.json').read_text())


def _text(document: dict) -> str:
    return json.dumps(document)


def test_documented_examples_compile_and_carry_the_declared_contract() -> None:
    for name in ('managed-small.json', 'synthetic-small.json', 'legacy-context-rejected.json'):
        text = (FIXTURES / name).read_text()
        plan = compile_plan_text(text)
        assert plan.contract_version == PLAN_SCHEMA_VERSION
        assert plan.compiler_version
        assert plan.run_count > 0
        assert len(plan.plan_hash) == 64 and len(plan.semantic_fingerprint) == 64


def test_duplicate_json_key_is_rejected_before_pydantic() -> None:
    text = '{"kind": "iss-eval.perf-plan", "schema_version": "1.0", "load": {}, "load": {}}'
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(text)
    assert error.value.code == 'duplicate_json_key'


def test_credential_fields_are_rejected_with_a_field_path() -> None:
    document = _managed()
    document['connection']['api_key'] = 'sk-secret'
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(_text(document))
    assert error.value.code == 'credential_in_document'
    assert 'connection.api_key' in error.value.message


def test_command_fields_are_rejected() -> None:
    for field in ('argv', 'shell', 'executable'):
        document = _managed()
        document[field] = 'rm -rf /'
        with pytest.raises(PlanParseError) as error:
            parse_plan_text(_text(document))
        assert error.value.code == 'unsupported_feature'


def test_unknown_field_is_rejected_instead_of_ignored() -> None:
    document = _managed()
    document['unsupported_knob'] = 1
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(_text(document))
    assert error.value.code == 'invalid_value'
    assert 'unsupported_knob' in error.value.message


def test_unknown_schema_version_is_a_typed_rejection() -> None:
    document = _managed()
    document['schema_version'] = '2.0'
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(_text(document))
    assert error.value.code == 'unsupported_schema'


def test_arbitrary_backend_payload_is_not_a_plan() -> None:
    body = {'model': 'x', 'url': 'http://localhost:8000/v1', 'parallel': [1], 'number': [1]}
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(json.dumps(body))
    assert error.value.code == 'unsupported_schema'


def test_configuration_library_is_classified_but_not_accepted_here() -> None:
    body = {'version': 2, 'connections': [], 'values': {}}
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(json.dumps(body))
    assert error.value.code == 'unsupported_schema'


def test_document_size_limit_is_enforced_on_bytes() -> None:
    with pytest.raises(PlanParseError) as error:
        parse_document_text(' ' * 16, max_bytes=8)
    assert error.value.code == 'too_large_document'


def test_non_finite_number_is_rejected() -> None:
    text = re.sub(r'"request_count": \d+', '"request_count": Infinity', _text(_managed()), count=1)
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(text)
    assert error.value.code == 'invalid_type'


def test_strict_int_rejects_boolean_and_string() -> None:
    document = _managed()
    document['load']['request_count'] = True
    with pytest.raises(PlanParseError):
        parse_plan_text(_text(document))
    document = _managed()
    document['load']['request_count'] = '20'
    with pytest.raises(PlanParseError):
        parse_plan_text(_text(document))


def test_ratio_one_is_rejected_because_no_distinct_suffix_exists() -> None:
    document = _managed()
    document['workload']['target_ratios'] = [0.8, 1.0]
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(_text(document))
    assert error.value.code == 'invalid_value'
    assert 'no distinct suffix' in error.value.message


def test_duplicate_matrix_values_are_rejected() -> None:
    managed = _managed()
    managed['workload']['input_lengths'] = [100, 100]
    managed['workload']['target_ratios'] = [0.5, 0.5]
    managed['load']['points'] = [{'concurrency': 4}, {'concurrency': 4}]
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(_text(managed))
    assert 'unique' in error.value.message

    synthetic = json.loads((FIXTURES / 'synthetic-small.json').read_text())
    synthetic['workload']['cases'].append(dict(synthetic['workload']['cases'][0]))
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(_text(synthetic))
    assert 'unique' in error.value.message


def test_managed_requires_a_primer_policy() -> None:
    document = _managed()
    document['priming']['policy'] = 'none'
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(_text(document))
    assert 'prime_all_prefixes' in error.value.message


def test_synthetic_rejects_a_primer_and_a_chat_template() -> None:
    document = json.loads((FIXTURES / 'synthetic-small.json').read_text())
    document['priming']['policy'] = 'prime_all_prefixes'
    with pytest.raises(PlanParseError):
        parse_plan_text(_text(document))
    document = json.loads((FIXTURES / 'synthetic-small.json').read_text())
    document['workload']['apply_chat_template'] = True
    with pytest.raises(PlanParseError):
        parse_plan_text(_text(document))


def test_synthetic_ratio_verification_defaults_to_a_declared_only_check() -> None:
    plan = compile_plan_text((FIXTURES / 'synthetic-small.json').read_text())
    workload = plan.normalized.workload
    assert workload.ratio_tolerance == 0.05
    assert workload.require_observed_ratio is False


def test_synthetic_ratio_verification_rejects_a_boolean_and_out_of_range_tolerance() -> None:
    for value in (True, -0.1, 1.1):
        document = json.loads((FIXTURES / 'synthetic-small.json').read_text())
        document['workload']['ratio_tolerance'] = value
        with pytest.raises(PlanParseError):
            parse_plan_text(_text(document))


def test_telemetry_targets_cannot_enter_a_portable_document() -> None:
    document = _managed()
    document['telemetry']['targets'] = [{'url': 'http://node:9100/metrics'}]
    with pytest.raises(PlanParseError) as error:
        parse_plan_text(_text(document))
    assert 'node metrics targets' in error.value.message


def test_credentialed_url_is_rejected() -> None:
    document = _managed()
    document['connection']['url'] = 'http://user:password@localhost:8000/v1'
    with pytest.raises(PlanParseError):
        parse_plan_text(_text(document))
