/**
 * Timestamp interpretation shared by the Task Center list and detail header.
 *
 * Legacy manifests were written without an offset. The browser must not invent
 * one: it reads them in local time (the service's own convention) and marks the
 * basis so the UI can say so instead of presenting a guessed instant as exact.
 */

export type TimeBasis = 'aware' | 'legacy' | 'unknown'

export interface ParsedTaskTime {
  ms: number
  basis: Exclude<TimeBasis, 'unknown'>
}

const OFFSET_SUFFIX = /(?:[zZ]|[+-]\d{2}:?\d{2})$/

/** Parse one task timestamp, reporting whether its offset was explicit. */
export function parseTaskTime(value: string | null | undefined): ParsedTaskTime | null {
  if (typeof value !== 'string' || !value.trim()) return null
  const ms = Date.parse(value)
  if (!Number.isFinite(ms)) return null
  return { ms, basis: OFFSET_SUFFIX.test(value.trim()) ? 'aware' : 'legacy' }
}

/**
 * The backend reports the effective sorting/filtering time and its basis.
 * Fall back to the raw timestamps only when an older payload omits them.
 */
export function effectiveTaskTime(item: {
  effective_time?: string | null
  time_basis?: TimeBasis
  finished_at?: string | null
  started_at?: string | null
  enqueued_at?: string | null
}): ParsedTaskTime | null {
  if (item.effective_time) {
    const parsed = parseTaskTime(item.effective_time)
    if (parsed) {
      return { ms: parsed.ms, basis: item.time_basis === 'aware' ? 'aware' : parsed.basis }
    }
  }
  for (const candidate of [item.finished_at, item.started_at, item.enqueued_at]) {
    const parsed = parseTaskTime(candidate)
    if (parsed) return parsed
  }
  return null
}

/** Translation key describing how the timestamp was interpreted. */
export function timeBasisLabelKey(basis: TimeBasis | null | undefined): string {
  if (basis === 'legacy') return 'tasks.time.legacy'
  if (basis === 'aware') return 'tasks.time.aware'
  return 'tasks.time.unknown'
}