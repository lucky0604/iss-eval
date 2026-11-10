/* eslint-disable */
/** Generated from Pydantic Web API response models. Do not edit. */

/**
 * Whether a score is usable, and why it is not.
 *
 * A failed judge is not a zero: only ``SUCCESS`` and ``FALLBACK`` carry a score that may
 * enter aggregation. The remaining values mean the score is unavailable and the sample
 * must be excluded from the affected metric rather than counted as 0.
 */
export type ScoreStatus =
  "success" | "transport_error" | "parse_error" | "invalid_session" | "fallback" | "degraded" | "excluded";
/**
 * Optimization direction of a metric.
 */
export type MetricDirection = "higher_is_better" | "lower_is_better" | "none";
/**
 * How a metric value is rendered.
 */
export type MetricDisplayKind = "number" | "percent";
/**
 * Whether a metric grades quality or only describes a run.
 */
export type MetricKind = "quality" | "diagnostic";
/**
 * Canonical event kinds emitted by the AgentLoop or external-agent bridge.
 */
export type EventType =
  | "model_generate"
  | "tool_call"
  | "tool_result"
  | "env_exec"
  | "env_reset"
  | "error"
  | "submit"
  | "nudge"
  | "run_start"
  | "run_end";

/**
 * Schema-only root that keeps every public Web API type reachable for code generation.
 */
export interface WebApiContracts {
  active_task_info: ActiveTaskInfo;
  aiperf_single_config_snapshot: AiPerfSingleConfigSnapshot;
  analysis_response: AnalysisResponse;
  availability_state: AvailabilityState;
  benchmark_entry: BenchmarkEntry;
  benchmarks_response: BenchmarksResponse;
  cache_experiment_config_snapshot: CacheExperimentConfigSnapshot;
  cache_experiment_list_response: CacheExperimentListResponse;
  cache_experiment_response: CacheExperimentResponse;
  cache_experiment_summary_response: CacheExperimentSummaryResponse;
  cache_node_metrics_response: CacheNodeMetricsResponse;
  cache_node_series_response: CacheNodeSeriesResponse;
  cache_repetitions_response: CacheRepetitionsResponse;
  cache_request_groups_response: CacheRequestGroupsResponse;
  cache_requests_response: CacheRequestsResponse;
  cache_scenario_response: CacheScenarioResponse;
  chat_message: ChatMessage;
  config_response: ConfigResponse;
  content_block: ContentBlock;
  data_frame_response: DataFrameResponse;
  delete_perf_run_response: DeletePerfRunResponse;
  delete_report_response: DeleteReportResponse;
  eval_config_snapshot: EvalConfigSnapshot;
  eval_invoke_response: EvalInvokeResponse;
  execution_summary: ExecutionSummary;
  experiment_coverage: ExperimentCoverage;
  experiment_group: ExperimentGroup;
  judge_attempt: JudgeAttempt;
  judge_summary: JudgeSummary;
  list_active_tasks_response: ListActiveTasksResponse;
  list_perf_runs_response: ListPerfRunsResponse;
  list_reports_grouped_response: ListReportsGroupedResponse;
  list_reports_response: ListReportsResponse;
  load_report_response: LoadReportResponse;
  log_response: LogResponse;
  metric_display_kind: MetricDisplayKind;
  metric_identity: MetricIdentity;
  metric_semantics: MetricSemantics;
  native_perf_config_snapshot: NativePerfConfigSnapshot;
  percentile_stats: PercentileStats;
  perf_detail_response: PerfDetailResponse;
  perf_engines_response: PerfEnginesResponse;
  perf_entry_list_response: PerfEntryListResponse;
  perf_invoke_response: PerfInvokeResponse;
  perf_live_response: PerfLiveResponse;
  perf_metrics: PerfMetrics;
  perf_plan_archive_response: PerfPlanArchiveResponse;
  perf_plan_export_response: PerfPlanExportResponse;
  perf_plan_invoke_response: PerfPlanInvokeResponse;
  perf_plan_preview_response: PerfPlanPreviewResponse;
  perf_plan_response: PerfPlanResponse;
  perf_plan_run_view: PerfPlanRunView;
  perf_plan_submit_response: PerfPlanSubmitResponse;
  perf_requests_response: PerfRequestsResponse;
  perf_run_item: PerfRunItem;
  perf_run_summary: PerfRunSummary;
  perf_runs_list_response: PerfRunsListResponse;
  perf_single_plan_response: PerfSinglePlanResponse;
  performance_result_entry: PerformanceResultEntry;
  plan_config_snapshot: PlanConfigSnapshot;
  plan_diagnostic_view: PlanDiagnosticView;
  plan_run_summary: PlanRunSummary;
  prediction_row: PredictionRow;
  prediction_score: PredictionScore;
  predictions_response: PredictionsResponse;
  progress_response: ProgressResponse;
  report_data: ReportData;
  report_group: ReportGroup;
  report_metric: ReportMetric;
  report_summary: ReportSummary;
  result_availability: ResultAvailability;
  result_capabilities: ResultCapabilities;
  task_detail_response: TaskDetailResponse;
  task_list_response: TaskListResponse;
  task_status_response: TaskStatusResponse;
  task_submit_response: TaskSubmitResponse;
  task_summary_item: TaskSummaryItem;
  task_summary_response: TaskSummaryResponse;
  tool_call: PredictionToolCall;
  value_range: ValueRange;
  [k: string]: any;
}
/**
 * Display identity of a task that is queued, running, or recently finished.
 */
export interface ActiveTaskInfo {
  alias?: string | null;
  completed_subruns?: number | null;
  config?: TaskConfigSummary | null;
  current_subrun?: string | null;
  endpoint?: string | null;
  engine?: ("evalscope" | "aiperf") | null;
  enqueued_at?: string | null;
  error?: string | null;
  finished_at?: string | null;
  html_report_url?: string | null;
  kind: string;
  model?: string;
  percent?: number | null;
  phase?: string | null;
  quality?: ("complete" | "partial" | "invalid") | null;
  queue_position?: number | null;
  report_url?: string | null;
  started_at?: string;
  status?:
    "queued" | "preparing" | "running" | "cancelling" | "finalizing" | "completed" | "stopped" | "error" | "unknown";
  task_id: string;
  total_subruns?: number | null;
}
export interface TaskConfigSummary {
  api?: string | null;
  dataset?: string | null;
  datasets?: string[] | null;
  eval_batch_size?: number | null;
  limit?: number | null;
  max_prompt_length?: number | null;
  max_tokens?: number | number[] | null;
  min_prompt_length?: number | null;
  min_tokens?: number | null;
  number?: number | number[] | null;
  parallel?: number | number[] | null;
  rate?: number | number[] | null;
  repeats?: number | null;
  temperature?: number | null;
  top_k?: number | null;
  top_p?: number | null;
}
/**
 * Public settings of an AIPerf single load run imported by the service.
 */
export interface AiPerfSingleConfigSnapshot {
  alias?: string | null;
  api_type?: string;
  concurrency?: number | null;
  endpoint?: string | null;
  engine?: "aiperf";
  input_sha256?: string | null;
  kind?: "perf";
  load_mode?: string | null;
  model?: string;
  output_token_limit?: number | null;
  planned_requests?: number | null;
  rate?: number | null;
  schema_version?: number;
  snapshot_type?: "perf_aiperf_single";
  test_type?: "single";
}
export interface AnalysisResponse {
  analysis: string;
}
/**
 * Whether one result surface can be read, with the reason when it cannot.
 */
export interface AvailabilityState {
  availability?: "available" | "pending" | "not_configured" | "unsupported" | "unavailable" | "unknown";
  reason?: string | null;
}
export interface BenchmarkEntry {
  category: "llm" | "vlm" | "agent" | "aigc";
  dataset_id: string;
  description: BenchmarkDescription;
  few_shot_num: number;
  meta: {
    [k: string]: any;
  };
  metrics: string[];
  name: string;
  paper_url?: string | null;
  pretty_name: string;
  subset_list: string[];
  tags: string[];
  total_samples: number;
}
export interface BenchmarkDescription {
  en?: BenchmarkDescriptionLocale | null;
  zh?: BenchmarkDescriptionLocale | null;
}
export interface BenchmarkDescriptionLocale {
  full: string;
  sections: {
    [k: string]: string;
  };
}
export interface BenchmarksResponse {
  agent?: BenchmarkEntry[] | null;
  aigc?: BenchmarkEntry[] | null;
  multimodal?: BenchmarkEntry[] | null;
  text?: BenchmarkEntry[] | null;
}
/**
 * Public plan identity of a prefix-cache experiment (credentials excluded).
 */
