"""Internal contracts shared by performance executor implementations."""

from typing import Dict, List, Literal, Optional

from pydantic import Field, SecretStr

from evalscope.utils import BaseArgument


class ExternalRuntime(BaseArgument):
    """Server-owned executable and shutdown budgets, never supplied by a task."""

    executable: str = Field(min_length=1)
    expected_version: Literal['0.13.0'] = '0.13.0'
    graceful_timeout: float = Field(default=10, gt=0, allow_inf_nan=False)
    terminate_timeout: float = Field(default=5, gt=0, allow_inf_nan=False)
    kill_timeout: float = Field(default=5, gt=0, allow_inf_nan=False)


class ExecutionContext(BaseArgument):
    """Task identity and server-selected artifact directory for an executor."""

    task_id: str = Field(min_length=1)
    output_dir: str = Field(min_length=1)
    runtime: Optional[ExternalRuntime] = None
    expected_plan_hash: Optional[str] = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class ExecutionOutcome(BaseArgument):
    """Small external-run result; raw artifacts remain on disk."""

    engine: Literal['aiperf'] = 'aiperf'
    executor_version: Optional[str] = None
    exit_reason: Literal['completed', 'error', 'cancelled']
    exit_code: Optional[int] = None
    artifact_status: Literal['available', 'partial', 'missing']
    raw_artifact_dir: Optional[str] = None
    cleanup_confirmed: bool = False


class ExternalInvocation(BaseArgument):
    """Server-prepared command; never deserialize this from a client request."""

    arguments: List[str] = Field(default_factory=list)
    working_dir: str = Field(min_length=1)
    log_file: str = Field(min_length=1)
    environment: Dict[str, SecretStr] = Field(default_factory=dict)
    redactions: List[SecretStr] = Field(default_factory=list)
    max_log_bytes: int = Field(default=20 * 1024 * 1024, gt=0, strict=True)
