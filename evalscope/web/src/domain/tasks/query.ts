/**
 * URL <-> Task Center filters.
 *
 * The list view is the shareable/refreshable representation: every filter the
 * user sees lives in the query string, and parsing tolerates stale or hostile
 * values (an unknown view falls back to `all`, a bad limit to the default)
 * instead of throwing.
 */

export type TaskView = 'all' | 'active' | 'attention' | 'history'
export type TaskKind = 'eval' | 'perf'
export type TaskEngine = 'evalscope' | 'aiperf'
export type TaskSort = 'recent' | 'queue'

export interface TaskListFilters {
  view: TaskView
  kind?: TaskKind
  engine?: TaskEngine
  statuses: string[]
  q: string
  from: string
  to: string
  offset: number
  limit: number
  sort: TaskSort
}

export const DEFAULT_PAGE_SIZE = 25
export const MAX_PAGE_SIZE = 100

export const RAW_STATUSES = [
  'queued',
  'preparing',
  'running',
  'cancelling',
  'finalizing',
  'completed',
  'stopped',
  'error',
  'unknown',
] as const

const VIEWS: ReadonlySet<string> = new Set(['all', 'active', 'attention', 'history'])
const KINDS: ReadonlySet<string> = new Set(['eval', 'perf'])
const ENGINES: ReadonlySet<string> = new Set(['evalscope', 'aiperf'])
const SORTS: ReadonlySet<string> = new Set(['recent', 'queue'])
const STATUS_SET: ReadonlySet<string> = new Set(RAW_STATUSES)

export function defaultTaskFilters(): TaskListFilters {
  return {
    view: 'all',
    statuses: [],
    q: '',
    from: '',
    to: '',
    offset: 0,
    limit: DEFAULT_PAGE_SIZE,
    sort: 'recent',
  }
}

function parseTimeParam(value: string | null): string {
  const text = (value ?? '').trim()
  if (!text) return ''
  return Number.isFinite(Date.parse(text)) ? text : ''
}

function parseInteger(value: string | null, fallback: number, min: number, max: number): number {
  if (value === null || value.trim() === '') return fallback
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) return fallback
  return Math.max(min, Math.min(max, parsed))
}

/** Parse the Task Center query string. Invalid values degrade, never throw. */
export function parseTaskFilters(params: URLSearchParams): TaskListFilters {
  const view = params.get('view') ?? 'all'
  const kind = params.get('kind')
  const engine = params.get('engine')
  const sort = params.get('sort') ?? 'recent'
  const statusParam = params.get('status') ?? ''
  const statuses = statusParam
    .split(',')
    .map((value) => value.trim())
    .filter((value) => STATUS_SET.has(value))
  return {
    view: VIEWS.has(view) ? (view as TaskView) : 'all',
    kind: kind && KINDS.has(kind) ? (kind as TaskKind) : undefined,
    engine: engine && ENGINES.has(engine) ? (engine as TaskEngine) : undefined,
    statuses: [...new Set(statuses)],
    q: (params.get('q') ?? '').slice(0, 200),
    from: parseTimeParam(params.get('from')),
    to: parseTimeParam(params.get('to')),
    offset: parseInteger(params.get('offset'), 0, 0, Number.MAX_SAFE_INTEGER),
    limit: parseInteger(params.get('limit'), DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE),
    sort: SORTS.has(sort) ? (sort as TaskSort) : 'recent',
  }
}

/**
 * Serialize filters back into query params, omitting defaults so the URL stays
 * short and old links keep working.
 */
export function taskFilterParams(filters: TaskListFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.view !== 'all') params.set('view', filters.view)
  if (filters.kind) params.set('kind', filters.kind)
  if (filters.engine) params.set('engine', filters.engine)
  if (filters.statuses.length) params.set('status', filters.statuses.join(','))
  if (filters.q) params.set('q', filters.q)
  if (filters.from) params.set('from', filters.from)
  if (filters.to) params.set('to', filters.to)
  if (filters.offset > 0) params.set('offset', String(filters.offset))
  if (filters.limit !== DEFAULT_PAGE_SIZE) params.set('limit', String(filters.limit))
  if (filters.sort !== 'recent') params.set('sort', filters.sort)
  return params
}

/**
 * Merge a filter patch into the current URL.
 *
 * Any change other than paging resets the offset: leaving the user on page 7
 * of a differently filtered list shows an empty page for no visible reason.
 * The `tab` parameter (used by the creation page) is preserved.
 */
export function updateTaskParams(current: URLSearchParams, patch: Partial<TaskListFilters>): URLSearchParams {
  const merged = { ...parseTaskFilters(current), ...patch }
  if (!('offset' in patch)) merged.offset = 0
  const next = new URLSearchParams()
  const tab = current.get('tab')
  if (tab) next.set('tab', tab)
  for (const [key, value] of taskFilterParams(merged)) next.set(key, value)
  return next
}

/** Stable identity for polling dependencies. */
export function taskFiltersKey(filters: TaskListFilters): string {
  return taskFilterParams(filters).toString()
}
