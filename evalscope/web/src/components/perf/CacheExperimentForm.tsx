import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { getPerfEngines, validatePerfExperiment, validatePerfSingle } from '@/api/perf'
import type { PerfPlanResponse, PerfSinglePlanResponse } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import { useAsyncResource } from '@/hooks/useAsyncResource'
import { useTaskForm } from '@/components/tasks/useTaskForm'
import { type FieldErrors, type ModelConnection } from '@/components/tasks/taskConfig'
import {
  buildAIPerfPayload, validateAIPerfDraft, type AIPerfDraft,
} from '@/components/tasks/aiperfDraft'
import Field from '@/components/ui/Field'
import { FORM_INPUT_CLASS, inputClass } from '@/components/ui/formStyles'
import Button from '@/components/ui/Button'
import PerfPlanImportPanel from '@/components/perf/PerfPlanImportPanel'
import MetricsNodeEditor from '@/components/perf/MetricsNodeEditor'
import { metricsNodeFieldIds } from '@/domain/perf/metricsNodes'
import PlanMatrixForm from '@/components/perf/PlanMatrixForm'

interface Props {
  connection: ModelConnection
  onSubmit: (config: Record<string, unknown>) => Promise<void>
  disabled: boolean
  draft: AIPerfDraft
  onChange: (draft: AIPerfDraft) => void
  toolbar?: ReactNode
  onValidateConnection: () => FieldErrors
  /** Called when a portable plan has been accepted and owns a task id. */
  onPlanSubmitted?: (taskId: string) => void
}

type TextField = Exclude<keyof AIPerfDraft, 'nodes' | 'required' | 'mode' | 'deploymentMode' | 'runType' | 'prompts' |
  `matrix${string}`>
type Preview = { kind: 'cache'; value: PerfPlanResponse } | { kind: 'single'; value: PerfSinglePlanResponse }
const FIELD_LABELS: Record<TextField, string> = {
  tokenizer: 'tasks.aiperf.tokenizer', ratios: 'tasks.aiperf.ratios', inputTokens: 'tasks.aiperf.inputTokens',
  requestCount: 'tasks.aiperf.requestCount', outputTokens: 'tasks.aiperf.outputTokens', loads: 'tasks.aiperf.loads',
  repetitions: 'tasks.aiperf.repetitions', pool: 'tasks.aiperf.pool', seed: 'tasks.aiperf.seed',
  sampleInterval: 'tasks.aiperf.sampleInterval', maxGap: 'tasks.aiperf.maxGap',
}

/**
 * AIPerf entry point: the full matrix is the primary workload, the legacy prefix
 * cache experiment and single run remain selectable from the same control.
 */
export default function CacheExperimentForm(props: Props) {
  return props.draft.runType === 'matrix' ? <PlanMatrixForm {...props} /> : <LegacyAIPerfForm {...props} />
}