export interface CacheExperimentConfigSnapshot {
  alias?: string | null;
  configuration_hash?: string | null;
  deployment_mode?: string | null;
  endpoint?: string | null;
  engine?: ("evalscope" | "aiperf") | null;
  kind?: "perf";
  load_modes?: string[];
  model?: string;
  node_ids?: string[];
  plan_hash?: string | null;
  repetitions?: number | null;
  schema_version?: number;
  snapshot_type?: "perf_cache_experiment";
  stream?: boolean | null;
  target_ratios?: number[];
  template_id?: string | null;
  test_type?: "cache_experiment";
  tokenizer_id?: string | null;
  total_subruns?: number | null;
}
export interface CacheExperimentListResponse {
  experiments: CacheExperimentListItem[];
  limit: number;
  offset: number;
  total: number;
}
export interface CacheExperimentListItem {
  completed_subruns: number;
  quality?: ("complete" | "partial" | "invalid") | null;
  status: "running" | "completed" | "stopped" | "error" | "unknown";
  task_id: string;
  total_subruns: number;
}
export interface CacheExperimentResponse {
  plan: CacheExperimentPlan;
  state: CacheExperimentState;
  task_id: string;
}
/**
 * A reproducible plan and its request budget.
 */
export interface CacheExperimentPlan {
  configuration?: CachePlanConfiguration | null;
  configuration_hash: string;
  max_primer_output_tokens?: number | null;
  max_profiling_output_tokens?: number | null;
  max_warmup_output_tokens?: number | null;
  plan_hash: string;
  primer_requests: number;
  profiling_requests: number;
  schema_version?: "1.0";
  subruns: CacheSubrunPlan[];
  template_id: string;
  tokenizer_id: string;
  warmup_requests?: number;
}
/**
 * Reproducible configuration without inference or telemetry credentials.
 */
export interface CachePlanConfiguration {
  cache: CacheExperiment;
  deployment_mode: "single" | "pd";
  load: LoadConfiguration;
  model: string;
  stream: boolean;
  task_id: string;
  telemetry: PlannedTelemetry;
  template_id: string;
  tokenizer_id: string;
  tokenizer_path: string;
  url: string;
}
/**
 * Target prefix reuse, independent of generated reuse or observed cache hits.
 */
export interface CacheExperiment {
  cache_state_policy?: "fresh_namespace";
  input_token_values?: number[] | null;
  mode?: "token_prefix_ratio" | "synthetic_isl";
  prefix_pool_size?: number;
  routing_coverage?: "observed";
  routing_policy?: "router_native";
  seed?: number;
  /**
   * @minItems 1
   */
  target_ratios: [number, ...number[]];
  total_input_tokens: number;
  warmup?: WarmupConfiguration | null;
  warmup_policy?: "prime_all_prefixes";
}
/**
 * Synthetic pre-run traffic sent once before a cache experiment matrix.
 */
export interface WarmupConfiguration {
  concurrency: number;
  output_token_limit: number;
  request_count: number;
  total_input_tokens: number;
}
/**
 * Load points and budgets shared by all experiment scenarios.
 */
export interface LoadConfiguration {
  mode?: "closed_loop" | "open_loop";
  output_token_limit: number;
  /**
   * @minItems 1
   */
  points: [LoadPoint, ...LoadPoint[]];
  repetitions?: number;
  request_count: number;
}
/**
 * One closed-loop concurrency or open-loop request rate.
 */
export interface LoadPoint {
  concurrency?: number | null;
  rate?: number | null;
}
export interface PlannedTelemetry {
  max_scrape_gap_seconds: number | null;
  required: boolean;
  sample_interval_seconds: number;
  targets: PlannedMetricNode[];
}
/**
 * Credential-free identity of a configured telemetry node.
 */
export interface PlannedMetricNode {
  engine: "vllm" | "sglang";
  labels: {
    [k: string]: string;
  };
  node_id: string;
  role: "prefill" | "decode";
  url: string;
}
/**
 * One uniquely named, independently seeded matrix member.
 */
export interface CacheSubrunPlan {
  input_tokens?: number | null;
  load_mode: "closed_loop" | "open_loop";
  load_point: LoadPoint;
  namespace: string;
  order: number;
  output_token_limit?: number | null;
  primer_requests: number;
  repetition: number;
  request_count: number;
  seed: number;
  subrun_id: string;
  target_ratio: number;
}
export interface CacheExperimentState {
  completed_subruns: number;
  current_subrun?: string | null;
  last_phase?: string | null;
  phase?: string | null;
  plan_hash: string;
  quality?: ("complete" | "partial" | "invalid") | null;
  schema_version: "1.0";
  status: "running" | "completed" | "stopped" | "error" | "unknown";
  subruns: CacheSubrunState[];
  total_subruns: number;
  warmup?: CacheWarmupState | null;
}
export interface CacheSubrunState {
  input_tokens?: number | null;
  quality?: ("complete" | "partial" | "invalid") | null;
  scenario?: string | null;
  status: "not_started" | "running" | "completed" | "cancelled" | "error";
  subrun_id: string;
}
export interface CacheWarmupState {
  completed_requests?: number | null;
  concurrency?: number | null;
  failed_requests?: number | null;
  output_token_limit?: number | null;
  reason?: string | null;
  request_count?: number | null;
  status: "completed" | "failed" | "cancelled";
  total_input_tokens?: number | null;
}
/**
 * Authoritative, plan-wide experiment counts for the report header.
 */
export interface CacheExperimentSummaryResponse {
  availability?: "available" | "pending" | "not_configured" | "unsupported" | "unavailable" | "unknown";
  completeness?: "complete" | "partial";
  completeness_reason?: string | null;
  configuration_hash?: string | null;
  counts_by_quality?: {
    [k: string]: number;
  };
  counts_by_status?: {
    [k: string]: number;
  };
  coverage: ExperimentCoverage;
  execution_status: string;
  groups: ExperimentGroup[];
  missing_subruns?: number;
  observed_at: string;
  plan_hash: string;
  quality?: ("complete" | "partial" | "invalid") | null;
  readable_subruns?: number;
  revision?: number;
  task_id: string;
  total_planned_subruns?: number;
  unreadable_subruns?: number;
}
/**
 * Planned / completed / readable subrun counts, kept explicitly distinct.
 */
export interface ExperimentCoverage {
  completed?: number;
  included?: number;
  planned?: number;
  readable?: number;
}
/**
 * One comparable cell of the experiment matrix (dimensions never merged).
 */
export interface ExperimentGroup {
  concurrency?: number | null;
  group_key: string;
  input_tokens?: number | null;
  load_mode: string;
  observed_requests?: number;
  output_token_limit?: number | null;
  planned_requests?: number;
  rate?: number | null;
  readable_subruns?: number;
  status_counts?: {
    [k: string]: number;
  };
  successful_requests?: number;
  target_ratio: number;
  total_subruns?: number;
}
export interface CacheNodeMetricsResponse {
  aggregation?: "raw";
  downsample?: "none";
  limit: number;
  offset: number;
  samples: NodeMetricSample[];
  total: number;
}
/**
 * One source-attributed raw sample; no request-level PD inference is made.
 */
export interface NodeMetricSample {
  endpoint: string;
  engine?: ("vllm" | "sglang") | null;
  labels: {
    [k: string]: string;
  };
  metric_name: string;
  metric_type: "counter" | "gauge" | "histogram" | "unknown";
  node_id?: string | null;
  observation: {
    [k: string]: any;
  };
  phase_kind?: ("warmup" | "profiling") | null;
  request_sent_ns?: number | null;
  role?: ("prefill" | "decode") | null;
  scope?: "node_window";
  scrape_latency_ns?: number | null;
  semantics?: "unverified";
  timestamp_ns: number;
}
/**
 * Bounded raw node points and their full matching sample count.
 */
