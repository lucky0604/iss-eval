import { apiDeleteValidated, apiPostValidated, apiValidated } from './client'
import type { PerfEnginesResponse, PerfInvokeResponse, PerfLiveResponse, PerfPlanResponse, PerfSinglePlanResponse, CacheExperimentResponse, CacheExperimentListResponse, CacheScenarioResponse, CacheNodeMetricsResponse, CacheRequestsResponse, CacheRequestGroupsResponse, CacheRepetitionsResponse, CacheNodeSeriesResponse } from './generated/contracts'
import type {
  DeletePerfRunResponse,
  ListPerfRunsResponse,
  PerfDetailResponse,
  PerfRequestsResponse,
  PerfRunsListResponse,
} from './types'
import { createTaskApi } from './task'

const perfTaskApi = createTaskApi('perf')

export const submitPerfTask = perfTaskApi.submit
export const getPerfProgress = perfTaskApi.progress
export const getPerfLog = perfTaskApi.log
export const getPerfReportUrl = perfTaskApi.reportUrl
export const stopPerfTask = perfTaskApi.stop

export function getPerfEngines(signal?: AbortSignal): Promise<PerfEnginesResponse> {
  return apiValidated('/api/v1/perf/engines', { signal })
}

export function getLivePerfMetrics(taskId: string, signal?: AbortSignal): Promise<PerfLiveResponse> {
  return apiValidated(`/api/v1/perf/live/${encodeURIComponent(taskId)}`, { signal })
}

export function validatePerfExperiment(payload: Record<string, unknown>, taskId: string, signal?: AbortSignal,
                                       probeMetrics = false): Promise<PerfPlanResponse> {
  return apiPostValidated('/api/v1/perf/validate', payload, {
    headers: { 'EvalScope-Task-Id': taskId }, signal, params: { probe_metrics: probeMetrics ? 1 : 0 },
  })
}

export function submitPerfExperiment(payload: Record<string, unknown>, taskId: string): Promise<PerfInvokeResponse> {
  return validatePerfExperiment(payload, taskId).then((preview) => apiPostValidated('/api/v1/perf/invoke', payload, {
    headers: { 'EvalScope-Task-Id': taskId, 'EvalScope-Plan-Hash': preview.plan.plan_hash },
  }))
}

export function validatePerfSingle(payload: Record<string, unknown>, taskId: string, signal?: AbortSignal,
                                   probeMetrics = false): Promise<PerfSinglePlanResponse> {
  return apiPostValidated('/api/v1/perf/validate-single', payload, {
    headers: { 'EvalScope-Task-Id': taskId }, signal, params: { probe_metrics: probeMetrics ? 1 : 0 },
  })
}

export async function submitPerfSingle(payload: Record<string, unknown>, taskId: string): Promise<PerfInvokeResponse> {
  await validatePerfSingle(payload, taskId)
  return apiPostValidated('/api/v1/perf/invoke', payload, { headers: { 'EvalScope-Task-Id': taskId } })
}

export function getCacheExperiment(taskId: string, signal?: AbortSignal): Promise<CacheExperimentResponse> {
  return apiValidated(`/api/v1/perf/experiments/${encodeURIComponent(taskId)}`, { signal })
}

export function listCacheExperiments(offset = 0): Promise<CacheExperimentListResponse> {
  return apiValidated('/api/v1/perf/experiments', { params: { offset, limit: 50 } })
}

export function getCacheScenario(taskId: string, subrunId: string, signal?: AbortSignal): Promise<CacheScenarioResponse> {
  return apiValidated(`/api/v1/perf/experiments/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(subrunId)}`, { signal })
}

export function getCacheRepetitions(taskId: string, subrunId: string, metric: string, offset: number,
                                    signal?: AbortSignal): Promise<CacheRepetitionsResponse> {
  return apiValidated(`/api/v1/perf/experiments/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(subrunId)}/repetitions`, {
    params: { metric, offset, limit: 100 }, signal,
  })
}

interface NodeSeriesFilter {
  nodeId: string; metricName: string; labels: Record<string, string>; start?: number | null; end?: number | null;
  maxPoints?: number; signal?: AbortSignal
}

export function getCacheNodeSeries(taskId: string, subrunId: string, filter: NodeSeriesFilter): Promise<CacheNodeSeriesResponse> {
  return apiValidated(`/api/v1/perf/experiments/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(subrunId)}/metrics/series`, {
    params: { node_id: filter.nodeId, metric_name: filter.metricName, labels: JSON.stringify(filter.labels),
      start_ns: filter.start ?? undefined, end_ns: filter.end ?? undefined, max_points: filter.maxPoints ?? 500 }, signal: filter.signal,
  })
}

