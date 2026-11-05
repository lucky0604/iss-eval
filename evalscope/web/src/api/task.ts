import { apiPostValidated, apiValidated } from './client'
import type { LogResponse, ProgressResponse, TaskStatusResponse, TaskSubmitResponse } from './types'

type TaskScope = 'eval' | 'perf'

export function createTaskApi(scope: TaskScope) {
  const basePath = `/api/v1/${scope}`

  return {
    /**
     * Queue a task and return as soon as it is accepted.
     *
     * `async=1` keeps the request short: the service returns 202 with the
     * queue position and the browser is free to close or reload while the task
     * runs. Progress and the final result are read back from `/api/v1/tasks`.
     */
    submit(
      payload: Record<string, unknown>,
      taskId: string,
      signal?: AbortSignal,
    ): Promise<TaskSubmitResponse> {
      return apiPostValidated<TaskSubmitResponse>(`${basePath}/invoke`, payload, {
        headers: { 'EvalScope-Task-Id': taskId },
        params: { async: 1 },
        signal,
      })
    },

    progress(taskId: string, signal?: AbortSignal): Promise<ProgressResponse> {
      return apiValidated<ProgressResponse>(`${basePath}/progress`, {
        params: { task_id: taskId },
        signal,
      })
    },

    log(
      taskId: string,
      startLine?: number,
      page = 500,
      signal?: AbortSignal,
    ): Promise<LogResponse> {
      const params: Record<string, string> = { task_id: taskId, page: String(page) }
      if (startLine !== undefined) params.start_line = String(startLine)
      return apiValidated<LogResponse>(`${basePath}/log`, { params, signal })
    },

    reportUrl(taskId: string): string {
      return `${basePath}/report?task_id=${encodeURIComponent(taskId)}`
    },

    stop(taskId: string, signal?: AbortSignal): Promise<TaskStatusResponse> {
      return apiPostValidated<TaskStatusResponse>(
        `${basePath}/stop`,
        {},
        { params: { task_id: taskId }, signal },
      )
    },
  }
}