export interface CacheNodeSeriesResponse {
  series: NodeMetricSeries;
}
/**
 * Selected raw points, reporting the full matching population and sampling method.
 */
export interface NodeMetricSeries {
  aggregation?: "raw";
  downsample: "none" | "uniform_index";
  max_points: number;
  samples: NodeMetricSample[];
  total: number;
}
export interface CacheRepetitionsResponse {
  limit: number;
  offset: number;
  plan_hash: string;
  repetitions: CacheRepetitionItem[];
  total: number;
}
/**
 * One planned repetition with its independently observed metric.
 */
export interface CacheRepetitionItem {
  engine?: "aiperf" | null;
  executor_version?: string | null;
  metric?: MetricStatistics | null;
  quality?: ("complete" | "partial" | "invalid") | null;
  repetition: number;
  status: string;
  subrun_id: string;
  unavailable_reason?: string | null;
}
/**
 * A metric with explicit unit, scope, source, and observation count.
 */
export interface MetricStatistics {
  availability?: "available" | "unavailable";
  count?: number | null;
  key: string;
  reason?: string | null;
  scope: "request" | "run" | "node_window";
  source: string;
  statistics?: {
    [k: string]: number;
  };
  unit: string;
  value?: number | null;
}
export interface CacheRequestGroupsResponse {
  availability: "available" | "unavailable";
  groups?: CacheRequestGroup[];
  observed_requests: number;
  reason?: string | null;
  successful_requests?: number | null;
}
/**
 * A group defined only by the server's request-level cached token count.
 */
export interface CacheRequestGroup {
  count: number;
  group: "no_cached_tokens" | "some_cached_tokens";
  latency: RequestLatencyDistribution;
  ttft: RequestLatencyDistribution;
}
/**
 * Exact nearest-rank percentiles for observed request latencies.
 */
export interface RequestLatencyDistribution {
  count: number;
  mean?: number | null;
  p50?: number | null;
  p95?: number | null;
  p99?: number | null;
  unit?: "seconds";
}
export interface CacheRequestsResponse {
  limit: number;
  offset: number;
  requests: PerfRequestRecord[];
  total: number;
}
/**
 * A request record without inferred server cache data.
 */
export interface PerfRequestRecord {
  cached_tokens?: number | null;
  cached_tokens_source?: "server_usage" | null;
  end_ns?: number | null;
  error_code?: number | null;
  index: number;
  latency_seconds?: number | null;
  output_tokens?: number | null;
  output_tokens_source?: ("server_usage" | "aiperf_estimate") | null;
  phase: string;
  prompt_tokens?: number | null;
  prompt_tokens_source?: ("server_usage" | "aiperf_estimate") | null;
  request_id?: string | null;
  session_id?: string | null;
  start_ns?: number | null;
  status: "success" | "error" | "cancelled" | "incomplete";
  status_reason?: string | null;
  ttft_seconds?: number | null;
  turn_index?: number | null;
}
export interface CacheScenarioResponse {
  cache?: CacheObservation | null;
  generated_count: number;
  generated_ratio?: number | null;
  input_tokens_per_request?: number | null;
  node_analysis?: NodeMetricsAnalysis | null;
  quality_reasons?: string[];
  request_hashes_sha256?: string | null;
  result?: PerfRunResult | null;
  status: string;
  subrun_id: string;
  target_ratio: number;
  workload_mode?: ("token_prefix_ratio" | "synthetic_isl") | null;
}
/**
 * Server-reported cache-read token totals and their actual coverage.
 */
export interface CacheObservation {
  availability: "available" | "unavailable";
  cached_tokens?: number | null;
  observed_requests: number;
  prompt_tokens?: number | null;
  ratio?: number | null;
  reason?: string | null;
  source?: "server_usage" | null;
  successful_requests?: number | null;
}
/**
 * Bounded node analysis and the provenance of its profiling interval.
 */
export interface NodeMetricsAnalysis {
  availability: "available" | "partial" | "missing" | "invalid";
  cache_ratios?: NodeCacheRatio[];
  max_gap_ns: number;
  quality_reasons?: string[];
  reason?: string | null;
  scrapes?: NodeScrapeSummary[];
  series?: NodeMetricWindow[];
  timed_requests?: number;
  total_requests?: number;
  window?: MetricWindow | null;
  window_source?: ("aiperf_phase" | "observed_request_bounds") | null;
}
/**
 * vLLM lookup-token ratio; never confused with response cache usage.
 */
export interface NodeCacheRatio {
  availability: "available" | "partial" | "missing" | "invalid";
  hit_tokens?: number | null;
  labels: {
    [k: string]: string;
  };
  lookup_tokens?: number | null;
  node_id: string;
  ratio?: number | null;
  reason?: string | null;
  role: "prefill" | "decode";
  semantics?: "vllm_prefix_lookup_tokens";
}
/**
 * Observed successful scrapes; failures are unknown when not exported.
 */
export interface NodeScrapeSummary {
  failed_scrapes?: number | null;
  mean_interval_seconds?: number | null;
  mean_latency_seconds?: number | null;
  node_id: string;
  observed_successful_scrapes: number;
}
/**
 * A single node/label series with upstream definition and measured window.
 */
export interface NodeMetricWindow {
  attribution?: "node_window_shared_traffic";
  engine: "vllm" | "sglang";
  histogram?: HistogramWindow | null;
  labels: {
    [k: string]: string;
  };
  metric: MetricMapping;
  node_id: string;
  reason?: string | null;
  role: "prefill" | "decode";
  statistic?: WindowStatistic | null;
  verification?: "deployment_unverified";
  window_source: "aiperf_phase" | "observed_request_bounds";
}
/**
 * Window bucket deltas and approximate quantiles, never averaged percentiles.
 */
export interface HistogramWindow {
  availability: "available" | "partial" | "missing" | "invalid";
  boundary?: "exact" | "bracketed" | "unknown";
  buckets?: {
    [k: string]: number;
  };
  count?: number | null;
  mean?: number | null;
  metric_key: string;
  p50?: number | null;
  p95?: number | null;
  p99?: number | null;
  reason?: string | null;
  source_end_ns?: number | null;
  source_start_ns?: number | null;
  sum?: number | null;
  unit: string;
  window_end_ns: number;
  window_start_ns: number;
}
/**
 * One exact upstream metric family and its non-aggregated semantics.
 */
export interface MetricMapping {
  engine: "vllm" | "sglang";
  key: string;
  kind: "gauge" | "counter" | "histogram";
  name: string;
  scale?: number;
  source_unit: string;
  source_url: string;
  unit: string;
  upstream_version: string;
}
/**
 * A window value with coverage and explicit boundary semantics.
 */
export interface WindowStatistic {
  availability: "available" | "partial" | "missing" | "invalid";
  boundary?: "exact" | "bracketed" | "step" | "unknown";
  coverage?: number | null;
  reason?: string | null;
  sample_count: number;
  sample_peak?: number | null;
  source_end_ns?: number | null;
  source_start_ns?: number | null;
  value?: number | null;
}
/**
 * An explicit profiling window in the collector's nanosecond clock.
 */
export interface MetricWindow {
  end_ns: number;
  start_ns: number;
}
/**
 * A normalized run; raw files remain the source of truth.
 */
