/**
 * Typed selection rules for the unified performance-result list.
 *
 * A list row can be opened, compared, deleted, viewed as HTML or have its
 * configuration copied, but only some of those actions apply to each result
 * kind. The backend already publishes the legal actions per entry; these
 * helpers turn a selection into the actions it may actually perform, so a mixed
 * selection (native run + cache experiment) can never trigger `deletePerfRun`
 * on an experiment path or send an experiment into the native compare view.
 */
import type { PerformanceResultEntry } from '@/api/types'

export type EntryAction = 'open' | 'compare' | 'delete' | 'html' | 'copy_config'

export interface SelectionSupport {
  count: number
  canCompare: boolean
  canDelete: boolean
  canViewHtml: boolean
  /** True when the selection is large enough for the action but some entry vetoes it. */
  compareBlocked: boolean
  deleteBlocked: boolean
  viewHtmlBlocked: boolean
  /** Why delete is unavailable: experiment archives, read-only external runs, or a mismatch. */
  deleteBlockedReason: 'experiment' | 'read_only' | 'mixed' | null
}

export function entryHasAction(entry: PerformanceResultEntry, action: EntryAction): boolean {
  return (entry.actions ?? []).includes(action)
}

export function isEntryOpenable(entry: PerformanceResultEntry): boolean {
  return entryHasAction(entry, 'open') && Boolean(entry.detail_path)
}

/**
 * The experiment directory id used to address its analysis page.
 *
 * The service root exposes the identity as `task_id`; an external read-only
 * archive deliberately omits it so it can never control the service's task of
 * the same name. The plan directory still names the experiment, so the detail
 * link is built from the archive path instead.
 */
export function experimentIdOf(entry: PerformanceResultEntry): string | null {
  if (entry.result_kind !== 'experiment') return null
  if (entry.task_id) return entry.task_id
  const segments = entry.path.replace(/\\/g, '/').split('/').filter(Boolean)
  return segments.length >= 2 ? segments[segments.length - 2] : null
}

export function selectionSupport(selected: PerformanceResultEntry[]): SelectionSupport {
  const count = selected.length
  const all = (action: EntryAction) => count > 0 && selected.every((entry) => entryHasAction(entry, action))
  const canCompare = count >= 2 && all('compare')
  const canDelete = count >= 1 && all('delete')
  const canViewHtml = count === 1 && entryHasAction(selected[0], 'html')
  return {
    count,
    canCompare,
    canDelete,
    canViewHtml,
    compareBlocked: count >= 2 && !canCompare,
    deleteBlocked: count >= 1 && !canDelete,
    viewHtmlBlocked: count === 1 && !canViewHtml,
    deleteBlockedReason: count === 0 || canDelete
      ? null
      : selected.every((entry) => entry.result_kind === 'experiment')
        ? 'experiment'
        : selected.every((entry) => entry.result_kind === 'run' && isEntryOpenable(entry))
          ? 'read_only'
          : 'mixed',
  }
}
