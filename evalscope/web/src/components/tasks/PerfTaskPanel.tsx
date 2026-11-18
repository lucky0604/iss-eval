import { useState, type ReactNode } from 'react'
import { useLocale } from '@/contexts/LocaleContext'
import { connectionPayload, type ModelConnection, type PerfDraft, type FieldErrors } from './taskConfig'
import PerfConfigForm from '@/components/perf/PerfConfigForm'
import AIPerfTaskForm from '@/components/perf/AIPerfTaskForm'
import TaskRunnerShell, { type TaskSelectionProps } from '@/components/tasks/TaskRunnerShell'
import { submitPerfTask, submitPerfExperiment, submitPerfSingle, stopPerfTask, getPerfLog } from '@/api/perf'
import type { AIPerfTaskDraft } from './aiperfDraft'

interface Props extends TaskSelectionProps {
  connection: ModelConnection
  draft: PerfDraft
  onChange: (draft: PerfDraft) => void
  toolbar: ReactNode
  aiperfToolbar: ReactNode
  engine: 'evalscope' | 'aiperf'
  aiperfDraft: AIPerfTaskDraft
  onEngineChange: (engine: 'evalscope' | 'aiperf') => void
  onAIPerfChange: (draft: AIPerfTaskDraft) => void
  onValidateConnection: () => FieldErrors
  onValidateAIPerfConnection: () => FieldErrors
  /** Raised while the AIPerf form owns a submission, so shared controls can lock. */
  onSubmittingChange?: (submitting: boolean) => void
}

export default function PerfTaskPanel({
  connection,
  draft,
  onChange,
  toolbar,
  aiperfToolbar,
  engine,
  aiperfDraft,
  onEngineChange,
  onAIPerfChange,
  onValidateConnection,
  onValidateAIPerfConnection,
  onSubmittingChange,
  ...selection
}: Props) {
  const { t, locale } = useLocale()
  const [formSubmitting, setFormSubmitting] = useState(false)
  return (
    <TaskRunnerShell
      {...selection}
      idPrefix="perf"
      title={t('perf.task.title')}
      configTitle={t('perf.task.config')}
      statusTitle={t('perf.task.status')}
      readyLabel={t('perf.task.ready')}
      submitTask={(payload, id) => {
        if (payload.engine !== 'aiperf') return submitPerfTask({ ...payload, ...connectionPayload(connection, 'perf') }, id)
        const settings = payload.aiperf as Record<string, unknown>
        return Array.isArray(settings.input_prompts) ? submitPerfSingle(payload, id) : submitPerfExperiment(payload, id)
      }}
      stopTask={stopPerfTask}
      getLog={getPerfLog}
      renderForm={({ onSubmit, disabled }) => (
        <><label className="mb-4 block text-sm">{locale === 'zh' ? '执行器' : 'Executor'}
          <select className="ml-2" value={engine} disabled={disabled || formSubmitting} onChange={(event) => onEngineChange(event.target.value as 'evalscope' | 'aiperf')}>
            <option value="evalscope">EvalScope</option><option value="aiperf">AIPerf</option>
          </select>
        </label>{engine === 'aiperf' ? <AIPerfTaskForm connection={connection} onSubmit={onSubmit} disabled={disabled}
          draft={aiperfDraft} onChange={onAIPerfChange} toolbar={aiperfToolbar} onValidateConnection={onValidateAIPerfConnection}
          onAttemptTask={(taskId) => selection.onSelect(taskId)}
          onSubmittingChange={(value) => {
            setFormSubmitting(value)
            onSubmittingChange?.(value)
          }}
          onPlanSubmitted={(taskId) => { selection.onSelect(taskId); selection.onAccepted?.(taskId) }} /> : <PerfConfigForm
          onSubmit={onSubmit}
          disabled={disabled}
          draft={draft}
          onChange={onChange}
          toolbar={toolbar}
          onValidateConnection={onValidateConnection}
        />}</>
      )}
    />
  )
}