function LegacyAIPerfForm({ connection, onSubmit, disabled, draft, onChange, toolbar, onValidateConnection, onPlanSubmitted }: Props) {
  const { t } = useLocale()
  const engines = useAsyncResource((signal) => getPerfEngines(signal), [])
  const capability = engines.data?.engines.find((item) => item.engine === 'aiperf')
  const [preview, setPreview] = useState<{ key: string; result: Preview } | null>(null)
  const [error, setError] = useState('')
  const [checkingKey, setCheckingKey] = useState<string | null>(null)
  const [probe, setProbe] = useState(false)
  const sequence = useRef(0)
  const pending = useRef<AbortController | null>(null)
  const payload = buildAIPerfPayload(draft, connection)
  const key = JSON.stringify({ payload, probe })
  const checking = checkingKey === key
  const result = preview?.key === key ? preview.result : null
  const current = result?.value ?? null
  const requiredNodesFailed = Boolean(draft.required && current?.metrics_probe === 'completed' &&
    current.nodes?.some((node) => node.status !== 'available'))

  useEffect(() => {
    sequence.current += 1
    pending.current?.abort()
    return () => pending.current?.abort()
  }, [key])

  const values: Record<string, string | boolean> = {
    ...Object.fromEntries(Object.entries(draft).filter(([name]) => name !== 'nodes' && !name.startsWith('matrix'))
      .map(([name, value]) => [`aiperf-${name}`, value])),
    'aiperf-nodes': JSON.stringify(draft.nodes.map((node) => ({ ...node, bearerToken: '' }))),
  }
  for (const node of draft.nodes) {
    for (const name of ['nodeId', 'role', 'engine', 'url', 'labels', 'bearerToken'] as const)
      values[`aiperf-node-${node.id}-${name}`] = node[name]
  }
  const domOrder = ['connection-api', 'connection-model', 'connection-url',
    ...['prompts', 'tokenizer', 'ratios', 'inputTokens', 'requestCount', 'outputTokens', 'loads', 'repetitions', 'pool',
      'seed', 'sampleInterval', 'maxGap'].map((name) => `aiperf-${name}`),
    ...metricsNodeFieldIds('aiperf', draft.nodes),
    'aiperf-nodes']
  const { errorFor, submitHandler } = useTaskForm({ domOrder, values })
  const validate = () => ({
    ...onValidateConnection(),
    ...Object.fromEntries(Object.entries(validateAIPerfDraft(draft)).map(([field, message]) => [`aiperf-${field}`, message])),
  })
  const update = <K extends keyof AIPerfDraft>(field: K, value: AIPerfDraft[K]) => onChange({ ...draft, [field]: value })
  const previewPlan = async () => {
    const request = ++sequence.current
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setCheckingKey(key)
    setError('')
    try {
      const taskId = `preview_${Date.now()}_${request}`
      const result: Preview = draft.runType === 'single'
        ? { kind: 'single', value: await validatePerfSingle(payload, taskId, controller.signal, probe) }
        : { kind: 'cache', value: await validatePerfExperiment(payload, taskId, controller.signal, probe) }
      if (!controller.signal.aborted && sequence.current === request) setPreview({ key, result })
    } catch (err) {
      if (!controller.signal.aborted && sequence.current === request)
        setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (sequence.current === request) setCheckingKey(null)
    }
  }
  const textField = (name: TextField, type = 'text') => {
    const id = `aiperf-${name}`
    const error = errorFor(id)
    return <Field id={id} name={name} labelKey={FIELD_LABELS[name]} error={error}>
      {(aria) => <input {...aria} type={type} step={name === 'sampleInterval' || name === 'maxGap' ? 'any' : undefined}
        className={inputClass(error)} value={draft[name]}
        placeholder={name === 'tokenizer' ? 'Qwen/Qwen2.5-0.5B-Instruct' : undefined}
        onChange={(event) => update(name, event.target.value)} />}
    </Field>
  }
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    submitHandler(validate, () => void previewPlan())(event)
  }
  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      {toolbar}
      <p className="text-sm" role="status">AIPerf 0.13.0: {engines.loading ? t('tasks.aiperf.checking') : capability?.available ? t('tasks.aiperf.available') : t('tasks.aiperf.unavailable')}
        {!capability?.available && (capability?.reason || engines.error) ? ` — ${capability?.reason || engines.error}` : ''}</p>
      <PerfPlanImportPanel disabled={disabled} onSubmitted={onPlanSubmitted} />
      <fieldset disabled={disabled} className="space-y-4">
        <Field id="aiperf-runType" name="runType" labelKey="tasks.aiperf.runType">
          {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.runType}
            onChange={(event) => update('runType', event.target.value as AIPerfDraft['runType'])}>
            <option value="matrix">{t('tasks.aiperf.matrix.run')}</option>
            <option value="cache">{t('tasks.aiperf.cacheRun')}</option><option value="single">{t('tasks.aiperf.singleRun')}</option>
          </select>}
        </Field>
        {draft.runType === 'single' && <Field id="aiperf-prompts" name="prompts" labelKey="tasks.aiperf.prompts" error={errorFor('aiperf-prompts')}>
          {(aria) => <textarea {...aria} className={inputClass(errorFor('aiperf-prompts'))} rows={5} value={draft.prompts}
            onChange={(event) => update('prompts', event.target.value)} />}
        </Field>}
        <div className="grid min-w-0 gap-3 md:grid-cols-2">
          {textField('tokenizer')}{draft.runType === 'cache' && <>{textField('ratios')}{textField('inputTokens', 'number')}
            {textField('requestCount', 'number')}</>}{textField('outputTokens', 'number')}{textField('loads')}
          {draft.runType === 'cache' && <>{textField('repetitions', 'number')}{textField('pool', 'number')}{textField('seed', 'number')}</>}
          <Field id="aiperf-mode" name="mode" labelKey="tasks.aiperf.loadMode">
            {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.mode}
              onChange={(event) => update('mode', event.target.value as AIPerfDraft['mode'])}>
              <option value="closed_loop">{t('tasks.aiperf.closed')}</option><option value="open_loop">{t('tasks.aiperf.open')}</option>
            </select>}
          </Field>
          <Field id="aiperf-deploymentMode" name="deploymentMode" labelKey="tasks.aiperf.deployment">
            {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.deploymentMode}
              onChange={(event) => update('deploymentMode', event.target.value as AIPerfDraft['deploymentMode'])}>
              <option value="single">{t('tasks.aiperf.single')}</option><option value="pd">{t('tasks.aiperf.pd')}</option>
            </select>}
          </Field>
          {textField('sampleInterval', 'number')}{textField('maxGap', 'number')}
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.required}
          onChange={(event) => update('required', event.target.checked)} />{t('tasks.aiperf.required')}</label>
        <MetricsNodeEditor idPrefix="aiperf" nodes={draft.nodes} errorFor={errorFor} disabled={disabled}
          onChange={(nodes) => update('nodes', nodes)} />
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={probe}
          onChange={(event) => setProbe(event.target.checked)} />{t('tasks.aiperf.probe')}</label>
        <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.limitations')}</p>
        {error && <p role="alert" className="text-sm text-[var(--danger)]">{error}</p>}
        {current && result && <div className="text-sm" role="status">
          {result.kind === 'cache' ? <><p>{t('tasks.aiperf.plan')}: {result.value.plan.subruns.length} {t('tasks.aiperf.runs')}, {result.value.plan.profiling_requests} {t('tasks.aiperf.requests')}, {result.value.plan.primer_requests} {t('tasks.aiperf.primers')}</p>
            <p>{t('tasks.aiperf.maxOutput')}: {result.value.plan.max_profiling_output_tokens ?? '—'}</p></> :
            <p>{t('tasks.aiperf.plan')}: {result.value.profiling_requests} {t('tasks.aiperf.requests')}</p>}
          {current.nodes?.map((node) => <p key={node.node_id}>{node.node_id}: {node.status} ({node.metric_names?.join(', ') || '—'})</p>)}
          {current.warnings?.map((warning) => <p key={warning}>{warning}</p>)}
          {requiredNodesFailed && <p className="text-[var(--danger)]">{t('tasks.aiperf.nodeUnavailable')}</p>}
        </div>}
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="outline" disabled={checking || !capability?.available}>{t('tasks.aiperf.preview')}</Button>
          <Button type="button" disabled={checking || !current?.engine_available || requiredNodesFailed}
            onClick={() => void onSubmit(payload)}>{t(draft.runType === 'single' ? 'tasks.aiperf.startSingle' : 'tasks.aiperf.start')}</Button>
        </div>
      </fieldset>
    </form>
  )
}