export interface PerfRunResult {
  engine?: "aiperf";
  executor_version: string;
  metrics?: MetricStatistics[];
  profiling_cancelled?: number | null;
  profiling_end_ns?: number | null;
  profiling_errors: number;
  profiling_incomplete?: number | null;
  profiling_requests: number;
  profiling_start_ns?: number | null;
  profiling_successes: number;
  quality: "complete" | "partial" | "invalid";
  quality_reasons?: string[];
  records_schema?: "unversioned";
  schema_version?: "1.0";
  server_metrics_schema?: string | null;
  summary_schema?: string | null;
}
export interface ChatMessage {
  content: string | ContentBlock[];
  error?: ToolMessageError | null;
  function?: string | null;
  id?: string | null;
  metadata?: {
    [k: string]: any;
  } | null;
  model?: string | null;
  perf_metrics?: SamplePerfMetrics | null;
  role: "system" | "user" | "assistant" | "tool";
  source?: ("input" | "generate") | null;
  tool_call_id?: string | string[] | null;
  tool_calls?: PredictionToolCall[] | null;
}
export interface ContentBlock {
  audio?: string | null;
  data?: {
    [k: string]: any;
  } | null;
  detail?: string | null;
  format?: string | null;
  image?: string | null;
  reasoning?: string | null;
  reasoning_tokens?: number | null;
  text?: string | null;
  type: "text" | "reasoning" | "image" | "audio" | "video" | "data";
  video?: string | null;
  [k: string]: any;
}
export interface ToolMessageError {
  message: string;
  type?: string | null;
}
export interface SamplePerfMetrics {
  input_tokens: number;
  latency: number;
  output_tokens: number;
  tpot?: number | null;
  ttft?: number | null;
}
export interface PredictionToolCall {
  arguments: {
    [k: string]: any;
  };
  function: string;
  id: string;
}
export interface ConfigResponse {
  outputs_root: string;
  version: string;
}
export interface DataFrameResponse {
  columns: string[];
  data: {
    [k: string]: any;
  }[];
}
export interface DeletePerfRunResponse {
  path: string;
  success: boolean;
}
export interface DeleteReportResponse {
  model_id: string;
  run_id: string;
  success: boolean;
}
/**
 * Public evaluation settings that are safe to display and copy.
 */
export interface EvalConfigSnapshot {
  alias?: string | null;
  datasets?: string[];
  endpoint?: string | null;
  engine?: ("evalscope" | "aiperf") | null;
  eval_batch_size?: number | null;
  kind?: "eval";
  limit?: number | null;
  max_tokens?: number | null;
  model?: string;
  repeats?: number | null;
  schema_version?: number;
  snapshot_type?: "eval";
  temperature?: number | null;
  top_k?: number | null;
  top_p?: number | null;
}
export interface EvalInvokeResponse {
  error?: string | null;
  result?: {
    [k: string]: any;
  };
  status: "ok" | "completed" | "error" | "stopped";
  table?: string | null;
  task_id: string;
}
/**
 * Run completeness kept separate from aggregation-specific metric counts.
 */
export interface ExecutionSummary {
  errored?: number;
  incomplete?: boolean;
  requested?: number;
  subsets?: {
    [k: string]: ExecutionSubset;
  };
  succeeded?: number;
  [k: string]: any;
}
/**
 * Completion counts for one evaluated subset.
 */
export interface ExecutionSubset {
  errored?: number;
  requested?: number;
  succeeded?: number;
  [k: string]: any;
}
export interface JudgeAttempt {
  case_id: string;
  error?: string | null;
  judge_id: string;
  latency?: number | null;
  messages?: any[];
  model_output?: {
    [k: string]: any;
  };
  parsed_value?: {
    [k: string]: any;
  };
  placement?: "original" | "swapped";
  raw_response?: string | null;
  repeat_id?: number;
  status: ScoreStatus;
}
/**
 * First-class summary of a judge session, for reports and offline inspection.
 */
export interface JudgeSummary {
  coverage?: number;
  disagreement?: {
    [k: string]: any;
  };
  error?: string | null;
  failures?: {
    [k: string]: number;
  };
  judge_models?: string[];
  scored?: number;
  /**
   * Whether a score is usable, and why it is not.
   *
   * A failed judge is not a zero: only ``SUCCESS`` and ``FALLBACK`` carry a score that may
   * enter aggregation. The remaining values mean the score is unavailable and the sample
   * must be excluded from the affected metric rather than counted as 0.
   */
  status?: "success" | "transport_error" | "parse_error" | "invalid_session" | "fallback" | "degraded" | "excluded";
  total?: number;
  total_observations?: number;
  valid_observations?: number;
  [k: string]: any;
}
export interface ListActiveTasksResponse {
  tasks: ActiveTaskInfo[];
  total: number;
}
export interface ListPerfRunsResponse {
  metric_semantics?: {
    [k: string]: MetricSemantics;
  };
  runs: PerfRunSummary[];
  total: number;
}
export interface MetricSemantics {
  direction: MetricDirection;
  display_kind: MetricDisplayKind;
  display_multiplier?: number | null;
  display_name?: string | null;
  display_precision: number;
  display_unit?: string | null;
  kind: MetricKind;
  metric_name: string;
  raw_unit?: string | null;
  semantic_id: string;
  value_range?: ValueRange | null;
}
export interface ValueRange {
  max: number;
  min: number;
}
export interface PerfRunSummary {
  alias?: string | null;
  api_host?: string | null;
  api_type: string;
  avg_input_tokens?: number | null;
  avg_output_tokens?: number | null;
  best_latency: number | null;
  best_rps: number | null;
  concurrency?: number[] | null;
  dataset: string;
  engine?: ("evalscope" | "aiperf") | null;
  executor_version?: string | null;
  has_html: boolean;
  is_embedding: boolean;
  model: string;
  num_runs: number;
  path: string;
  protocol?: string | null;
  provider?: string | null;
  quality?: ("complete" | "partial" | "invalid") | null;
  status?:
    | ("queued" | "preparing" | "running" | "cancelling" | "finalizing" | "completed" | "stopped" | "error" | "unknown")
    | null;
  success_rate: number | null;
  timestamp: string;
  total_requests: number;
}
export interface ListReportsGroupedResponse {
  filters: ReportFilters;
  page: number;
  page_size: number;
  reports: ReportGroup[];
  total: number;
}
export interface ReportFilters {
  available_datasets: string[];
  available_models: string[];
}
/**
 * One row per model in a `group_by=model` listing - display-only rollup, never a merged report.
 */
export interface ReportGroup {
  children: ReportSummary[];
  dataset_count: number;
  dataset_name: string;
  model_name: string;
  num_samples: number;
  refs: string[];
  report_count: number;
  timestamp: string;
}
export interface ReportSummary {
  alias?: string | null;
  api_host?: string | null;
  dataset_name: string;
  dataset_pretty_name?: string;
  model_id: string;
  model_name: string;
  num_samples: number;
  primary_metrics: PrimaryMetricRef[];
  run_id: string;
  timestamp: string;
}
export interface PrimaryMetricRef {
  dataset_name: string;
  dataset_pretty_name?: string;
  identity: MetricIdentity;
  score: number;
  semantics: MetricSemantics;
}
export interface MetricIdentity {
  aggregation: string;
  dimensions: {
    [k: string]: string | number | boolean;
  };
  name: string;
}
export interface ListReportsResponse {
  filters: ReportFilters;
  page: number;
  page_size: number;
  reports: ReportSummary[];
  total: number;
}
export interface LoadReportResponse {
  datasets: string[];
  report_list: ReportData[];
  task_config: {
    [k: string]: any;
  };
}
/**
 * Canonical report payload emitted by ``Report.to_dict``.
 */
