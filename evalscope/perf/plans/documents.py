"""Versioned, credential-free performance plan document.

These types are the single source of truth for the ``iss-eval.perf-plan`` file
format. They are deliberately independent of the Web layer: the service imports
them, it does not define them. Runtime secrets, executor paths and arbitrary
command arguments have no field here by design.
"""

import math
import re
from typing import Annotated, List, Literal, Optional, Union

from pydantic import Field, StrictInt, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from evalscope.utils import BaseArgument
from evalscope.utils.uri_utils import validate_http_endpoint

from .strict import PlanParseError

PLAN_KIND = 'iss-eval.perf-plan'
PLAN_SCHEMA_VERSION = '1.0'

_CASE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
_MAX_NAME = 120


class PlanConnection(BaseArgument):
    """Where the plan sends requests; never carries a credential."""

    model: str = Field(min_length=1, max_length=256)
    url: str = Field(min_length=1, max_length=2048)
    tokenizer_path: str = Field(min_length=1, max_length=1024)
    protocol: Literal['openai_chat'] = 'openai_chat'
    streaming: Literal[True] = True

    @field_validator('model', 'tokenizer_path')
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('model and tokenizer_path must not be blank')
        return value.strip()

    @field_validator('url')
    @classmethod
    def validate_url(cls, value: str) -> str:
        validate_http_endpoint(value)
        return value.strip()


class DeploymentFacts(BaseArgument):
    """Declared deployment shape; facts are recorded, never used to reprogram a cluster."""

    mode: Literal['aggregated', 'disaggregated', 'unknown'] = 'unknown'
    declared_max_model_len: Optional[StrictInt] = Field(default=None, gt=0)
    evidence: Literal['declared', 'measured', 'unknown'] = 'unknown'
    routing_policy: Literal['round_robin', 'unknown', 'other'] = 'unknown'
    routing_coverage: Literal['unknown', 'partial', 'all_replicas'] = 'unknown'
    deployment_id: Optional[str] = Field(default=None, max_length=128)
    model_revision: Optional[str] = Field(default=None, max_length=128)
    engine_image_digest: Optional[str] = Field(default=None, max_length=128)
    engine_version: Optional[str] = Field(default=None, max_length=64)
    node_count: Optional[StrictInt] = Field(default=None, gt=0)
    gpu_count: Optional[StrictInt] = Field(default=None, gt=0)
    tp: Optional[StrictInt] = Field(default=None, gt=0)
    dp: Optional[StrictInt] = Field(default=None, gt=0)
    pp: Optional[StrictInt] = Field(default=None, gt=0)
    ep: Optional[StrictInt] = Field(default=None, gt=0)
    kv_dtype: Optional[str] = Field(default=None, max_length=32)
    prefix_cache: Optional[Literal['enabled', 'disabled', 'unknown']] = None
    max_num_seqs: Optional[StrictInt] = Field(default=None, gt=0)


