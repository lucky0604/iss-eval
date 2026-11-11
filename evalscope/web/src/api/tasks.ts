import { apiValidated } from './client'
import type {
  ActiveTaskInfo,
  ListActiveTasksResponse,
  TaskDetailResponse,
  TaskListResponse,
  TaskSummaryResponse,
} from './types'

/** Task list filters, mirrored one-to-one by the `Task Center` URL params. */
export interface TaskListQuery {
  view?: 'all' | 'active' | 'attention' | 'history'
  kind?: 'eval' | 'perf'
  engine?: 'evalscope' | 'aiperf'
  statuses?: string[]
  q?: string
  from?: string
  to?: string
  offset?: number
  limit?: number
  sort?: 'recent' | 'queue'
}

function taskListParams(query: TaskListQuery): Record<string, unknown> {
  return {
    view: query.view,
    kind: query.kind,
    engine: query.engine,
    status: query.statuses && query.statuses.length ? query.statuses.join(',') : undefined,
    q: query.q,
    from: query.from,
    to: query.to,
    offset: query.offset,
    limit: query.limit,
    sort: query.sort,
  }
}

/**
 * List tasks currently running on the service, regardless of which browser
 * session launched them. Backed by the service's in-process task registry.
 */
export async function listActiveTasks(signal?: AbortSignal): Promise<ListActiveTasksResponse> {
  return apiValidated<ListActiveTasksResponse>('/api/v1/tasks/active', { signal })
}

/** Page through the persistent task history merged with live queue state. */
export async function listTasks(query: TaskListQuery = {}, signal?: AbortSignal): Promise<TaskListResponse> {
  return apiValidated<TaskListResponse>('/api/v1/tasks', { params: taskListParams(query), signal })
}

/**
 * Global Task Center counts. Always computed over the whole history, never
 * over one page of list items.
 */
export async function getTaskSummary(
  range: { from?: string; to?: string } = {},
  signal?: AbortSignal,
): Promise<TaskSummaryResponse> {
  return apiValidated<TaskSummaryResponse>('/api/v1/tasks/summary', {
    params: { from: range.from, to: range.to },
    signal,
  })
}

/** One task with its sanitized configuration snapshot and capabilities. */
export async function getTaskDetail(taskId: string, signal?: AbortSignal): Promise<TaskDetailResponse> {
  return apiValidated<TaskDetailResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/detail`, { signal })
}

export async function getTask(taskId: string, signal?: AbortSignal): Promise<ActiveTaskInfo> {
  return apiValidated<ActiveTaskInfo>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { signal })
}