export interface ReportData {
  analysis: string;
  dataset_description?: string;
  dataset_name: string;
  dataset_pretty_name?: string;
  execution_summary?: ExecutionSummary | null;
  judge_summary?: JudgeSummary | null;
  metrics: ReportMetric[];
  model_name: string;
  name: string;
  num?: number;
  perf_metrics?: PerfMetrics | null;
  primary_metric_identity?: MetricIdentity | null;
  primary_metric_unavailable_reason?: string | null;
  schema_version: 2;
}
export interface ReportMetric {
  categories: ReportCategory[];
  identity: MetricIdentity;
  legacy_name?: string | null;
  macro_score?: number;
  num: number;
  num_correct?: number | null;
  score: number;
  semantics: MetricSemantics;
}
export interface ReportCategory {
  macro_score?: number;
  name: string[];
  num: number;
  num_correct?: number | null;
  score: number;
  subsets: ReportSubset[];
}
export interface ReportSubset {
  is_aggregate?: boolean;
  metadata?: {
    [k: string]: any;
  } | null;
  name: string;
  num: number;
  num_correct?: number | null;
  score: number;
}
export interface PerfMetrics {
  coverage?: PerfCoverage | null;
  metric_semantics?: {
    [k: string]: MetricSemantics;
  };
  summary?: PerfMetricsSummary | null;
}
export interface PerfCoverage {
  requests_with_metrics: number;
  total_requests: number;
}
export interface PerfMetricsSummary {
  latency: PercentileStats;
  n_samples: number;
  throughput: ThroughputSummary;
  tpot?: PercentileStats | null;
  ttft?: PercentileStats | null;
  usage: UsageSummary;
}
export interface PercentileStats {
  "25%": number;
  "50%": number;
  "75%": number;
  "90%": number;
  "99%": number;
  max: number;
  mean: number;
  min: number;
  std: number | null;
}
export interface ThroughputSummary {
  avg_output_tps: number;
  avg_req_ps: number;
}
export interface UsageSummary {
  input_tokens: PercentileStats;
  output_tokens: PercentileStats;
  total_input_tokens?: number | null;
  total_output_tokens?: number | null;
  total_tokens: PercentileStats;
  total_tokens_count?: number | null;
}
export interface LogResponse {
  head_line: number;
  tail_line: number;
  text: string;
  total_lines: number;
}
/**
 * Public settings of a native EvalScope performance run (single or sweep).
 */
export interface NativePerfConfigSnapshot {
  alias?: string | null;
  api?: string | null;
  dataset?: string | null;
  endpoint?: string | null;
  engine?: "evalscope";
  kind?: "perf";
  max_prompt_length?: number | null;
  max_tokens?: number | number[] | null;
  min_prompt_length?: number | null;
  min_tokens?: number | null;
  model?: string;
  number?: number | number[] | null;
  parallel?: number | number[] | null;
  rate?: number | number[] | null;
  schema_version?: number;
  snapshot_type?: "perf_native";
  test_type?: "single" | "sweep" | "cache_experiment" | "unknown";
}
export interface PerfDetailResponse {
  alias?: string | null;
  api_type: string;
  basic_info: {
    [k: string]: string;
  };
  best_config: {
    [k: string]: string;
  };
  capabilities?: ResultCapabilities;
  configuration?: {
    [k: string]: string;
  } | null;
  dataset: string;
  engine?: ("evalscope" | "aiperf") | null;
  executor_version?: string | null;
  generated_at: string;
  has_html: boolean;
  is_embedding: boolean;
  model: string;
  node_analysis?: NodeMetricsAnalysis | null;
  num_runs: number;
  path: string;
  quality?: ("complete" | "partial" | "invalid") | null;
  quality_reasons?: string[];
  recommendations: string[];
  summary_columns: PerfSummaryColumn[];
  summary_rows: PerfSummaryRow[];
  test_type?: "single" | "sweep" | "cache_experiment" | "unknown";
  total_requests: number;
}
/**
 * Per-surface capability set resolved from the archive, never from the engine label.
 */
export interface ResultCapabilities {
  cache_experiment?: AvailabilityState;
  configuration?: AvailabilityState;
  html?: AvailabilityState;
  node_metrics?: AvailabilityState;
  overview?: AvailabilityState;
  requests?: AvailabilityState;
  sweep?: AvailabilityState;
}
export interface PerfSummaryColumn {
  key: string;
  label: string;
  semantics: MetricSemantics | null;
}
export interface PerfSummaryRow {
  sample_counts: {
    [k: string]: number;
  };
  values: {
    [k: string]: number | null;
  };
}
/**
 * Available and unavailable execution engines.
 */
export interface PerfEnginesResponse {
  engines: PerfEngineCapability[];
}
/**
 * Public availability of a performance execution engine.
 */
export interface PerfEngineCapability {
  available: boolean;
  engine: "evalscope" | "aiperf";
  protocols?: string[];
  reason?: string | null;
  version?: string | null;
}
/**
 * A filtered page of unified performance entries.
 */
export interface PerfEntryListResponse {
  completeness: "complete" | "partial";
  entries: PerformanceResultEntry[];
  known_total: number;
  limit: number;
  observed_at: string;
  offset: number;
  root_path: string;
  scanned?: number;
  skipped?: number;
  total: number;
}
/**
 * One row of the unified performance result list.
 */
export interface PerformanceResultEntry {
  actions?: ("open" | "compare" | "delete" | "html" | "copy_config")[];
  alias?: string | null;
  best_latency?: number | null;
  best_rps?: number | null;
  capabilities: ResultCapabilities;
  dataset?: string | null;
  detail_path?: string | null;
  display_name?: string;
  endpoint?: string | null;
  engine?: ("evalscope" | "aiperf") | null;
  entry_id: string;
  execution_status?: string | null;
  executor_version?: string | null;
  metric_scope?: "run" | "none";
  model?: string;
  num_runs?: number | null;
  path: string;
  quality?: ("complete" | "partial" | "invalid") | null;
  result_kind: "run" | "experiment";
  root_path: string;
  success_rate?: number | null;
  task_id?: string | null;
  test_type?: "single" | "sweep" | "cache_experiment" | "unknown";
  time_basis?: "aware" | "legacy" | "unknown";
  timestamp?: string | null;
  total_requests?: number | null;
  unreadable_reason?: string | null;
}
/**
 * Typed AIPerf task result, compatible with the shared task monitor.
 */
export interface PerfInvokeResponse {
  result: CacheExperimentOutcome | PublicAIPerfOutcome;
  status: "completed" | "error" | "stopped";
  task_id: string;
}
/**
 * Small task result; the experiment manifest contains subrun details.
 */
export interface CacheExperimentOutcome {
  cleanup_confirmed: boolean;
  completed_subruns: number;
  manifest?: string;
  plan_hash: string;
  quality: "partial" | "invalid";
  status: "completed" | "stopped" | "error";
  total_subruns: number;
}
/**
 * Single-run outcome without local paths or credentials.
 */
export interface PublicAIPerfOutcome {
  artifact_status: "available" | "partial" | "missing";
  cleanup_confirmed: boolean;
  engine: "aiperf";
  executor_version?: string | null;
  exit_code?: number | null;
  exit_reason: "completed" | "error" | "cancelled";
}
/**
 * Bounded last node scrapes and the current planned scenario, when available.
 */
export interface PerfLiveResponse {
  client?: ClientProgress | null;
  concurrency?: number | null;
  current_subrun?: string | null;
  expected_interval_seconds?: number | null;
  load_mode?: ("closed_loop" | "open_loop") | null;
  nodes?: LiveNode[];
  rate?: number | null;
  repetition?: number | null;
  target_ratio?: number | null;
  task_id: string;
}
/**
 * AIPerf client counters, distinct from all server node metrics.
 */
export interface ClientProgress {
  completed_records: number;
  error_records: number;
  rounded_requests_per_second?: number | null;
  sampled_at_ns: number;
  sent_at_ns?: number | null;
  sent_requests?: number | null;
  source?: "aiperf_0.13.0_realtime_log";
  successful_records: number;
}
export interface LiveNode {
  attempted_at_ns?: number | null;
  engine: "vllm" | "sglang";
  /**
   * @maxItems 16
   */
  gauges?:
    | []
    | [LiveGauge]
    | [LiveGauge, LiveGauge]
    | [LiveGauge, LiveGauge, LiveGauge]
    | [LiveGauge, LiveGauge, LiveGauge, LiveGauge]
    | [LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge]
    | [LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge]
    | [LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge]
    | [LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge]
    | [LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge]
    | [LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge, LiveGauge]
    | [
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge
      ]
    | [
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge
      ]
    | [
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge
      ]
    | [
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge
      ]
    | [
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge
      ]
    | [
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge,
        LiveGauge
      ];
  node_id: string;
  role: "prefill" | "decode";
  sampled_at_ns?: number | null;
  status?: "waiting" | "available" | "unavailable";
  truncated?: boolean;
}
/**
 * A single unaggregated gauge series from one actual node scrape.
 */
