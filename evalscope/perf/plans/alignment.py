"""Per-dimension alignment between a plan, its source snapshot and a result.

A single ``equivalent`` boolean is deliberately not modelled: parameters,
request semantics, cache state, environment, runtime version and statistics
can each be matched, different, unknown or unsupported independently.
"""

from typing import List, Literal, Optional

from pydantic import Field, StrictInt

from evalscope.utils import BaseArgument

AlignmentStatus = Literal['matched', 'different', 'unknown', 'unsupported']
AlignmentDimensionName = Literal['parameters', 'request_semantics', 'cache', 'environment', 'version', 'statistics']

_STATUS_ORDER: dict[str, int] = {'matched': 0, 'unknown': 1, 'different': 2, 'unsupported': 3}


class AlignmentDimension(BaseArgument):
    """One comparable dimension with concrete reasons."""

    dimension: AlignmentDimensionName
    status: AlignmentStatus
    reasons: List[str] = Field(default_factory=list)


class PlanAlignment(BaseArgument):
    """All comparable dimensions for one plan and the resulting overall status."""

    schema_version: Literal['1.0'] = '1.0'
    source: Optional[str] = None
    overall: AlignmentStatus = 'unknown'
    dimensions: List[AlignmentDimension] = Field(default_factory=list)

    @property
    def by_dimension(self) -> dict[str, AlignmentDimension]:
        """Index dimensions by name for callers that project a single status."""
        return {item.dimension: item for item in self.dimensions}


def overall_status(dimensions: List[AlignmentDimension]) -> AlignmentStatus:
    """Return the worst status, so an unknown dimension can never be reported as matched."""
    worst = 'matched'
    for item in dimensions:
        if _STATUS_ORDER[item.status] > _STATUS_ORDER[worst]:
            worst = item.status
    return worst  # type: ignore[return-value]


class LegacySourceFacts(BaseArgument):
    """The migration source, normalized into explicit values without re-rounding them."""

    script_name: str
    runtime_version: Optional[str] = None
    model: Optional[str] = None
    url: Optional[str] = None
    tokenizer: Optional[str] = None
    endpoint_type: Optional[str] = None
    streaming: Optional[bool] = None
    input_lengths: List[StrictInt] = Field(default_factory=list)
    target_prefix_ratio: Optional[float] = None
    output_tokens: Optional[StrictInt] = None
    request_count: Optional[StrictInt] = None
    concurrencies: List[StrictInt] = Field(default_factory=list)
    repetitions: Optional[StrictInt] = None
    order: Optional[str] = None
    warmup_request_count: Optional[StrictInt] = None
    warmup_input_tokens: Optional[StrictInt] = None
    warmup_output_tokens: Optional[StrictInt] = None
    warmup_concurrency: Optional[StrictInt] = None
    warmup_failure_policy: Optional[str] = None
    declared_max_model_len: Optional[StrictInt] = None
    routing_policy: Optional[str] = None
    awk_cases: List[dict] = Field(default_factory=list)