export function getPerfNodeSeries(rootPath: string, path: string, filter: NodeSeriesFilter): Promise<CacheNodeSeriesResponse> {
  return apiValidated('/api/v1/perf/server-metrics', {
    params: { root_path: rootPath, path, downsample: 'uniform_index', node_id: filter.nodeId,
      metric_name: filter.metricName, labels: JSON.stringify(filter.labels), start_ns: filter.start ?? undefined,
      end_ns: filter.end ?? undefined, max_points: filter.maxPoints ?? 500 }, signal: filter.signal,
  })
}

export function getCacheNodeMetrics(taskId: string, subrunId: string, offset = 0,
  filter?: { nodeId: string; metricName: string; signal?: AbortSignal }): Promise<CacheNodeMetricsResponse> {
  return apiValidated(`/api/v1/perf/experiments/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(subrunId)}/metrics`, {
    params: { offset, limit: filter ? 500 : 100, node_id: filter?.nodeId, metric_name: filter?.metricName }, signal: filter?.signal,
  })
}

export function getCacheRequests(taskId: string, subrunId: string, offset = 0): Promise<CacheRequestsResponse> {
  return apiValidated(`/api/v1/perf/experiments/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(subrunId)}/requests`, {
    params: { offset, limit: 50 },
  })
}

export function getCacheRequestGroups(taskId: string, subrunId: string, signal?: AbortSignal): Promise<CacheRequestGroupsResponse> {
  return apiValidated(`/api/v1/perf/experiments/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(subrunId)}/request-cache-groups`, { signal })
}

// ------------------------------------------------------------------ //
// Historical perf-run archive                                         //
// ------------------------------------------------------------------ //

export async function listPerfRuns(rootPath: string, signal?: AbortSignal): Promise<ListPerfRunsResponse> {
  return apiValidated<ListPerfRunsResponse>('/api/v1/perf/list', {
    params: { root_path: rootPath },
    signal,
  })
}

export async function deletePerfRun(
  rootPath: string,
  path: string,
  signal?: AbortSignal,
): Promise<DeletePerfRunResponse> {
  return apiDeleteValidated<DeletePerfRunResponse>('/api/v1/perf/run', {
    params: { root_path: rootPath, path },
    signal,
  })
}

export async function getPerfDetail(
  rootPath: string,
  path: string,
  signal?: AbortSignal,
): Promise<PerfDetailResponse> {
  return apiValidated<PerfDetailResponse>('/api/v1/perf/detail', {
    params: { root_path: rootPath, path },
    signal,
  })
}

export async function listPerfRunDetails(
  rootPath: string,
  path: string,
  signal?: AbortSignal,
): Promise<PerfRunsListResponse> {
  return apiValidated<PerfRunsListResponse>('/api/v1/perf/runs', {
    params: { root_path: rootPath, path },
    signal,
  })
}

export async function getPerfRequests(params: {
  rootPath: string
  path: string
  run: string
  status?: 'success' | 'failed'
  page?: number
  pageSize?: number
  signal?: AbortSignal
}): Promise<PerfRequestsResponse> {
  return apiValidated<PerfRequestsResponse>('/api/v1/perf/requests', {
    params: {
      root_path: params.rootPath,
      path: params.path,
      run: params.run,
      status: params.status,
      page: params.page,
      page_size: params.pageSize,
    },
    signal: params.signal,
  })
}

export function getPerfChartUrl(
  rootPath: string,
  path: string,
  chartType: string,
  opts: { run?: string; theme?: string } = {},
): string {
  const params = new URLSearchParams({ root_path: rootPath, path, chart_type: chartType })
  if (opts.run) params.set('run', opts.run)
  if (opts.theme) params.set('theme', opts.theme)
  return `/api/v1/perf/chart?${params.toString()}`
}

export function getPerfHistoryReportUrl(rootPath: string, path: string): string {
  const params = new URLSearchParams({ root_path: rootPath, path })
  return `/api/v1/perf/history/report?${params.toString()}`
}

export function getPerfCompareChartUrl(
  rootPath: string,
  paths: string[],
  chartType: string,
  theme?: string,
): string {
  const params = new URLSearchParams({ root_path: rootPath, paths: paths.join(';'), chart_type: chartType })
  if (theme) params.set('theme', theme)
  return `/api/v1/perf/compare/chart?${params.toString()}`
}