export interface LiveGauge {
  key: string;
  labels: {
    [k: string]: string;
  };
  name: string;
  unit: string;
  value: number;
}
/**
 * Archived plan provenance, bounded run page and effective runtime identity.
 */
export interface PerfPlanArchiveResponse {
  alignment?: PlanAlignment | null;
  archive?: {
    [k: string]: string;
  };
  budgets?: PlanBudgets | null;
  compiler_version?: string | null;
  completed_runs?: number;
  context?: ContextCheck[];
  contract_version?: string | null;
  current_run?: string | null;
  effective?: EffectiveIdentity | null;
  limit?: number;
  model?: string;
  name?: string | null;
  normalized_hash?: string | null;
  offset?: number;
  phase?: string | null;
  plan_hash?: string | null;
  quality?: ("complete" | "partial" | "invalid") | null;
  quality_reasons?: string[];
  report_available?: boolean;
  runs?: PerfPlanRunView[];
  semantic_fingerprint?: string | null;
  source_hash?: string | null;
  state?: "reserved" | "queued" | "running" | "completed" | "stopped" | "error" | "lost";
  state_reason?: string | null;
  status?: ("running" | "completed" | "stopped" | "error") | null;
  task_id: string;
  total_runs?: number;
  warmup?: PlanWarmupState | null;
  workload_semantics?: ("managed_token_prefix" | "aiperf_synthetic_compat") | null;
}
/**
 * All comparable dimensions for one plan and the resulting overall status.
 */
export interface PlanAlignment {
  dimensions?: AlignmentDimension[];
  overall?: "matched" | "different" | "unknown" | "unsupported";
  schema_version?: "1.0";
  source?: string | null;
}
/**
 * One comparable dimension with concrete reasons.
 */
export interface AlignmentDimension {
  dimension: "parameters" | "request_semantics" | "cache" | "environment" | "version" | "statistics";
  reasons?: string[];
  status: "matched" | "different" | "unknown" | "unsupported";
}
/**
 * Request and output ceilings, with the one-off phases kept separate.
 */
export interface PlanBudgets {
  formal_output_tokens: number;
  formal_requests: number;
  primer_requests: number;
  total_requests: number;
  warmup_output_tokens: number;
  warmup_requests: number;
}
/**
 * One distinct context judgement, exposed so the UI can explain the basis.
 */
export interface ContextCheck {
  case_id?: string | null;
  limit_tokens?: number | null;
  overhead?: "included" | "known" | "unknown";
  required_tokens?: number | null;
  run_id: string;
  status?: "fits" | "overflow" | "unknown";
}
/**
 * Execution-time identity, kept apart from the accepted plan hash.
 *
 * ``identity_version`` versions the comparison semantics. A comparison of two
 * records with different versions, or with unknown content hashes, is
 * ``unknown``: an equal unknown fingerprint never means "matched".
 */
export interface EffectiveIdentity {
  aiperf_version?: string | null;
  compiler_version: string;
  contract_version: string;
  deployment_evidence?: "declared" | "measured" | "unknown";
  executor?: "evalscope_service";
  identity_version?: "1";
  parent_plan_hash?: string | null;
  plan_hash: string;
  schema_version?: "1.0";
  tokenizer?: TokenizerIdentity;
}
/**
 * What was actually resolved, never what a path suggests.
 */
export interface TokenizerIdentity {
  content_hash?: string | null;
  path?: string | null;
  status?: "resolved" | "unknown";
  template_hash?: string | null;
}
/**
 * One planned run as requested (dimensions) and as executed (state).
 *
 * The dimensions come from the accepted plan and the state from the manifest,
 * so a partially executed matrix shows the real length/concurrency/repetition
 * of every point instead of the first run's arguments.
 */
export interface PerfPlanRunView {
  cache_verification?: CacheRatioVerification | null;
  case_id?: string | null;
  concurrency: number;
  declared_total_input_tokens?: number | null;
  finished_ns?: number | null;
  formal_requests: number;
  input_tokens?: number | null;
  message_structure: "user_only" | "system_and_user";
  normalized?: string | null;
  order: number;
  output_token_limit: number;
  primer_requests?: number;
  quality?: ("complete" | "partial" | "invalid") | null;
  reason?: string | null;
  repetition: number;
  requests_available?: boolean;
  run_id: string;
  shared_system_tokens?: number | null;
  started_ns?: number | null;
  status?: "not_started" | "running" | "completed" | "cancelled" | "error";
  target_ratio?: number | null;
  unique_user_tokens?: number | null;
  workload_kind: "token_prefix" | "aiperf_synthetic";
}
/**
 * Declared-versus-observed prefix reuse for one synthetic run.
 *
 * The declared ratio is a property of the generated workload; only a
 * server-reported ratio can confirm or refute it. A ratio the server did not
 * report is ``unavailable``, never an invented deviation, and it only becomes
 * a data-quality defect when the plan explicitly requires the observation.
 */
export interface CacheRatioVerification {
  declared_ratio?: number | null;
  declared_source?: "target_prefix_ratio" | "token_split";
  delta?: number | null;
  observed_ratio?: number | null;
  required?: boolean;
  status: "verified" | "mismatch" | "unavailable" | "not_applicable";
  tolerance?: number;
}
/**
 * Ordinary warmup outcome, kept out of the formal request counts.
 */
export interface PlanWarmupState {
  completed_requests?: number;
  concurrency?: number;
  executor?: "evalscope_http";
  failed_requests?: number;
  output_token_limit?: number;
  reason?: string | null;
  request_count?: number;
  status: "skipped" | "completed" | "failed" | "cancelled";
  total_input_tokens?: number;
}
/**
 * A stable, credential-free serialization that can be imported again.
 */
export interface PerfPlanExportResponse {
  can_submit: boolean;
  content: string;
  diagnostics?: PlanDiagnosticView[];
  normalized_hash: string;
  plan_hash: string;
  semantic_fingerprint: string;
}
/**
 * One structured diagnostic; never contains prompts, secrets or local paths.
 */
export interface PlanDiagnosticView {
  case_id?: string | null;
  code: string;
  field_path?: string;
  message?: string;
  message_key?: string;
  parameters?: {
    [k: string]: any;
  };
  severity: "error" | "warning" | "info";
}
/**
 * Terminal result of a synchronous plan execution.
 */
export interface PerfPlanInvokeResponse {
  result: PerfPlanOutcome;
  status: "completed" | "error" | "stopped";
  task_id: string;
}
/**
 * Small terminal result; per-run details stay in the plan manifest.
 */
export interface PerfPlanOutcome {
  cleanup_confirmed?: boolean;
  completed_runs: number;
  manifest?: string;
  plan_hash: string;
  quality: "complete" | "partial" | "invalid";
  quality_reasons?: string[];
  status: "completed" | "stopped" | "error";
  total_runs: number;
}
/**
 * A side-effect-free preview: normalized plan, matrix, budgets, diagnostics and alignment.
 */
export interface PerfPlanPreviewResponse {
  acknowledged?: string[];
  alignment: PlanAlignment;
  budgets: PlanBudgets;
  can_submit: boolean;
  compiler_version: string;
  context: ContextCheck[];
  contract_version: string;
  diagnostics: PlanDiagnosticView[];
  engine_available: boolean;
  intent: "diagnostic" | "benchmark";
  limit: number;
  normalized: PerfPlanDocument;
  normalized_hash: string;
  offset: number;
  plan_hash: string;
  requires_acknowledgement?: string[];
  runs: PlanRunSummary[];
  runtime: PlanRuntimeIdentity;
  schema_version?: "1.0";
  semantic_fingerprint: string;
  source_hash: string;
  total_runs: number;
  workload_semantics: "managed_token_prefix" | "aiperf_synthetic_compat";
}
/**
 * A portable, versioned and credential-free performance plan.
 */
export interface PerfPlanDocument {
  connection: PlanConnection;
  deployment?: DeploymentFacts;
  execution?: PlanExecution;
  intent?: "diagnostic" | "benchmark";
  kind?: "iss-eval.perf-plan";
  load: PlanLoad;
  name?: string | null;
  priming?: PlanPriming;
  schema_version?: "1.0";
  telemetry?: PlanTelemetry;
  warmup?: PlanWarmup;
  workload: TokenPrefixWorkload | SyntheticWorkload;
}
/**
 * Where the plan sends requests; never carries a credential.
 */
