from pydantic import BaseModel

from evalscope.api.metric import JudgeSummary
from evalscope.api.metric.semantics import MetricDisplayKind
from evalscope.report.report import ExecutionSummary

from .common import (
    ActiveTaskInfo,
    ApiResponseModel,
    ConfigResponse,
    DataFrameResponse,
    ListActiveTasksResponse,
    LogResponse,
    ProgressResponse,
    TaskConfigSummary,
    TaskStatusResponse,
)
from .eval import BenchmarkEntry, BenchmarksResponse, EvalInvokeResponse
from .perf import (
    CacheExperimentListResponse,
    CacheExperimentResponse,
    CacheNodeMetricsResponse,
    CacheRepetitionsResponse,
    CacheRequestGroupsResponse,
    CacheRequestsResponse,
    CacheScenarioResponse,
    DeletePerfRunResponse,
    ListPerfRunsResponse,
    PerfDetailResponse,
    PerfEnginesResponse,
    PerfInvokeResponse,
    PerfLiveResponse,
    PerfPlanResponse,
    PerfRequestsResponse,
    PerfRunItem,
    PerfRunsListResponse,
    PerfRunSummary,
    PerfSinglePlanResponse,
)
from .reports import (
    AnalysisResponse,
    ChatMessage,
    ContentBlock,
    DeleteReportResponse,
    JudgeAttempt,
    ListReportsGroupedResponse,
    ListReportsResponse,
    LoadReportResponse,
    MetricIdentity,
    MetricSemantics,
    PercentileStats,
    PerfMetrics,
    PredictionRow,
    PredictionScore,
    PredictionsResponse,
    PredictionToolCall,
    ReportData,
    ReportGroup,
    ReportMetric,
    ReportSummary,
    ValueRange,
)


class WebApiContracts(BaseModel):
    """Schema-only root that keeps every public Web API type reachable for code generation."""

    config_response: ConfigResponse
    data_frame_response: DataFrameResponse
    log_response: LogResponse
    task_status_response: TaskStatusResponse
    progress_response: ProgressResponse
    eval_invoke_response: EvalInvokeResponse
    benchmark_entry: BenchmarkEntry
    benchmarks_response: BenchmarksResponse
    report_data: ReportData
    report_summary: ReportSummary
    list_reports_response: ListReportsResponse
    report_group: ReportGroup
    list_reports_grouped_response: ListReportsGroupedResponse
    load_report_response: LoadReportResponse
    percentile_stats: PercentileStats
    perf_metrics: PerfMetrics
    content_block: ContentBlock
    tool_call: PredictionToolCall
    chat_message: ChatMessage
    judge_attempt: JudgeAttempt
    judge_summary: JudgeSummary
    prediction_score: PredictionScore
    prediction_row: PredictionRow
    predictions_response: PredictionsResponse
    delete_report_response: DeleteReportResponse
    analysis_response: AnalysisResponse
    perf_run_summary: PerfRunSummary
    list_perf_runs_response: ListPerfRunsResponse
    perf_detail_response: PerfDetailResponse
    perf_engines_response: PerfEnginesResponse
    perf_invoke_response: PerfInvokeResponse
    perf_live_response: PerfLiveResponse
    perf_plan_response: PerfPlanResponse
    perf_single_plan_response: PerfSinglePlanResponse
    cache_experiment_response: CacheExperimentResponse
    cache_experiment_list_response: CacheExperimentListResponse
    cache_scenario_response: CacheScenarioResponse
    cache_requests_response: CacheRequestsResponse
    cache_request_groups_response: CacheRequestGroupsResponse
    cache_repetitions_response: CacheRepetitionsResponse
    cache_node_metrics_response: CacheNodeMetricsResponse
    perf_run_item: PerfRunItem
    perf_runs_list_response: PerfRunsListResponse
    perf_requests_response: PerfRequestsResponse
    delete_perf_run_response: DeletePerfRunResponse
    active_task_info: ActiveTaskInfo
    list_active_tasks_response: ListActiveTasksResponse
    metric_identity: MetricIdentity
    metric_display_kind: MetricDisplayKind
    metric_semantics: MetricSemantics
    value_range: ValueRange
    report_metric: ReportMetric
    execution_summary: ExecutionSummary


__all__ = [
    'ActiveTaskInfo',
    'AnalysisResponse',
    'ApiResponseModel',
    'BenchmarkEntry',
    'CacheExperimentResponse',
    'CacheExperimentListResponse',
    'CacheNodeMetricsResponse',
    'CacheRequestsResponse',
    'CacheRequestGroupsResponse',
    'CacheRepetitionsResponse',
    'CacheScenarioResponse',
    'BenchmarksResponse',
    'ConfigResponse',
    'DataFrameResponse',
    'DeletePerfRunResponse',
    'DeleteReportResponse',
    'EvalInvokeResponse',
    'ListActiveTasksResponse',
    'ListPerfRunsResponse',
    'ListReportsGroupedResponse',
    'ListReportsResponse',
    'LoadReportResponse',
    'LogResponse',
    'PerfDetailResponse',
    'PerfEnginesResponse',
    'PerfInvokeResponse',
    'PerfLiveResponse',
    'PerfPlanResponse',
    'PerfSinglePlanResponse',
    'PerfRequestsResponse',
    'PerfRunsListResponse',
    'PredictionsResponse',
    'ProgressResponse',
    'TaskStatusResponse',
    'TaskConfigSummary',
    'WebApiContracts',
]