class TokenPrefixWorkload(BaseArgument):
    """Managed token-prefix workload: a target reuse ratio per input length."""

    kind: Literal['token_prefix']
    input_lengths: List[StrictInt] = Field(min_length=1, max_length=64)
    target_ratios: List[float] = Field(min_length=1, max_length=16)
    prefix_pool_size: StrictInt = Field(default=1, gt=0)
    seed: StrictInt = 42

    @field_validator('input_lengths')
    @classmethod
    def validate_lengths(cls, value: List[int]) -> List[int]:
        if any(item <= 0 for item in value):
            raise ValueError('input lengths must be positive integers')
        if len(set(value)) != len(value):
            raise ValueError('input lengths must be unique')
        return value

    @field_validator('target_ratios', mode='before')
    @classmethod
    def validate_ratios(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                raise ValueError('target ratios must be finite numbers between 0 and 1')
            if not 0 <= item < 1:
                raise ValueError(
                    'target ratios must satisfy 0 <= ratio < 1: ratio 1.0 leaves no distinct suffix and cannot be '
                    'rendered as a template-validated prompt'
                )
        if len(set(value)) != len(value):
            raise ValueError('target ratios must be unique')
        return value


class SyntheticCase(BaseArgument):
    """One explicit shared-system / unique-user split copied from a legacy script."""

    case_id: str = Field(min_length=1, max_length=64)
    declared_total_input_tokens: StrictInt = Field(gt=0)
    shared_system_tokens: StrictInt = Field(ge=0)
    unique_user_tokens: StrictInt = Field(gt=0)
    target_prefix_ratio: Optional[float] = None

    @field_validator('case_id')
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        if not _CASE_ID.fullmatch(value):
            raise ValueError('case_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}')
        return value

    @field_validator('target_prefix_ratio', mode='before')
    @classmethod
    def validate_ratio(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('target prefix ratio must be a finite number between 0 and 1')
        if not 0 <= value <= 1:
            raise ValueError('target prefix ratio must be between 0 and 1')
        return value


class SyntheticWorkload(BaseArgument):
    """AIPerf synthetic compatibility workload: system + user messages, not one user blob."""

    kind: Literal['aiperf_synthetic']
    cases: List[SyntheticCase] = Field(min_length=1, max_length=64)
    apply_chat_template: Literal[False] = False
    cache_bust: Literal['none'] = 'none'
    # A synthetic workload only *declares* its prefix ratio; the server decides
    # what it cached. The tolerance gates the declared-versus-observed check,
    # and ``require_observed_ratio`` turns a missing observation into a defect.
    ratio_tolerance: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)
    require_observed_ratio: bool = False

    @field_validator('ratio_tolerance', mode='before')
    @classmethod
    def reject_boolean_tolerance(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError('ratio tolerance must be a finite number between 0 and 1')
        return value

    @model_validator(mode='after')
    def unique_cases(self) -> 'SyntheticWorkload':
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError('synthetic case IDs must be unique')
        return self


PlanWorkload = Annotated[Union[TokenPrefixWorkload, SyntheticWorkload], Field(discriminator='kind')]


class PlanLoadPoint(BaseArgument):
    """One closed-loop concurrency point."""

    concurrency: StrictInt = Field(gt=0)


class PlanLoad(BaseArgument):
    """Load axis, request count and output budget shared by every run."""

    mode: Literal['closed_loop'] = 'closed_loop'
    points: List[PlanLoadPoint] = Field(min_length=1, max_length=32)
    request_count: StrictInt = Field(gt=0)
    output_token_limit: StrictInt = Field(gt=0)
    repetitions: StrictInt = Field(default=1, gt=0)

    @model_validator(mode='after')
    def unique_points(self) -> 'PlanLoad':
        if len({point.concurrency for point in self.points}) != len(self.points):
            raise ValueError('load points must have unique concurrency values')
        return self


class PlanWarmup(BaseArgument):
    """Ordinary warmup sent once before the whole matrix; never counted as formal samples."""

    enabled: bool = False
    request_count: StrictInt = Field(default=3, gt=0)
    input_tokens: StrictInt = Field(default=16000, gt=0)
    output_token_limit: StrictInt = Field(default=1000, gt=0)
    concurrency: StrictInt = Field(default=1, gt=0)
    failure_policy: Literal['abort', 'record_and_continue'] = 'abort'


class PlanPriming(BaseArgument):
    """Prefix priming policy, separate from ordinary warmup."""

    policy: Literal['prime_all_prefixes', 'none'] = 'prime_all_prefixes'
    timeout_seconds: float = Field(default=30, gt=0, le=3600, allow_inf_nan=False)

    @field_validator('timeout_seconds', mode='before')
    @classmethod
    def reject_boolean_timeout(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError('primer timeout must be numeric')
        return value


class PlanExecution(BaseArgument):
    """Deterministic ordering of the compiled matrix."""

    order: Literal['seeded_shuffle', 'load_then_input'] = 'load_then_input'
    seed: Optional[StrictInt] = None


class PlanTelemetry(BaseArgument):
    """Node telemetry stays outside the portable document in this version.

    The Web form still supports required node metrics, but a portable plan has
    no field that can name a collection target: the target URL and its bearer
    token are runtime configuration. Accepting ``required=true`` here would
    promise a collection this document cannot describe, so it is rejected
    instead of being silently downgraded to ``required=false``.
    """

    required: bool = False
    targets: List[dict] = Field(default_factory=list)

    @field_validator('required')
    @classmethod
    def reject_required_telemetry(cls, value: bool) -> bool:
        if value:
            raise PydanticCustomError(
                'unsupported_feature',
                'telemetry.required=true is not supported for portable plans: node-metric targets and their '
                'credentials are runtime configuration, so a required collection cannot be guaranteed from the '
                'document; use the existing AIPerf form for required node metrics',
            )
        return value

    @field_validator('targets')
    @classmethod
    def reject_portable_targets(cls, value: List[dict]) -> List[dict]:
        if value:
            raise PydanticCustomError(
                'unsupported_feature',
                'portable plans cannot carry node metrics targets in this version; configure them through the '
                'existing connection form so credentials never enter the document',
            )
        return value


class PerfPlanDocument(BaseArgument):
    """A portable, versioned and credential-free performance plan."""

    kind: Literal['iss-eval.perf-plan'] = PLAN_KIND
    schema_version: Literal['1.0'] = PLAN_SCHEMA_VERSION
    name: Optional[str] = Field(default=None, max_length=_MAX_NAME)
    intent: Literal['diagnostic', 'benchmark'] = 'benchmark'
    connection: PlanConnection
    deployment: DeploymentFacts = Field(default_factory=DeploymentFacts)
    workload: PlanWorkload
    load: PlanLoad
    warmup: PlanWarmup = Field(default_factory=PlanWarmup)
    priming: PlanPriming = Field(default_factory=PlanPriming)
    execution: PlanExecution = Field(default_factory=PlanExecution)
    telemetry: PlanTelemetry = Field(default_factory=PlanTelemetry)

    @field_validator('name')
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode='after')
    def validate_modes(self) -> 'PerfPlanDocument':
        workload = self.workload
        if isinstance(workload, TokenPrefixWorkload) and self.priming.policy != 'prime_all_prefixes':
            raise ValueError(
                'token_prefix workloads require priming.policy=prime_all_prefixes; '
                'a managed prefix scenario without a primer barrier cannot claim warm cache'
            )
        if isinstance(workload, SyntheticWorkload) and self.priming.policy != 'none':
            raise ValueError(
                'aiperf_synthetic workloads require priming.policy=none: the legacy CLI did not send a primer and '
                'adding one would change the request sequence'
            )
        return self


def parse_plan_document(document: dict) -> PerfPlanDocument:
    """Validate a decoded document, mapping version problems to a stable code."""
    version = document.get('schema_version')
    if version is not None and version != PLAN_SCHEMA_VERSION:
        raise PlanParseError(
            'unsupported_schema',
            f'schema_version {version!r} is not supported; this build reads {PLAN_SCHEMA_VERSION!r} only',
            'schema_version',
        )
    try:
        return PerfPlanDocument.model_validate(document)
    except ValidationError as error:
        raise _plan_parse_error(error) from None
    except Exception as error:  # pragma: no cover - defensive, unexpected validator failure
        raise PlanParseError('invalid_value', _describe(error)) from None


def _field_path(item: dict) -> str:
    return '.'.join(str(part) for part in item['loc']) or 'document'


def _plan_parse_error(error: ValidationError) -> PlanParseError:
    """Map one validation failure to a stable code, preferring explicit unsupported fields."""
    details = error.errors(include_input=False, include_url=False, include_context=False)
    unsupported = [item for item in details if item.get('type') == 'unsupported_feature']
    if unsupported:
        return PlanParseError('unsupported_feature', _describe(error), _field_path(unsupported[0]))
    return PlanParseError('invalid_value', _describe(error), _field_path(details[0]) if details else '')


def _describe(error: Exception) -> str:
    """Render a validation error without echoing submitted values."""
    errors = getattr(error, 'errors', None)
    if callable(errors):
        parts = []
        for item in errors(include_input=False, include_url=False):
            location = '.'.join(str(part) for part in item['loc']) or 'document'
            parts.append(f'{location}: {item["msg"]}')
        if parts:
            return '; '.join(parts)
    return str(error) or type(error).__name__
