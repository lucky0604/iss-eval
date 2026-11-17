"""Response models for the unified performance report snapshot."""

from typing import List, Literal, Optional

from pydantic import Field

from ...perf.results.report import (
    ReportChartSeries,
    ReportHeader,
    ReportRequestRow,
    ReportRun,
)
from .common import ApiResponseModel


class PerfReportSummaryResponse(ApiResponseModel):
    """Snapshot identity, plan-wide counts and groups without the run rows."""

    report: ReportHeader
    total_runs: int = Field(ge=0)


class PerfReportRunsResponse(ApiResponseModel):
    """One page of the full main table; ``total`` is the filtered row count."""

    snapshot_id: str
    rows: List[ReportRun]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    group: Optional[str] = None
    sort: str
    order: Literal['asc', 'desc']


class PerfReportRequestsResponse(ApiResponseModel):
    """One page of a run's request details, filtered and sorted server-side."""

    snapshot_id: str
    run_key: str
    run_id: str
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    status: Optional[str] = None
    phase: Optional[str] = None
    search: Optional[str] = None
    sort: str
    order: Literal['asc', 'desc']
    requests: List[ReportRequestRow]


class PerfReportChartsResponse(ApiResponseModel):
    """Chart data computed from the whole snapshot, not the current table page."""

    snapshot_id: str
    metric: str
    statistic: str
    series: List[ReportChartSeries]
