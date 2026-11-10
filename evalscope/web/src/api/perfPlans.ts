/**
 * Portable performance plan endpoints.
 *
 * Preview and export are side-effect free; invoke re-runs every check on the
 * server. All three accept the plan as raw text so the server, not the browser,
 * decides what the document means.
 */

import { apiPostValidated, apiValidated } from './client'
import type {
  CacheRequestsResponse,
  PerfPlanArchiveResponse,
  PerfPlanExportResponse,
  PerfPlanPreviewResponse,
  PerfPlanSubmitResponse,
  PlanDiagnosticView,
} from './generated/contracts'

/** Diagnostics carried by a rejected preview or submission. */
export interface PlanErrorPayload {
  error: string
  diagnostics?: PlanDiagnosticView[]
  missing_acknowledgements?: string[]
  plan_hash?: string
}

export function previewPerfPlan(
  content: string,
  options: { acknowledged?: string[]; offset?: number; limit?: number; signal?: AbortSignal } = {},
): Promise<PerfPlanPreviewResponse> {
  return apiPostValidated('/api/v1/perf/plans/preview', { content, acknowledged: options.acknowledged ?? [] }, {
    params: { offset: options.offset ?? 0, limit: options.limit ?? 100 },
    signal: options.signal,
  })
}

export function exportPerfPlan(content: string, signal?: AbortSignal): Promise<PerfPlanExportResponse> {
  return apiPostValidated('/api/v1/perf/plans/export', { content }, { signal })
}

/**
 * Submit a plan for one explicit task id.
 *
 * `expectedPlanHash` binds the submission to the preview the user approved: an
 * edited document produces a different hash and the server refuses it.
 */
export function submitPerfPlan(
  content: string,
  taskId: string,
  options: { acknowledged?: string[]; expectedPlanHash?: string } = {},
): Promise<PerfPlanSubmitResponse> {
  const headers: Record<string, string> = { 'EvalScope-Task-Id': taskId }
  if (options.expectedPlanHash) headers['EvalScope-Plan-Hash'] = options.expectedPlanHash
  return apiPostValidated(
    '/api/v1/perf/plans/invoke',
    { content, acknowledged: options.acknowledged ?? [], expected_plan_hash: options.expectedPlanHash },
    { headers, params: { async: 1 } },
  )
}

export function getPerfPlanArchive(taskId: string, signal?: AbortSignal): Promise<PerfPlanArchiveResponse> {
  return apiValidated(`/api/v1/perf/plans/${encodeURIComponent(taskId)}`, { signal })
}

/** Archive of a plan that lives under an explicit outputs root. */
export function getPerfPlanArchiveAt(
  rootPath: string,
  path: string,
  options: { offset?: number; limit?: number; signal?: AbortSignal } = {},
): Promise<PerfPlanArchiveResponse> {
  return apiValidated('/api/v1/perf/plans/archive', {
    params: { root_path: rootPath, path, offset: options.offset ?? 0, limit: options.limit ?? 100 },
    signal: options.signal,
  })
}

/** Request page of one planned run, resolved by archive path. */
export function getPerfPlanRunRequests(
  rootPath: string,
  path: string,
  runId: string,
  options: { offset?: number; limit?: number; phase?: string; signal?: AbortSignal } = {},
): Promise<CacheRequestsResponse> {
  return apiValidated('/api/v1/perf/plans/requests', {
    params: {
      root_path: rootPath,
      path,
      run_id: runId,
      offset: options.offset ?? 0,
      limit: options.limit ?? 100,
      ...(options.phase ? { phase: options.phase } : {}),
    },
    signal: options.signal,
  })
}

/** Standalone HTML report URL for a plan under an explicit outputs root. */
export function perfPlanReportUrl(rootPath: string, path: string): string {
  const query = new URLSearchParams({ root_path: rootPath, path })
  return `/api/v1/perf/plans/report?${query.toString()}`
}
