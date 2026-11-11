"""Global experiment summary contract.

Counts cover the whole saved plan and never shrink to the currently paged
matrix window. Means and percentiles stay in their groups; this summary only
aggregates trustworthy request counts.
"""

from typing import Dict, List, Optional

from .common import ApiResponseModel
from .tasks import Availability, Completeness, QualityLevel


class ExperimentCoverage(ApiResponseModel):
    """Planned / completed / readable subrun counts, kept explicitly distinct."""

    planned: int = 0
    completed: int = 0
    readable: int = 0
    included: int = 0


class ExperimentGroup(ApiResponseModel):
    """One comparable cell of the experiment matrix (dimensions never merged)."""

    group_key: str
    load_mode: str
    concurrency: Optional[int] = None
    rate: Optional[float] = None
    target_ratio: float
    input_tokens: Optional[int] = None
    output_token_limit: Optional[int] = None
    total_subruns: int = 0
    readable_subruns: int = 0
    status_counts: Dict[str, int] = {}
    planned_requests: int = 0
    observed_requests: int = 0
    successful_requests: int = 0


class CacheExperimentSummaryResponse(ApiResponseModel):
    """Authoritative, plan-wide experiment counts for the report header."""

    task_id: str
    plan_hash: str
    configuration_hash: Optional[str] = None
    observed_at: str
    revision: int = 0
    execution_status: str
    quality: Optional[QualityLevel] = None
    total_planned_subruns: int = 0
    counts_by_status: Dict[str, int] = {}
    counts_by_quality: Dict[str, int] = {}
    readable_subruns: int = 0
    missing_subruns: int = 0
    unreadable_subruns: int = 0
    coverage: ExperimentCoverage
    groups: List[ExperimentGroup]
    completeness: Completeness = 'complete'
    completeness_reason: Optional[str] = None
    availability: Availability = 'available'
