import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { EvalInvokeResponse, LogResponse, TaskSubmitResponse } from '@/api/types'
import { isDefiniteRejection } from '@/api/errors'
import { useLocale } from '@/contexts/LocaleContext'
import Card from '@/components/ui/Card'
import TaskRunMonitor from '@/components/tasks/TaskRunMonitor'

/** What the service said about a submission: accepted, definitely refused, or unknown. */
export type SubmitOutcome = 'accepted' | 'rejected' | 'unknown'

/** The outcome the shell reports back to the form that requested it. */
export interface SubmitResult {
  outcome: SubmitOutcome
  taskId: string
  error?: unknown
}

interface FormRenderProps {
  onSubmit: (config: Record<string, unknown>) => Promise<SubmitResult>
  disabled: boolean
}

export interface TaskSelectionProps {
  selectedTaskId: string | null
  onSelect: (taskId: string) => void
  /** Called once the service has accepted the task, so the caller can open its detail page. */
  onAccepted?: (taskId: string) => void
  active?: boolean
}

interface TaskRunnerShellProps extends TaskSelectionProps {
  idPrefix: string
  title: string
  configTitle: string
  statusTitle: string
  readyLabel: string
  renderForm: (props: FormRenderProps) => ReactNode
  submitTask: (config: Record<string, unknown>, taskId: string) => Promise<TaskSubmitResponse>
  stopTask: (taskId: string) => Promise<unknown>
  getLog: (taskId: string, tailLine: number) => Promise<LogResponse>
}

function createTaskId(prefix: string): string {
  return `${prefix}_${Date.now()}`
}

function TaskForm({ renderForm, ...props }: FormRenderProps & Pick<TaskRunnerShellProps, 'renderForm'>) {
  return renderForm(props)
}

export default function TaskRunnerShell({
  idPrefix,
  title,
  configTitle,
  statusTitle,
  readyLabel,
  renderForm,
  submitTask,
  stopTask,
  getLog,
  selectedTaskId,
  onSelect,
  onAccepted,
  active = true,
}: TaskRunnerShellProps) {
  const { t } = useLocale()
  const [submitting, setSubmitting] = useState(false)
  const [pendingTaskId, setPendingTaskId] = useState<string | null>(null)
  const [result, setResult] = useState<EvalInvokeResponse | null>(null)
  const [rejectedTaskId, setRejectedTaskId] = useState<string | null>(null)
  // A synchronous guard: two events can run before React commits `submitting`,
  // so the state flag alone must never decide whether a second invoke is legal.
  const submittingRef = useRef(false)
  const pendingTaskIdRef = useRef<string | null>(null)
  useEffect(() => {
    try {
      if (selectedTaskId && selectedTaskId === rejectedTaskId) {
        sessionStorage.removeItem(`evalscope-active-task-${idPrefix}`)
      } else if (selectedTaskId) {
        sessionStorage.setItem(`evalscope-active-task-${idPrefix}`, selectedTaskId)
      }
    } catch {
      return
    }
  }, [idPrefix, selectedTaskId, rejectedTaskId])
  const handleSubmit = async (config: Record<string, unknown>): Promise<SubmitResult> => {
    if (submittingRef.current) {
      return { outcome: 'unknown', taskId: pendingTaskIdRef.current ?? '' }
    }
    const id = createTaskId(idPrefix)
    submittingRef.current = true
    pendingTaskIdRef.current = id
    setSubmitting(true)
    setPendingTaskId(id)
    onSelect(id)
    try {
      await submitTask(config, id)
      // The service accepted the task. Creation owns the form; the task's
      // status, logs and results live on the detail page from here on.
      onAccepted?.(id)
      setResult((previous) => (previous && previous.task_id !== id ? previous : null))
      return { outcome: 'accepted', taskId: id }
    } catch (error) {
      const rejected = isDefiniteRejection(error)
      setRejectedTaskId(rejected ? id : null)
      setResult({ status: 'error', task_id: id, error: rejected ? error.message : t('tasks.submitUnconfirmed') })
      return { outcome: rejected ? 'rejected' : 'unknown', taskId: id, error }
    } finally {
      submittingRef.current = false
      pendingTaskIdRef.current = null
      setSubmitting(false)
      setPendingTaskId((current) => (current === id ? null : current))
    }
  }
  return (
    <div className="page-enter min-w-0">
      <h2 className="sr-only">{title}</h2>
      <div className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card title={`${t('tasks.newTask')} · ${configTitle}`}>
          <TaskForm renderForm={renderForm} onSubmit={handleSubmit} disabled={submitting} />
        </Card>
        <Card title={statusTitle} className="min-w-0 lg:sticky lg:top-20">
          {selectedTaskId ? (
            <TaskRunMonitor
              key={selectedTaskId}
              taskId={selectedTaskId}
              active={active}
              result={result?.task_id === selectedTaskId ? result : null}
              submissionPending={pendingTaskId === selectedTaskId}
              submissionRejected={rejectedTaskId === selectedTaskId}
              stopTask={stopTask}
              getLog={getLog}
            />
          ) : (
            <div className="space-y-2 py-3">
              <p className="text-sm text-[var(--text)]">{readyLabel}</p>
              <p className="text-xs text-[var(--text-muted)]">{t('tasks.library.emptyMonitor')}</p>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
