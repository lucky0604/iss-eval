"""Unified performance-result entry contracts.

Both execution backends (EvalScope native and AIPerf) and every workload shape
(single, sweep, cache experiment) are projected into one entry type. The
backend name is secondary metadata; the list and detail framework are shared.
"""

from typing import List, Literal, Optional

from pydantic import Field

from .common import ApiResponseModel
from .tasks import Completeness, EngineName, PerfTestType, QualityLevel, ResultCapabilities

ResultKind = Literal['run', 'experiment']

ResultAction = Literal['open', 'compare', 'delete', 'html', 'copy_config']


class PerformanceResultEntry(ApiResponseModel):
    """One row of the unified performance result list."""

    entry_id: str
    result_kind: ResultKind
    root_path: str
    path: str
    task_id: Optional[str] = None
    display_name: str = ''
    model: str = ''
    alias: Optional[str] = None
    endpoint: Optional[str] = None
    dataset: Optional[str] = None
    test_type: PerfTestType = 'unknown'
    engine: Optional[EngineName] = None
    executor_version: Optional[str] = None
    execution_status: Optional[str] = None
    quality: Optional[QualityLevel] = None
    timestamp: Optional[str] = None
    time_basis: Literal['aware', 'legacy', 'unknown'] = 'unknown'
    num_runs: Optional[int] = None
    total_requests: Optional[int] = None
    best_rps: Optional[float] = None
    best_latency: Optional[float] = None
    success_rate: Optional[float] = None
    metric_scope: Literal['run', 'none'] = 'none'
    capabilities: ResultCapabilities
    detail_path: Optional[str] = None
    actions: List[ResultAction] = Field(default_factory=list)
    unreadable_reason: Optional[str] = None


class PerfEntryListResponse(ApiResponseModel):
    """A filtered page of unified performance entries."""

    entries: List[PerformanceResultEntry]
    total: int
    known_total: int
    completeness: Completeness
    offset: int
    limit: int
    observed_at: str
    root_path: str
    scanned: int = 0
    skipped: int = 0
