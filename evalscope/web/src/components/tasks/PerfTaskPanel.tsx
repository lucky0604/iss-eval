import { type ReactNode } from 'react'
import { useLocale } from '@/contexts/LocaleContext'
import { connectionPayload, type ModelConnection, type PerfDraft, type FieldErrors } from './taskConfig'
import PerfConfigForm from '@/components/perf/PerfConfigForm'
import CacheExperimentForm from '@/components/perf/CacheExperimentForm'
import TaskRunnerShell, { type TaskSelectionProps } from '@/components/tasks/TaskRunnerShell'
import { submitPerfTask, submitPerfExperiment, submitPerfSingle, stopPerfTask, getPerfLog } from '@/api/perf'
import type { AIPerfDraft } from './aiperfDraft'

interface Props extends TaskSelectionProps {
  connection: ModelConnection
  draft: PerfDraft
  onChange: (draft: PerfDraft) => void
  toolbar: ReactNode
  aiperfToolbar: ReactNode
  engine: 'evalscope' | 'aiperf'
  aiperfDraft: AIPerfDraft
  onEngineChange: (engine: 'evalscope' | 'aiperf') => void
  onAIPerfChange: (draft: AIPerfDraft) => void
  onValidateConnection: () => FieldErrors
  onValidateAIPerfConnection: () => FieldErrors
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
  ...selection
}: Props) {
  const { t, locale } = useLocale()
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
          <select className="ml-2" value={engine} disabled={disabled} onChange={(event) => onEngineChange(event.target.value as 'evalscope' | 'aiperf')}>
            <option value="evalscope">EvalScope</option><option value="aiperf">AIPerf</option>
          </select>
        </label>{engine === 'aiperf' ? <CacheExperimentForm connection={connection} onSubmit={onSubmit} disabled={disabled}
          draft={aiperfDraft} onChange={onAIPerfChange} toolbar={aiperfToolbar} onValidateConnection={onValidateAIPerfConnection}
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