export interface PlanConnection {
  model: string;
  protocol?: "openai_chat";
  streaming?: true;
  tokenizer_path: string;
  url: string;
}
/**
 * Declared deployment shape; facts are recorded, never used to reprogram a cluster.
 */
export interface DeploymentFacts {
  declared_max_model_len?: number | null;
  deployment_id?: string | null;
  dp?: number | null;
  engine_image_digest?: string | null;
  engine_version?: string | null;
  ep?: number | null;
  evidence?: "declared" | "measured" | "unknown";
  gpu_count?: number | null;
  kv_dtype?: string | null;
  max_num_seqs?: number | null;
  mode?: "aggregated" | "disaggregated" | "unknown";
  model_revision?: string | null;
  node_count?: number | null;
  pp?: number | null;
  prefix_cache?: ("enabled" | "disabled" | "unknown") | null;
  routing_coverage?: "unknown" | "partial" | "all_replicas";
  routing_policy?: "round_robin" | "unknown" | "other";
  tp?: number | null;
}
/**
 * Deterministic ordering of the compiled matrix.
 */
export interface PlanExecution {
  order?: "seeded_shuffle" | "load_then_input";
  seed?: number | null;
}
/**
 * Load axis, request count and output budget shared by every run.
 */
export interface PlanLoad {
  mode?: "closed_loop";
  output_token_limit: number;
  /**
   * @minItems 1
   * @maxItems 32
   */
  points: [PlanLoadPoint, ...PlanLoadPoint[]];
  repetitions?: number;
  request_count: number;
}
/**
 * One closed-loop concurrency point.
 */
export interface PlanLoadPoint {
  concurrency: number;
}
/**
 * Prefix priming policy, separate from ordinary warmup.
 */
export interface PlanPriming {
  policy?: "prime_all_prefixes" | "none";
  timeout_seconds?: number;
}
/**
 * Node telemetry stays outside the portable document in this version.
 *
 * The Web form still supports required node metrics, but a portable plan has
 * no field that can name a collection target: the target URL and its bearer
 * token are runtime configuration. Accepting ``required=true`` here would
 * promise a collection this document cannot describe, so it is rejected
 * instead of being silently downgraded to ``required=false``.
 */
export interface PlanTelemetry {
  required?: boolean;
  targets?: {
    [k: string]: any;
  }[];
}
/**
 * Ordinary warmup sent once before the whole matrix; never counted as formal samples.
 */
export interface PlanWarmup {
  concurrency?: number;
  enabled?: boolean;
  failure_policy?: "abort" | "record_and_continue";
  input_tokens?: number;
  output_token_limit?: number;
  request_count?: number;
}
/**
 * Managed token-prefix workload: a target reuse ratio per input length.
 */
export interface TokenPrefixWorkload {
  /**
   * @minItems 1
   * @maxItems 64
   */
  input_lengths: [number, ...number[]];
  kind: "token_prefix";
  prefix_pool_size?: number;
  seed?: number;
  /**
   * @minItems 1
   * @maxItems 16
   */
  target_ratios:
    | [number]
    | [number, number]
    | [number, number, number]
    | [number, number, number, number]
    | [number, number, number, number, number]
    | [number, number, number, number, number, number]
    | [number, number, number, number, number, number, number]
    | [number, number, number, number, number, number, number, number]
    | [number, number, number, number, number, number, number, number, number]
    | [number, number, number, number, number, number, number, number, number, number]
    | [number, number, number, number, number, number, number, number, number, number, number]
    | [number, number, number, number, number, number, number, number, number, number, number, number]
    | [number, number, number, number, number, number, number, number, number, number, number, number, number]
    | [number, number, number, number, number, number, number, number, number, number, number, number, number, number]
    | [
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number
      ]
    | [
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number
      ];
}
/**
 * AIPerf synthetic compatibility workload: system + user messages, not one user blob.
 */
export interface SyntheticWorkload {
  apply_chat_template?: false;
  cache_bust?: "none";
  /**
   * @minItems 1
   * @maxItems 64
   */
  cases: [SyntheticCase, ...SyntheticCase[]];
  kind: "aiperf_synthetic";
  ratio_tolerance?: number;
  require_observed_ratio?: boolean;
}
/**
 * One explicit shared-system / unique-user split copied from a legacy script.
 */
export interface SyntheticCase {
  case_id: string;
  declared_total_input_tokens: number;
  shared_system_tokens: number;
  target_prefix_ratio?: number | null;
  unique_user_tokens: number;
}
/**
 * Bounded per-run projection of the compiled matrix.
 */
export interface PlanRunSummary {
  case_id?: string | null;
  concurrency: number;
  declared_total_input_tokens?: number | null;
  input_tokens?: number | null;
  message_structure: "user_only" | "system_and_user";
  order: number;
  output_token_limit: number;
  primer_requests: number;
  repetition: number;
  request_count: number;
  run_id: string;
  shared_system_tokens?: number | null;
  target_ratio?: number | null;
  unique_user_tokens?: number | null;
  workload_kind: "token_prefix" | "aiperf_synthetic";
}
/**
 * Identity of the runtime/tokenizer/template, explicitly unknown until checked.
 */
export interface PlanRuntimeIdentity {
  reason?: string | null;
  runtime_status?: "unknown" | "available" | "unavailable";
  runtime_version?: string | null;
  template_id?: string | null;
  tokenizer_id?: string | null;
  tokenizer_status?: "unknown" | "resolved" | "unavailable";
}
/**
 * A side-effect-free cache experiment plan and its limitations.
 */
export interface PerfPlanResponse {
  engine_available: boolean;
  metrics_probe?: "not_requested" | "completed";
  nodes?: NodePreflight[];
  plan: CacheExperimentPlan;
  warnings?: string[];
}
/**
 * A single endpoint's reachability and documented matching families.
 */
export interface NodePreflight {
  elapsed_ms: number;
  engine: "vllm" | "sglang";
  metric_names?: string[];
  node_id: string;
  reason?: string | null;
  role: "prefill" | "decode";
  status: "available" | "missing" | "unreachable" | "invalid";
  verification?: "deployment_unverified";
}
/**
 * The truthful acceptance answer for one plan submission.
 *
 * ``state`` is the state of the task this plan already owns, so an accepted
 * retry never pretends a finished run is still queued.
 */
export interface PerfPlanSubmitResponse {
  duplicate?: boolean;
  enqueued_at?: string | null;
  message?: string | null;
  queue_position?: number | null;
  state: "reserved" | "queued" | "running" | "completed" | "stopped" | "error" | "lost";
  task_id: string;
}
export interface PerfRequestsResponse {
  columns: string[];
  has_db: boolean;
  page: number;
  page_size: number;
  rows: {
    [k: string]: any;
  }[];
  total: number;
}
export interface PerfRunItem {
  dir_name: string;
  has_requests: boolean;
  name: string;
  num_requests: number;
  number: number;
  parallel: number | null;
  percentile_columns: string[];
  percentile_rows: (string | number | null)[][];
  rate: number | null;
  succeed_requests: number;
  success_rate: number | null;
  total_requests: number;
}
export interface PerfRunsListResponse {
  runs: PerfRunItem[];
  total: number;
}
/**
 * Preflight for one inline-prompt AIPerf run.
 */
export interface PerfSinglePlanResponse {
  engine_available: boolean;
  metrics_probe?: "not_requested" | "completed";
  nodes?: NodePreflight[];
  profiling_requests: number;
  warnings?: string[];
}
/**
 * Public identity and mechanics of a portable performance plan.
 *
 * Declared deployment facts are labelled as declared; the effective tokenizer
 * and template hashes come from the archive and stay ``None`` until a run
 * resolves them.
 */
