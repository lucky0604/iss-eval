/**
 * Task status semantics shared by the Task Center, the detail page and the
 * creation monitor.
 *
 * The backend already groups raw statuses; these helpers exist so the UI never
 * re-derives them differently per component, and so the two rules that have
 * burned users before stay testable in one place:
 *
 * - a 100% progress value is not a finished process (only a terminal status is);
 * - a missing or non-finite percentage is "indeterminate", never 0%.
 */
import type { TaskSummaryItem } from '@/api/types'

export type RawTaskStatus = TaskSummaryItem['status']
export type StatusGroup = TaskSummaryItem['status_group']

const GROUPS: Record<RawTaskStatus, StatusGroup> = {
  queued: 'queued',
  preparing: 'executing',
  running: 'executing',
  cancelling: 'executing',
  finalizing: 'executing',
  completed: 'completed',
  stopped: 'stopped',
  error: 'failed',
  unknown: 'uncertain',
}

export const TERMINAL_GROUPS: ReadonlySet<StatusGroup> = new Set(['completed', 'stopped', 'failed'])
export const ACTIVE_GROUPS: ReadonlySet<StatusGroup> = new Set(['executing', 'queued'])

const RAW_STATUSES = new Set<string>(Object.keys(GROUPS))

/** Group a raw status; unknown values are uncertain rather than assumed running. */
export function statusGroupOf(status: string | null | undefined): StatusGroup {
  return GROUPS[status as RawTaskStatus] ?? 'uncertain'
}

export function isTerminalStatus(status: string | null | undefined): boolean {
  return TERMINAL_GROUPS.has(statusGroupOf(status))
}

export function isActiveStatus(status: string | null | undefined): boolean {
  return ACTIVE_GROUPS.has(statusGroupOf(status))
}

/** Whether the status has a dedicated translation; used to pick a safe label. */
export function hasStatusLabel(status: string | null | undefined): status is RawTaskStatus {
  return typeof status === 'string' && RAW_STATUSES.has(status)
}

/** Translation key for a raw status, falling back to the uncertain label. */
export function statusLabelKey(status: string | null | undefined): string {
  const safe = hasStatusLabel(status) ? status : 'unknown'
  return `tasks.state.${safe}`
}

export type ProgressKind = 'queued' | 'indeterminate' | 'determinate' | 'finished'

/**
 * How a row's progress should render.
 *
 * A finished task shows a finished label instead of a full bar; a queued task
 * shows its queue position; anything without a trustworthy percentage is
 * indeterminate, never 0%.
 */
export function progressKind(item: Pick<TaskSummaryItem, 'status' | 'percent'>): ProgressKind {
  if (isTerminalStatus(item.status)) return 'finished'
  if (item.status === 'queued') return 'queued'
  return progressPercent(item) === null ? 'indeterminate' : 'determinate'
}

/** A finite, clamped percentage, or `null` when the backend has none. */
export function progressPercent(item: Pick<TaskSummaryItem, 'percent'>): number | null {
  const value = item.percent
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return Math.max(0, Math.min(100, value))
}

/** Active items are the only ones that need status polling. */
export function countsAsActive(item: Pick<TaskSummaryItem, 'status'>): boolean {
  return isActiveStatus(item.status)
}
