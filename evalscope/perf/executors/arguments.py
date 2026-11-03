"""Typed configuration for native and external performance executors."""

import math
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, SecretStr, StrictInt, field_validator, model_validator

from evalscope.perf.arguments import Arguments
from evalscope.utils import BaseArgument
from evalscope.utils.uri_utils import validate_http_endpoint


class LoadPoint(BaseArgument):
    """One closed-loop concurrency or open-loop request rate."""

    concurrency: Optional[StrictInt] = Field(default=None, gt=0)
    rate: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)

    @field_validator('rate', mode='before')
    @classmethod
    def reject_boolean_rate(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError('rate must be numeric')
        return value


class LoadConfiguration(BaseArgument):
    """Load points and budgets shared by all experiment scenarios."""

    mode: Literal['closed_loop', 'open_loop'] = 'closed_loop'
    points: List[LoadPoint] = Field(min_length=1)
    request_count: StrictInt = Field(gt=0)
    output_token_limit: StrictInt = Field(gt=0)
    repetitions: StrictInt = Field(default=1, gt=0)

    @model_validator(mode='after')
    def validate_points(self) -> 'LoadConfiguration':
        for point in self.points:
            if self.mode == 'closed_loop' and (point.concurrency is None or point.rate is not None):
                raise ValueError('closed-loop points require only concurrency')
            if self.mode == 'open_loop' and (point.rate is None or point.concurrency is not None):
                raise ValueError('open-loop points require only rate')
        return self


class CacheExperiment(BaseArgument):
    """Target prefix reuse, independent of generated reuse or observed cache hits."""

    mode: Literal['token_prefix_ratio'] = 'token_prefix_ratio'
    target_ratios: List[float] = Field(min_length=1)
    total_input_tokens: StrictInt = Field(gt=0)
    prefix_pool_size: StrictInt = Field(default=1, gt=0)
    seed: StrictInt = 42
    warmup_policy: Literal['prime_all_prefixes'] = 'prime_all_prefixes'
    cache_state_policy: Literal['fresh_namespace'] = 'fresh_namespace'
    routing_policy: Literal['router_native'] = 'router_native'
    routing_coverage: Literal['observed'] = 'observed'

    @field_validator('target_ratios', mode='before')
    @classmethod
    def validate_ratios(cls, value: Any) -> Any:
        if not isinstance(value, list) or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            or not 0 <= item <= 1
            for item in value
        ):
            raise ValueError('target ratios must be finite numbers between 0 and 1')
        return value


class TelemetryTarget(BaseArgument):
    """One Prometheus endpoint with its own role, labels, and credentials."""

    node_id: str = Field(min_length=1)
    role: Literal['prefill', 'decode']
    engine: Literal['vllm', 'sglang']
    url: str
    labels: Dict[str, str] = Field(default_factory=dict)
    headers: Dict[str, SecretStr] = Field(default_factory=dict)

    @field_validator('node_id')
    @classmethod
    def validate_node_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('node ID must not be blank')
        return value

    @field_validator('url')
    @classmethod
    def validate_url(cls, value: str) -> str:
        validate_http_endpoint(value)
        return value.strip()


class TelemetryConfiguration(BaseArgument):
    """Configuration for independent node-level telemetry collection."""

    required: bool = False
    sample_interval_seconds: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    targets: List[TelemetryTarget] = Field(default_factory=list)

    @field_validator('sample_interval_seconds', mode='before')
    @classmethod
    def reject_boolean_interval(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError('sample interval must be numeric')
        return value

    @model_validator(mode='after')
    def unique_nodes(self) -> 'TelemetryConfiguration':
        if len({target.node_id for target in self.targets}) != len(self.targets):
            raise ValueError('telemetry node IDs must be unique')
        return self


class AIPerfArguments(BaseArgument):
    """Supported, structured AIPerf task configuration."""

    model: str = Field(min_length=1)
    alias: Optional[str] = None
    url: str
    api: Literal['openai'] = 'openai'
    api_key: Optional[SecretStr] = None
    headers: Dict[str, SecretStr] = Field(default_factory=dict)
    tokenizer_path: str = Field(min_length=1)
    stream: bool = True
    deployment_mode: Literal['single', 'pd'] = 'single'
    cache_experiment: Optional[CacheExperiment] = None
    load: LoadConfiguration
    telemetry: TelemetryConfiguration = Field(default_factory=TelemetryConfiguration)

    @field_validator('model', 'tokenizer_path')
    @classmethod
    def reject_blank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('model and tokenizer path must not be blank')
        return value

    @field_validator('url')
    @classmethod
    def validate_url(cls, value: str) -> str:
        validate_http_endpoint(value)
        return value.strip()

    @model_validator(mode='after')
    def validate_pd_targets(self) -> 'AIPerfArguments':
        if self.deployment_mode == 'pd' and self.telemetry.required:
            if {target.role for target in self.telemetry.targets} != {'prefill', 'decode'}:
                raise ValueError('required PD telemetry needs both prefill and decode targets')
        return self


class PerfTaskArguments(BaseArgument):
    """Discriminated task envelope; exactly one executor configuration is allowed."""

    engine: Literal['evalscope', 'aiperf']
    native: Optional[Arguments] = None
    aiperf: Optional[AIPerfArguments] = None

    @model_validator(mode='after')
    def validate_executor(self) -> 'PerfTaskArguments':
        if (self.engine == 'evalscope' and (self.native is None or self.aiperf is not None)) or (
            self.engine == 'aiperf' and (self.aiperf is None or self.native is not None)
        ):
            raise ValueError('exactly one matching executor configuration is required')
        return self


def parse_perf_task_arguments(payload: Dict[str, Any]) -> PerfTaskArguments:
    """Parse a strict executor envelope or the existing flat native payload."""
    if not isinstance(payload, dict) or not payload:
        raise ValueError('performance configuration must be a nonempty object')
    if 'engine' not in payload:
        return PerfTaskArguments(engine='evalscope', native=Arguments.from_dict(payload))
    return PerfTaskArguments.from_dict(payload)