export interface PlanConfigSnapshot {
  aiperf_version?: string | null;
  alias?: string | null;
  compiler_version?: string | null;
  concurrencies?: number[];
  contract_version?: string | null;
  declared_max_model_len?: number | null;
  deployment_evidence?: string | null;
  deployment_measured?: boolean | null;
  deployment_mode?: string | null;
  endpoint?: string | null;
  engine?: "aiperf";
  input_lengths?: number[];
  intent?: string | null;
  kind?: "perf";
  model?: string;
  name?: string | null;
  normalized_hash?: string | null;
  output_token_limit?: number | null;
  plan_hash?: string | null;
  primer_timeout_seconds?: number | null;
  repetitions?: number | null;
  request_count?: number | null;
  schema_version?: number;
  semantic_fingerprint?: string | null;
  snapshot_type?: "perf_plan";
  source_hash?: string | null;
  target_ratios?: number[];
  template_id?: string | null;
  test_type?: "cache_experiment";
  tokenizer_id?: string | null;
  total_runs?: number | null;
  warmup_enabled?: boolean | null;
  warmup_input_tokens?: number | null;
  warmup_output_token_limit?: number | null;
  workload_semantics?: string | null;
}
export interface PredictionRow {
  AgentTrace?: AgentTrace | null;
  Generated: string;
  Gold: string | string[];
  Index: string;
  Input: string;
  Messages?: ChatMessage[] | null;
  Metadata: any;
  NScore: number | null;
  PerfMetrics?: SamplePerfMetrics | null;
  Pred: string;
  Score: PredictionScore;
  Status?: string | null;
}
export interface AgentTrace {
  environment?: string | null;
  events: AgentTraceEvent[];
  framework?: string | null;
  max_steps: number;
  strategy?: string | null;
  total_usage?: TraceUsage | null;
  trial_id?: string | null;
}
export interface AgentTraceEvent {
  latency_ms?: number | null;
  message_id?: string | null;
  payload: {
    [k: string]: any;
  };
  step: number;
  timestamp: number;
  token_usage?: {
    [k: string]: number;
  } | null;
  type: EventType;
}
/**
 * Mirrors `evalscope.api.model.model_output.ModelUsage`'s shape for the response contract.
 */
export interface TraceUsage {
  input_tokens?: number;
  input_tokens_cache_read?: number | null;
  input_tokens_cache_write?: number | null;
  output_tokens?: number;
  reasoning_tokens?: number | null;
  total_tokens?: number;
}
/**
 * Score fields rendered by the Web UI; metadata remains benchmark-defined.
 */
export interface PredictionScore {
  explanation?: string | null;
  judge_summary?: JudgeSummary | null;
  main_score_name?: string | null;
  metadata?: PredictionMetadata | null;
  /**
   * Whether a score is usable, and why it is not.
   *
   * A failed judge is not a zero: only ``SUCCESS`` and ``FALLBACK`` carry a score that may
   * enter aggregation. The remaining values mean the score is unavailable and the sample
   * must be excluded from the affected metric rather than counted as 0.
   */
  status?: "success" | "transport_error" | "parse_error" | "invalid_session" | "fallback" | "degraded" | "excluded";
  value?: {
    [k: string]: number | boolean;
  };
  [k: string]: any;
}
export interface PredictionMetadata {
  judge_attempts?: JudgeAttempt[] | null;
  judge_skip_reason?: string | null;
  judge_skipped?: boolean | null;
  [k: string]: any;
}
export interface PredictionsResponse {
  predictions: PredictionRow[];
}
/**
 * Progress tracker payload with stable fields and tracker-specific metadata.
 */
export interface ProgressResponse {
  completed_subruns?: number | null;
  current_step?: string | null;
  current_subrun?: string | null;
  percent: number | null;
  phase?: string | null;
  quality?: ("complete" | "partial" | "invalid") | null;
  status?: string | null;
  total_subruns?: number | null;
  [k: string]: any;
}
/**
 * Readability of a task's result as a whole (independent of its quality).
 */
export interface ResultAvailability {
  availability?: "available" | "pending" | "not_configured" | "unsupported" | "unavailable" | "unknown";
  reason?: string | null;
}
/**
 * Task detail: the list projection plus the sanitized configuration snapshot.
 */
export interface TaskDetailResponse {
  alias?: string | null;
  capabilities?: ResultCapabilities;
  completed_subruns?: number | null;
  config_completeness?: "full" | "summary_only" | "unavailable";
  config_snapshot?:
    | EvalConfigSnapshot
    | NativePerfConfigSnapshot
    | AiPerfSingleConfigSnapshot
    | CacheExperimentConfigSnapshot
    | PlanConfigSnapshot
    | null;
  current_subrun?: string | null;
  effective_time?: string | null;
  endpoint?: string | null;
  engine?: ("evalscope" | "aiperf") | null;
  enqueued_at?: string | null;
  error?: string | null;
  finished_at?: string | null;
  html_report_url?: string | null;
  kind: "eval" | "perf";
  logs_available?: boolean;
  model?: string;
  observed_at: string;
  percent?: number | null;
  phase?: string | null;
  quality?: ("complete" | "partial" | "invalid") | null;
  queue_position?: number | null;
  report_url?: string | null;
  result_availability?: ResultAvailability;
  started_at?: string | null;
  status:
    "queued" | "preparing" | "running" | "cancelling" | "finalizing" | "completed" | "stopped" | "error" | "unknown";
  status_group: "executing" | "queued" | "completed" | "stopped" | "failed" | "uncertain";
  task_id: string;
  test_type?: ("single" | "sweep" | "cache_experiment" | "unknown") | null;
  time_basis?: "aware" | "legacy" | "unknown";
  total_subruns?: number | null;
}
/**
 * A filtered page of tasks plus the scope it was read from.
 */
export interface TaskListResponse {
  completeness: "complete" | "partial";
  excluded_without_time?: number;
  indexing: TaskIndexingState;
  items: TaskSummaryItem[];
  known_total: number;
  limit: number;
  observed_at: string;
  offset: number;
  revision: number;
  total: number;
}
/**
 * How much of the output root the persistent index has discovered.
 */
export interface TaskIndexingState {
  reason?: string | null;
  scanned?: number;
  state?: "ready" | "indexing" | "unavailable";
}
/**
 * One task as shown in the Task Center list or used for detail headers.
 */
export interface TaskSummaryItem {
  alias?: string | null;
  capabilities?: ResultCapabilities;
  completed_subruns?: number | null;
  current_subrun?: string | null;
  effective_time?: string | null;
  endpoint?: string | null;
  engine?: ("evalscope" | "aiperf") | null;
  enqueued_at?: string | null;
  error?: string | null;
  finished_at?: string | null;
  html_report_url?: string | null;
  kind: "eval" | "perf";
  model?: string;
  observed_at: string;
  percent?: number | null;
  phase?: string | null;
  quality?: ("complete" | "partial" | "invalid") | null;
  queue_position?: number | null;
  report_url?: string | null;
  result_availability?: ResultAvailability;
  started_at?: string | null;
  status:
    "queued" | "preparing" | "running" | "cancelling" | "finalizing" | "completed" | "stopped" | "error" | "unknown";
  status_group: "executing" | "queued" | "completed" | "stopped" | "failed" | "uncertain";
  task_id: string;
  test_type?: ("single" | "sweep" | "cache_experiment" | "unknown") | null;
  time_basis?: "aware" | "legacy" | "unknown";
  total_subruns?: number | null;
}
export interface TaskStatusResponse {
  status: string;
  task_id: string;
}
/**
 * Acknowledgement that a task was accepted for background execution.
 */
export interface TaskSubmitResponse {
  enqueued_at?: string | null;
  queue_position?: number | null;
  status?: "queued";
  task_id: string;
}
/**
 * Global task counts; never derived from a single page of list items.
 */
export interface TaskSummaryResponse {
  attention: number;
  completed_in_range: number;
  completeness?: "complete" | "partial";
  excluded_from_range?: number;
  executing: number;
  observed_at: string;
  queue: QueueConnectivity;
  queued: number;
  revision?: number;
  scope?: "global" | "global+range";
  unknown_timestamp_count?: number;
}
export interface QueueConnectivity {
  backend: string;
  reachable: boolean;
  reason?: string | null;
}
