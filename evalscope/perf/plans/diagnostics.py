"""Structured, stable diagnostics for portable performance plans."""

from typing import List, Literal, Optional

from pydantic import Field

from evalscope.utils import BaseArgument

DiagnosticSeverity = Literal['error', 'warning', 'info']

DiagnosticCode = Literal[
    'invalid_json',
    'too_large_document',
    'duplicate_json_key',
    'unknown_field',
    'unsupported_schema',
    'invalid_type',
    'invalid_value',
    'unsupported_feature',
    'credential_in_document',
    'too_many_runs',
    'duplicate_load_point',
    'duplicate_ratio',
    'context_overflow',
    'context_limit_unknown',
    'context_overhead_unknown',
    'request_count_below_concurrency',
    'sample_budget_small',
    'warmup_budget_large',
    'output_budget_exceeded',
    'legacy_rounding_difference',
    'workload_semantics_changed',
    'routing_coverage_unknown',
    'legacy_source_differs',
    'legacy_version_unknown',
    'synthetic_semantics_unverified',
    'tokenizer_unavailable',
    'runtime_unavailable',
    'runtime_version_mismatch',
    'plan_hash_mismatch',
]

#: Warning codes that must be acknowledged explicitly (scoped to one plan hash) before submit.
ACKNOWLEDGEMENT_CODES = frozenset(
    {
        'request_count_below_concurrency',
        'sample_budget_small',
        'context_overhead_unknown',
        'warmup_budget_large',
        'synthetic_semantics_unverified',
        'legacy_rounding_difference',
        'legacy_source_differs',
        'routing_coverage_unknown',
    }
)


class PlanDiagnostic(BaseArgument):
    """One explanation attached to a stable code, a field path and an optional case.

    ``parameters`` only carries redacted, numeric or enumerated values so a
    diagnostic can be shown in the UI, written to evidence and logged without
    leaking prompts, tokens or credentials.
    """

    code: DiagnosticCode
    severity: DiagnosticSeverity
    field_path: str = ''
    case_id: Optional[str] = None
    message_key: str = ''
    message: str = ''
    parameters: dict = Field(default_factory=dict[str, object])

    @property
    def blocks_submit(self) -> bool:
        """Return whether this diagnostic must prevent submission."""
        return self.severity == 'error'


def error(
    code: DiagnosticCode, message: str, field_path: str = '', case_id: Optional[str] = None, **parameters: object
) -> PlanDiagnostic:
    """Build an error diagnostic."""
    return PlanDiagnostic(
        code=code,
        severity='error',
        field_path=field_path,
        case_id=case_id,
        message_key=f'perf.plan.diagnostic.{code}',
        message=message,
        parameters=parameters,
    )


def warning(
    code: DiagnosticCode, message: str, field_path: str = '', case_id: Optional[str] = None, **parameters: object
) -> PlanDiagnostic:
    """Build a warning diagnostic."""
    return PlanDiagnostic(
        code=code,
        severity='warning',
        field_path=field_path,
        case_id=case_id,
        message_key=f'perf.plan.diagnostic.{code}',
        message=message,
        parameters=parameters,
    )


def info(
    code: DiagnosticCode, message: str, field_path: str = '', case_id: Optional[str] = None, **parameters: object
) -> PlanDiagnostic:
    """Build an informational diagnostic."""
    return PlanDiagnostic(
        code=code,
        severity='info',
        field_path=field_path,
        case_id=case_id,
        message_key=f'perf.plan.diagnostic.{code}',
        message=message,
        parameters=parameters,
    )


def acknowledgement_codes(diagnostics: List[PlanDiagnostic]) -> List[str]:
    """Return the distinct warning codes a client must acknowledge, in stable order."""
    codes: List[str] = []
    for item in diagnostics:
        if item.severity == 'warning' and item.code in ACKNOWLEDGEMENT_CODES and item.code not in codes:
            codes.append(item.code)
    return codes
