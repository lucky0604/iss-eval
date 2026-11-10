/**
 * Portable performance plan helpers shared by the import panel.
 *
 * The browser never parses-and-reuploads a plan: a `JSON.parse` round trip would
 * silently drop a duplicate object key, so the original text is what the server
 * receives. Everything here works on documents the server has already accepted.
 */

import type { PerfPlanDocument, PerfPlanPreviewResponse } from '../../api/generated/contracts'

/** Server-side limit on the raw plan text, in UTF-8 bytes. */
export const PLAN_LIMIT_BYTES = 1024 * 1024

/** A single declared parameter that changed between two plans. */
export interface PlanParameterDiff {
  /** Dotted path of the changed value. */
  path: string
  before: string
  after: string
}

/** One row of the compiled matrix rendered in the preview table. */
export interface PlanMatrixRow {
  runId: string
  order: number
  workload: string
  structure: string
  concurrency: number
  requests: number
  primerRequests: number
  input: string
  ratio: string
}

/**
 * Return an error message when the file cannot be a plan, or `null` when its
 * size is acceptable. Size is checked before any upload attempt.
 */
export function checkPlanFileSize(bytes: number): string | null {
  if (bytes <= 0) return 'tasks.aiperf.planImport.errorEmpty'
  if (bytes > PLAN_LIMIT_BYTES) return 'tasks.aiperf.planImport.errorTooLarge'
  return null
}

function flatten(value: unknown, prefix: string, into: Map<string, string>): void {
  if (value === null || value === undefined) {
    into.set(prefix, 'null')
    return
  }
  if (Array.isArray(value)) {
    into.set(prefix, JSON.stringify(value))
    return
  }
  if (typeof value === 'object') {
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      flatten(child, prefix ? `${prefix}.${key}` : key, into)
    }
    return
  }
  into.set(prefix, String(value))
}

/**
 * Compare two accepted plans field by field.
 *
 * Paths that exist on one side only are still reported, so dropping the warmup
 * block or renaming a case can never look like "no change".
 */
export function diffPlanParameters(before: PerfPlanDocument, after: PerfPlanDocument): PlanParameterDiff[] {
  const left = new Map<string, string>()
  const right = new Map<string, string>()
  flatten(before as unknown, '', left)
  flatten(after as unknown, '', right)
  const paths = new Set([...left.keys(), ...right.keys()])
  const diffs: PlanParameterDiff[] = []
  for (const path of [...paths].sort()) {
    const oldValue = left.get(path) ?? '(absent)'
    const newValue = right.get(path) ?? '(absent)'
    if (oldValue !== newValue) diffs.push({ path, before: oldValue, after: newValue })
  }
  return diffs
}

/** Render a target ratio as a percentage without inventing precision. */
export function formatRatio(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return '—'
  return `${(ratio * 100).toFixed(0)}%`
}

/** Render an observed-versus-declared reuse ratio; an explicit 0 stays 0 %, never "unknown". */
export function formatReuseRatio(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return '—'
  return `${(ratio * 100).toFixed(1)}%`
}

/** Render a ratio difference in percentage points, keeping the sign visible. */
export function formatRatioDelta(delta: number | null | undefined): string {
  if (delta === null || delta === undefined) return '—'
  const sign = delta > 0 ? '+' : ''
  return `${sign}${(delta * 100).toFixed(1)} pp`
}

/** Render a symmetric tolerance band in percentage points. */
export function formatRatioTolerance(tolerance: number | null | undefined): string {
  if (tolerance === null || tolerance === undefined) return '±—'
  return `±${(tolerance * 100).toFixed(1)} pp`
}

/** Group thousands so long token counts stay readable in the matrix. */
export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/** Project the bounded preview into table rows. */
export function matrixRows(preview: PerfPlanPreviewResponse): PlanMatrixRow[] {
  return preview.runs.map((run) => ({
    runId: run.run_id,
    order: run.order,
    workload: run.workload_kind,
    structure: run.message_structure,
    concurrency: run.concurrency,
    requests: run.request_count,
    primerRequests: run.primer_requests,
    input: run.workload_kind === 'token_prefix' ? formatTokens(run.input_tokens) : `${formatTokens(run.shared_system_tokens)} + ${formatTokens(run.unique_user_tokens)}`,
    ratio: formatRatio(run.target_ratio),
  }))
}

/** Codes the preview requires but the operator has not confirmed yet. */
export function missingAcknowledgements(required: string[], selected: string[]): string[] {
  const chosen = new Set(selected)
  return required.filter((code) => !chosen.has(code))
}

/** Whether a plan can be submitted from the current client state. */
export function canSubmitPlan(
  preview: PerfPlanPreviewResponse | null,
  selected: string[],
): { allowed: boolean; reason: 'ready' | 'noPreview' | 'blocked' | 'engineUnavailable' | 'missingAcknowledgement' } {
  if (!preview) return { allowed: false, reason: 'noPreview' }
  if (!preview.engine_available) return { allowed: false, reason: 'engineUnavailable' }
  if (!preview.can_submit) return { allowed: false, reason: 'blocked' }
  if (missingAcknowledgements(preview.requires_acknowledgement ?? [], selected).length > 0) {
    return { allowed: false, reason: 'missingAcknowledgement' }
  }
  return { allowed: true, reason: 'ready' }
}

/** Build a stable, filesystem-safe export name from the plan name. */
export function planExportFileName(name: string | null | undefined): string {
  const slug = (name ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
  return `${slug || 'perf-plan'}.perf-plan.json`
}

/** True when the text looks like a portable plan rather than a config library. */
export function isPortablePlanText(text: string): boolean {
  return /"kind"\s*:\s*"iss-eval\.perf-plan"/.test(text)
}
