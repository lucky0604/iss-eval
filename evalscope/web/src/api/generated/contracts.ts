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
  analysis_response: AnalysisResponse;
  benchmark_entry: BenchmarkEntry;
  benchmarks_response: BenchmarksResponse;
  cache_experiment_list_response: CacheExperimentListResponse;
  cache_experiment_response: CacheExperimentResponse;
  cache_node_metrics_response: CacheNodeMetricsResponse;
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
  eval_invoke_response: EvalInvokeResponse;
  execution_summary: ExecutionSummary;
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
  percentile_stats: PercentileStats;
  perf_detail_response: PerfDetailResponse;
  perf_engines_response: PerfEnginesResponse;
  perf_invoke_response: PerfInvokeResponse;
  perf_live_response: PerfLiveResponse;
  perf_metrics: PerfMetrics;
  perf_plan_response: PerfPlanResponse;
  perf_requests_response: PerfRequestsResponse;
  perf_run_item: PerfRunItem;
  perf_run_summary: PerfRunSummary;
  perf_runs_list_response: PerfRunsListResponse;
  perf_single_plan_response: PerfSinglePlanResponse;
  prediction_row: PredictionRow;
  prediction_score: PredictionScore;
  predictions_response: PredictionsResponse;
  progress_response: ProgressResponse;
  report_data: ReportData;
  report_group: ReportGroup;
  report_metric: ReportMetric;
  report_summary: ReportSummary;
  task_status_response: TaskStatusResponse;
  tool_call: PredictionToolCall;
  value_range: ValueRange;
  [k: string]: any;
}
/**
 * Display identity of a task whose subprocess is currently registered.
 */
export interface ActiveTaskInfo {
  alias?: string | null;
  completed_subruns?: number | null;
  config?: TaskConfigSummary | null;
  current_subrun?: string | null;
  endpoint?: string | null;
  engine?: ("evalscope" | "aiperf") | null;
  html_report_url?: string | null;
  kind: string;
  model?: string;
  percent?: number | null;
  phase?: string | null;
  quality?: ("complete" | "partial" | "invalid") | null;
  report_url?: string | null;
  started_at?: string;
  status?: "preparing" | "running" | "cancelling" | "finalizing" | "completed" | "stopped" | "error" | "unknown";
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
export interface AnalysisResponse {
  analysis: string;
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
  plan_hash: string;
  primer_requests: number;
  profiling_requests: number;
  schema_version?: "1.0";
  subruns: CacheSubrunPlan[];
  template_id: string;
  tokenizer_id: string;
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
  mode?: "token_prefix_ratio";
  prefix_pool_size?: number;
  routing_coverage?: "observed";
  routing_policy?: "router_native";
  seed?: number;
  /**
   * @minItems 1
   */
  target_ratios: [number, ...number[]];
  total_input_tokens: number;
  warmup_policy?: "prime_all_prefixes";
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
}
export interface CacheSubrunState {
  quality?: ("complete" | "partial" | "invalid") | null;
  scenario?: string | null;
  status: "not_started" | "running" | "completed" | "cancelled" | "error";
  subrun_id: string;
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
export interface PerfDetailResponse {
  alias?: string | null;
  api_type: string;
  basic_info: {
    [k: string]: string;
  };
  best_config: {
    [k: string]: string;
  };
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
  total_requests: number;
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
export interface TaskStatusResponse {
  status: string;
  task_id: string;
}
