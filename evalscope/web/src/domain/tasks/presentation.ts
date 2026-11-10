/**
 * Small presentation rules shared by the Task Center list and the detail page.
 *
 * Kept out of the pages so a row and its detail header cannot disagree about
 * what a task is called or which colour its status gets.
 */
import type { TaskSummaryItem } from '@/api/types'
import { statusGroupOf } from './status'

export type StatusBadgeVariant = 'default' | 'success' | 'warning' | 'danger'

/** Human name for a task: the run alias, then the model, then the ID. */
export function taskTitle(item: Pick<TaskSummaryItem, 'alias' | 'model' | 'task_id'>): string {
  return item.alias || item.model || item.task_id
}

/** Badge severity for a raw status, grouped the same way the backend groups it. */
export function statusBadgeVariant(status: string | null | undefined): StatusBadgeVariant {
  switch (statusGroupOf(status)) {
    case 'completed':
      return 'success'
    case 'failed':
      return 'danger'
    case 'uncertain':
    case 'stopped':
      return 'warning'
    default:
      return 'default'
  }
}

/**
 * Format an absolute instant in the user's local time.
 *
 * `null` means the backend had no usable timestamp; returning an empty string
 * lets the caller choose its own "unknown" copy instead of inventing a date.
 */
export function formatTaskTime(ms: number | null, locale: string): string {
  if (ms === null) return ''
  return new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en-GB', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(ms))
}
