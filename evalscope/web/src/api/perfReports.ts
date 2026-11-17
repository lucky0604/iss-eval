/**
 * Typed reads of the unified performance report snapshot.
 *
 * Every archived kind (portable plan, cache experiment, single AIPerf run and
 * native archive) answers the same four shapes, so one table, one chart panel
 * and one export path can serve all of them.
 */
import { apiPostValidated, apiValidated } from './client'
import type {
  PerfExportArtifact,
  PerfExportCapabilitiesResponse,
  PerfExportResponse,
  PerfExportStatusResponse,
  PerfReportChartsResponse,
  PerfReportRequestsResponse,
  PerfReportRunsResponse,
  PerfReportSummaryResponse,
} from './generated/contracts'
import type { ReportRunSort } from '@/domain/perf/reportTable'

/** Where the report lives: a task id, or a path inside the outputs root. */
export interface ReportLocation {
  taskId?: string
  rootPath?: string
  path?: string
  /** Pin every read to one snapshot; the service answers 409 once it has moved. */
  snapshotId?: string
}

function locationParams(location: ReportLocation): Record<string, unknown> {
  return {
    task_id: location.taskId || undefined,
    root_path: location.rootPath || undefined,
    path: location.path || undefined,
    snapshot_id: location.snapshotId || undefined,
  }
}

export function getPerfReportSummary(
  location: ReportLocation,
  signal?: AbortSignal,
): Promise<PerfReportSummaryResponse> {
  return apiValidated('/api/v1/perf/reports', { params: locationParams(location), signal })
}

export interface ReportRunsQuery {
  offset?: number
  limit?: number
  group?: string
  sort?: ReportRunSort
  order?: 'asc' | 'desc'
}

export function getPerfReportRuns(
  location: ReportLocation,
  query: ReportRunsQuery = {},
  signal?: AbortSignal,
): Promise<PerfReportRunsResponse> {
  return apiValidated('/api/v1/perf/reports/runs', {
    params: { ...locationParams(location), ...query },
    signal,
  })
}

export interface ReportRequestsQuery {
  runKey: string
  offset?: number
  limit?: number
  status?: 'success' | 'failed'
  phase?: 'warmup' | 'profiling' | 'all'
  search?: string
  sort?: 'index' | 'latency' | 'ttft' | 'tpot' | 'prompt_tokens' | 'output_tokens'
  order?: 'asc' | 'desc'
}

export function getPerfReportRequests(
  location: ReportLocation,
  query: ReportRequestsQuery,
  signal?: AbortSignal,
): Promise<PerfReportRequestsResponse> {
  const { runKey, ...rest } = query
  return apiValidated('/api/v1/perf/reports/requests', {
    params: { ...locationParams(location), run_key: runKey, ...rest },
    signal,
  })
}

export type ReportChartMetric = 'ttft' | 'tpot' | 'latency' | 'input_tps' | 'output_tps' | 'rpm'
export type ReportChartStatistic = 'mean' | 'p50' | 'p90' | 'p95' | 'p99' | 'min' | 'max'

export function getPerfReportCharts(
  location: ReportLocation,
  query: { metric: ReportChartMetric; statistic: ReportChartStatistic },
  signal?: AbortSignal,
): Promise<PerfReportChartsResponse> {
  return apiValidated('/api/v1/perf/reports/charts', {
    params: { ...locationParams(location), ...query },
    signal,
  })
}

export type PerfExportKind = 'html' | 'pdf' | 'csv' | 'requests' | 'bundle'

/**
 * One export request. The server expands it into the archive's full snapshot:
 * every tier for the matrix exports, every matching request for `requests`.
 *
 * The export owns its own id and record, so asking for the same specification
 * twice returns the same independent export instead of touching the load test.
 */
export interface PerfExportRequest {
  kind: PerfExportKind
  location: ReportLocation
  group?: string
  preset?: 'core' | 'all'
  locale?: 'en' | 'zh'
  status?: 'success' | 'failed'
  search?: string
  runKey?: string
  phase?: 'warmup' | 'profiling' | 'all'
}

export function createPerfExport(request: PerfExportRequest, signal?: AbortSignal): Promise<PerfExportResponse> {
  const body: Record<string, unknown> = { kind: request.kind }
  const optional = {
    task_id: request.location.taskId,
    root_path: request.location.rootPath,
    path: request.location.path,
    group: request.group,
    preset: request.preset,
    locale: request.locale,
    status: request.status,
    search: request.search,
    run_key: request.runKey,
    phase: request.phase,
  }
  for (const [key, value] of Object.entries(optional)) {
    if (value !== undefined && value !== null && value !== '') body[key] = value
  }
  return apiPostValidated('/api/v1/perf/reports/exports', body, { signal })
}

export function getPerfExportStatus(exportId: string, signal?: AbortSignal): Promise<PerfExportStatusResponse> {
  return apiValidated(`/api/v1/perf/reports/exports/${encodeURIComponent(exportId)}`, { signal })
}

export function cancelPerfExport(exportId: string, signal?: AbortSignal): Promise<PerfExportStatusResponse> {
  return apiPostValidated(`/api/v1/perf/reports/exports/${encodeURIComponent(exportId)}/cancel`, {}, { signal })
}

export function retryPerfExport(exportId: string, signal?: AbortSignal): Promise<PerfExportResponse> {
  return apiPostValidated(`/api/v1/perf/reports/exports/${encodeURIComponent(exportId)}/retry`, {}, { signal })
}

/** Whether this host can render PDF (Playwright or a local Chrome/Chromium). */
export function getPerfExportCapabilities(signal?: AbortSignal): Promise<PerfExportCapabilitiesResponse> {
  return apiValidated('/api/v1/perf/reports/export-capabilities', { signal })
}

/** Id-addressed artifact URL: a request never supplies a filesystem path. */
export function perfExportArtifactUrl(exportId: string, artifact: Pick<PerfExportArtifact, 'name' | 'url'>): string {
  if (artifact.url) return artifact.url
  return `/api/v1/perf/reports/exports/${encodeURIComponent(exportId)}/download?file=${encodeURIComponent(artifact.name)}`
}
