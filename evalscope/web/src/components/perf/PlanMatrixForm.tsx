import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { getPerfEngines } from '@/api/perf'
import { exportPerfPlan, previewPerfPlan, submitPerfPlan, type PlanErrorPayload } from '@/api/perfPlans'
import { isDomainError } from '@/api/errors'
import type { PerfPlanPreviewResponse } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import { useAsyncResource } from '@/hooks/useAsyncResource'
import { useTaskForm } from '@/components/tasks/useTaskForm'
import type { FieldErrors, ModelConnection } from '@/components/tasks/taskConfig'
import {
  buildPlanRuntime,
  matrixInputFromDraft,
  validateAIPerfDraft,
  type AIPerfDraft,
} from '@/components/tasks/aiperfDraft'
import { buildPlanText, matrixRunCount, type MatrixScene } from '@/domain/perf/planDraft'
import { canSubmitPlan, formatTokens, planExportFileName } from '@/domain/perf/perfPlan'
import Field from '@/components/ui/Field'
import Button from '@/components/ui/Button'
import { FORM_INPUT_CLASS, inputClass } from '@/components/ui/formStyles'
import PerfPlanImportPanel from '@/components/perf/PerfPlanImportPanel'
import MetricsNodeEditor from '@/components/perf/MetricsNodeEditor'
import { metricsNodeFieldIds } from '@/domain/perf/metricsNodes'

interface Props {
  connection: ModelConnection
  onSubmit: (config: Record<string, unknown>) => Promise<void>
  disabled: boolean
  draft: AIPerfDraft
  onChange: (draft: AIPerfDraft) => void
  toolbar?: ReactNode
  onValidateConnection: () => FieldErrors
  /** Called when the matrix has been accepted and owns a task id. */
  onPlanSubmitted?: (taskId: string) => void
}

const PAGE_SIZE = 100
const id = (name: string) => `matrix-${name}`

interface PlanAttempt {
  planHash: string
  taskId: string
  accepted: boolean
}

function createPlanTaskId(): string {
  return `perf_matrix_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function errorDetails(error: unknown): PlanErrorPayload | null {
  const details = (error as { details?: unknown } | null)?.details
  if (!details || typeof details !== 'object') return null
  return details as PlanErrorPayload
}

/** One row of the preview's bounded page, with the tier's own dimensions. */
function runLoad(run: PerfPlanPreviewResponse['runs'][number]): string {
  if (run.load_mode === 'open_loop' || run.rate != null) return `${run.rate}/s`
  return String(run.concurrency ?? '—')
}

/**
 * Full-matrix configuration: every point of input length × output limit × reuse
 * ratio × load × repetition is one run of one parent task.
 *
 * The form builds a schema 1.1 portable plan and submits it as text; the server
 * compiles, validates and archives it. Credentials and monitoring targets go
 * beside the plan as runtime input, so they never enter the plan file or hash.
 */
export default function PlanMatrixForm({
  connection, disabled, draft, onChange, toolbar, onValidateConnection, onPlanSubmitted,
}: Props) {
  const { t } = useLocale()
  const engines = useAsyncResource((signal) => getPerfEngines(signal), [])
  const capability = engines.data?.engines.find((item) => item.engine === 'aiperf')
  const input = matrixInputFromDraft(draft)
  const planText = buildPlanText(input, { model: connection.model, url: connection.url })
  const key = planText
  const [preview, setPreview] = useState<{ key: string; result: PerfPlanPreviewResponse } | null>(null)
  const [acknowledged, setAcknowledged] = useState<string[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [diagnostics, setDiagnostics] = useState<PlanErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState<PlanAttempt | null>(null)
  const sequence = useRef(0)
  const pending = useRef<AbortController | null>(null)
  const current = preview?.key === key ? preview.result : null
  const stale = Boolean(preview && preview.key !== key)

  useEffect(() => {
    sequence.current += 1
    pending.current?.abort()
    return () => pending.current?.abort()
  }, [key])

  const values: Record<string, string | boolean> = {
    ...Object.fromEntries(Object.entries(draft).filter(([name]) => name !== 'nodes').map(([name, value]) => [id(name), value])),
    [id('nodes')]: JSON.stringify(draft.nodes.map((node) => ({ ...node, bearerToken: '' }))),
  }
  for (const node of draft.nodes) {
    for (const name of ['nodeId', 'role', 'engine', 'url', 'labels', 'bearerToken'] as const)
      values[`matrix-node-${node.id}-${name}`] = node[name]
  }
  const domOrder = [
    'connection-api', 'connection-model', 'connection-url',
    id('matrixScene'), id('matrixName'), id('tokenizer'), id('matrixInputLengths'), id('matrixOutputLimits'),
    id('requestCount'), id('mode'), id('loads'),
    ...(draft.matrixScene === 'cache_comparison' ? [id('ratios')] : []),
    id('pool'), id('seed'), id('repetitions'), id('matrixOrder'), id('deploymentMode'), id('matrixMaxModelLen'),
    ...(draft.matrixWarmupEnabled ? [id('matrixWarmupRequests'), id('matrixWarmupInput'), id('matrixWarmupOutput'),
      id('matrixWarmupConcurrency')] : []),
    ...(draft.matrixMonitoringEnabled ? [id('required'), id('sampleInterval'), id('maxGap'),
      ...metricsNodeFieldIds('matrix', draft.nodes), id('nodes')] : []),
  ]
  const { errorFor, submitHandler } = useTaskForm({ domOrder, values })
  const validate = () => ({
    ...onValidateConnection(),
    ...Object.fromEntries(Object.entries(validateAIPerfDraft(draft)).map(([field, fieldMessage]) => [id(field), fieldMessage])),
  })
  const update = <K extends keyof AIPerfDraft>(field: K, value: AIPerfDraft[K]) => onChange({ ...draft, [field]: value })

  const runPreview = async (offset = 0) => {
    const request = ++sequence.current
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setError('')
    setDiagnostics(null)
    try {
      const result = await previewPerfPlan(planText, { acknowledged, offset, limit: PAGE_SIZE, signal: controller.signal })
      if (controller.signal.aborted || sequence.current !== request) return
      setPreview({ key, result })
      setAcknowledged((chosen) => chosen.filter((code) => (result.requires_acknowledgement ?? []).includes(code)))
    } catch (err) {
      if (controller.signal.aborted || sequence.current !== request) return
      setDiagnostics(errorDetails(err))
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (sequence.current === request) setBusy(false)
    }
  }

  const planAttempt = (planHash: string): PlanAttempt => {
    if (attempt && attempt.planHash === planHash && !attempt.accepted) return attempt
    return { planHash, taskId: createPlanTaskId(), accepted: false }
  }

  const runSubmit = async () => {
    if (!current) return
    const next = planAttempt(current.plan_hash)
    setAttempt(next)
    setBusy(true)
    setError('')
    setDiagnostics(null)
    setMessage('')
    try {
      const accepted = await submitPerfPlan(planText, next.taskId, {
        acknowledged,
        expectedPlanHash: current.plan_hash,
        runtime: buildPlanRuntime(draft, connection),
      })
      setAttempt({ ...next, accepted: true })
      setMessage(t(accepted.duplicate ? 'tasks.aiperf.matrix.duplicate' : 'tasks.aiperf.matrix.accepted', { state: accepted.state }))
      onPlanSubmitted?.(accepted.task_id || next.taskId)
    } catch (err) {
      // A definite 4xx is the user's to fix; an unknown outcome keeps the same
      // id so the retry cannot start a second model run.
      const definite = isDomainError(err) && err.kind === 'http-4xx'
      if (definite) setAttempt(null)
      setDiagnostics(errorDetails(err))
      if (definite) setError(err instanceof Error ? err.message : String(err))
      else setMessage(t('tasks.aiperf.matrix.uncertain'))
    } finally {
      setBusy(false)
    }
  }

  const runExport = async () => {
    setBusy(true)
    setError('')
    try {
      const exported = await exportPerfPlan(planText)
      const link = document.createElement('a')
      link.href = URL.createObjectURL(new Blob([exported.content], { type: 'application/json' }))
      link.download = planExportFileName(current?.normalized.name)
      link.click()
      URL.revokeObjectURL(link.href)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const toggleAck = (code: string) => setAcknowledged((chosen) =>
    chosen.includes(code) ? chosen.filter((item) => item !== code) : [...chosen, code])

  const textField = (name: 'matrixName' | 'tokenizer' | 'matrixInputLengths' | 'matrixOutputLimits' | 'requestCount' |
    'loads' | 'ratios' | 'pool' | 'seed' | 'repetitions' | 'matrixMaxModelLen' | 'matrixWarmupRequests' |
    'matrixWarmupInput' | 'matrixWarmupOutput' | 'matrixWarmupConcurrency' | 'sampleInterval' | 'maxGap',
  labelKey: string, paramName: string | undefined, type = 'text', inputDisabled = false) => {
    const fieldId = id(name)
    const fieldError = errorFor(fieldId)
    return (
      <Field id={fieldId} name={name} labelKey={labelKey} paramName={paramName} error={fieldError}>
        {(aria) => <input {...aria} type={type} step={name === 'sampleInterval' || name === 'maxGap' ? 'any' : undefined}
          disabled={inputDisabled} className={inputClass(fieldError)} value={draft[name]}
          placeholder={name === 'tokenizer' ? 'Qwen/Qwen2.5-0.5B-Instruct' : name === 'matrixName' ? 'e.g. qwen-32k-4c' : undefined}
          onChange={(event) => update(name, event.target.value)} />}
      </Field>
    )
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    submitHandler(validate, () => void runPreview(0))(event)
  }

  const submitState = canSubmitPlan(current, acknowledged)
  const blockedKey = submitState.allowed ? 'tasks.aiperf.matrix.ready'
    : submitState.reason === 'engineUnavailable' ? 'tasks.aiperf.matrix.engineUnavailable'
      : submitState.reason === 'blocked' ? 'tasks.aiperf.matrix.blocked'
        : submitState.reason === 'missingAcknowledgement' ? 'tasks.aiperf.matrix.missingAck'
          : 'tasks.aiperf.matrix.previewFirst'
  const runs = current?.runs ?? []
  const total = current?.total_runs ?? 0
  const page = current ? Math.floor(current.offset / PAGE_SIZE) : 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const localRuns = matrixRunCount(input)
  const sceneOption = (scene: MatrixScene, labelKey: string) => <option value={scene}>{t(labelKey)}</option>
  const loadModeClosed = draft.mode === 'closed_loop'

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      {toolbar}
      <p className="text-sm" role="status">AIPerf 0.13.0: {engines.loading ? t('tasks.aiperf.checking') : capability?.available ? t('tasks.aiperf.available') : t('tasks.aiperf.unavailable')}
        {!capability?.available && (capability?.reason || engines.error) ? ` — ${capability?.reason || engines.error}` : ''}</p>
      <PerfPlanImportPanel disabled={disabled} onSubmitted={onPlanSubmitted} />
      <fieldset disabled={disabled} className="space-y-4">
        <Field id={id('runType')} name="runType" labelKey="tasks.aiperf.runType">
          {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.runType}
            onChange={(event) => update('runType', event.target.value as AIPerfDraft['runType'])}>
            <option value="matrix">{t('tasks.aiperf.matrix.run')}</option>
            <option value="cache">{t('tasks.aiperf.cacheRun')}</option>
            <option value="single">{t('tasks.aiperf.singleRun')}</option>
          </select>}
        </Field>

        <section className="space-y-3" aria-labelledby="matrix-basic-title">
          <h3 id="matrix-basic-title" className="text-sm font-semibold">{t('tasks.aiperf.matrix.basic')}</h3>
          <div className="grid min-w-0 gap-3 md:grid-cols-2">
            <Field id={id('matrixScene')} name="matrixScene" labelKey="tasks.aiperf.matrix.scene">
              {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.matrixScene}
                onChange={(event) => update('matrixScene', event.target.value as MatrixScene)}>
                {sceneOption('performance', 'tasks.aiperf.matrix.scenePerformance')}
                {sceneOption('cache_comparison', 'tasks.aiperf.matrix.sceneCache')}
              </select>}
            </Field>
            {textField('matrixName', 'tasks.aiperf.matrix.name', 'name')}
            {textField('tokenizer', 'tasks.aiperf.tokenizer', 'tokenizer_path')}
            {textField('matrixInputLengths', 'tasks.aiperf.matrix.inputLengths', 'input_lengths')}
            {textField('matrixOutputLimits', 'tasks.aiperf.matrix.outputLimits', 'output_token_limits')}
            {textField('requestCount', 'tasks.aiperf.requestCount', 'request_count', 'number')}
            <Field id={id('mode')} name="mode" labelKey="tasks.aiperf.loadMode" paramName="load.mode">
              {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.mode}
                onChange={(event) => onChange({ ...draft, mode: event.target.value as AIPerfDraft['mode'],
                  loads: event.target.value === draft.mode ? draft.loads : '' })}>
                <option value="closed_loop">{t('tasks.aiperf.closed')}</option>
                <option value="open_loop">{t('tasks.aiperf.open')}</option>
              </select>}
            </Field>
            {textField('loads', loadModeClosed ? 'tasks.aiperf.matrix.loadsClosed' : 'tasks.aiperf.matrix.loadsOpen', 'load.points')}
          </div>
          {draft.matrixScene === 'performance' &&
            <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.matrix.performanceHint')}</p>}
          <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.matrix.tokenizerHint')}</p>
        </section>

        <section className="space-y-3" aria-labelledby="matrix-advanced-title">
          <h3 id="matrix-advanced-title" className="text-sm font-semibold">{t('tasks.aiperf.matrix.advanced')}</h3>
          <div className="grid min-w-0 gap-3 md:grid-cols-2">
            {draft.matrixScene === 'cache_comparison' && textField('ratios', 'tasks.aiperf.ratios', 'target_ratios')}
            {textField('pool', 'tasks.aiperf.pool', 'prefix_pool_size', 'number',
              draft.matrixScene === 'performance')}
            {textField('seed', 'tasks.aiperf.seed', 'seed', 'number')}
            {textField('repetitions', 'tasks.aiperf.repetitions', 'load.repetitions', 'number')}
            <Field id={id('matrixOrder')} name="matrixOrder" labelKey="tasks.aiperf.matrix.order" paramName="execution.order">
              {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.matrixOrder}
                onChange={(event) => update('matrixOrder', event.target.value as AIPerfDraft['matrixOrder'])}>
                <option value="seeded_shuffle">{t('tasks.aiperf.matrix.orderShuffle')}</option>
                <option value="load_then_input">{t('tasks.aiperf.matrix.orderLoad')}</option>
              </select>}
            </Field>
            <Field id={id('deploymentMode')} name="deploymentMode" labelKey="tasks.aiperf.deployment" paramName="deployment.mode">
              {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.deploymentMode}
                onChange={(event) => update('deploymentMode', event.target.value as AIPerfDraft['deploymentMode'])}>
                <option value="single">{t('tasks.aiperf.single')}</option>
                <option value="pd">{t('tasks.aiperf.pd')}</option>
              </select>}
            </Field>
            {textField('matrixMaxModelLen', 'tasks.aiperf.matrix.maxModelLen', 'deployment.declared_max_model_len', 'number')}
          </div>
          <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.matrix.maxModelLenHint')}</p>
          <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.matrix.poolHint')}</p>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.matrixWarmupEnabled}
              onChange={(event) => update('matrixWarmupEnabled', event.target.checked)} />
            {t('tasks.aiperf.matrix.warmup')}
          </label>
          {draft.matrixWarmupEnabled && <div className="grid min-w-0 gap-3 md:grid-cols-2">
            {textField('matrixWarmupRequests', 'tasks.aiperf.matrix.warmupRequests', 'warmup.request_count', 'number')}
            {textField('matrixWarmupInput', 'tasks.aiperf.matrix.warmupInput', 'warmup.input_tokens', 'number')}
            {textField('matrixWarmupOutput', 'tasks.aiperf.matrix.warmupOutput', 'warmup.output_token_limit', 'number')}
            {textField('matrixWarmupConcurrency', 'tasks.aiperf.matrix.warmupConcurrency', 'warmup.concurrency', 'number')}
          </div>}
        </section>

        <section className="space-y-3" aria-labelledby="matrix-monitoring-title">
          <h3 id="matrix-monitoring-title" className="text-sm font-semibold">{t('tasks.aiperf.matrix.monitoring')}</h3>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.matrixMonitoringEnabled}
              onChange={(event) => update('matrixMonitoringEnabled', event.target.checked)} />
            {t('tasks.aiperf.matrix.monitoringEnable')}
          </label>
          <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.matrix.monitoringHint')}</p>
          {draft.matrixMonitoringEnabled && <>
            <div className="grid min-w-0 gap-3 md:grid-cols-2">
              {textField('sampleInterval', 'tasks.aiperf.sampleInterval', 'telemetry.sample_interval_seconds', 'number')}
              {textField('maxGap', 'tasks.aiperf.maxGap', 'telemetry.max_scrape_gap_seconds', 'number')}
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={draft.required}
                onChange={(event) => update('required', event.target.checked)} />
              {t('tasks.aiperf.required')}
            </label>
            <MetricsNodeEditor idPrefix="matrix" nodes={draft.nodes} errorFor={errorFor} disabled={disabled}
              onChange={(nodes) => update('nodes', nodes)} />
          </>}
        </section>

        <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.matrix.axesHint', { count: formatTokens(localRuns) })}</p>
        <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.limitations')}</p>
        {(error || message || !submitState.allowed) && <p role="alert" className={error ? 'text-sm text-[var(--danger)]' : 'text-sm'}>
          {error || message || t(stale ? 'tasks.aiperf.matrix.stale' : blockedKey)}
        </p>}
        {(diagnostics?.diagnostics ?? []).length > 0 && <ul className="space-y-1 text-xs">
          {diagnostics?.diagnostics?.map((item, index) => <li key={`${item.code}-${index}`}>
            <span className="font-mono">[{item.severity}] {item.code}</span> — {item.message}
          </li>)}
        </ul>}

        {current && <div className="space-y-3" role="status">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs md:grid-cols-4">
            <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.matrix.totalRuns')}</dt>
              <dd>{formatTokens(current.total_runs)}</dd></div>
            <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.matrix.budget')}</dt>
              <dd>{formatTokens(current.budgets.formal_requests)} + {formatTokens(current.budgets.warmup_requests)} + {formatTokens(current.budgets.primer_requests)}</dd></div>
            <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.matrix.outputLimits')}</dt>
              <dd>{(current.output_token_limits ?? []).join(', ') || '—'}</dd></div>
            <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.planImport.planHash')}</dt>
              <dd className="truncate font-mono">{current.plan_hash.slice(0, 12)}</dd></div>
          </dl>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead><tr>
                <th>#</th><th>{t('tasks.aiperf.planImport.runs', { shown: runs.length, total })}</th>
                <th>{t('tasks.aiperf.matrix.input')}</th><th>{t('tasks.aiperf.matrix.outputLimits')}</th>
                <th>{t('tasks.aiperf.matrix.ratio')}</th><th>{t('tasks.aiperf.matrix.load')}</th>
                <th>{t('tasks.aiperf.matrix.requests')}</th><th>{t('tasks.aiperf.matrix.primers')}</th>
              </tr></thead>
              <tbody>
                {runs.map((run) => <tr key={run.run_id}>
                  <td>{run.order}</td>
                  <td className="font-mono">{run.run_id}</td>
                  <td>{formatTokens(run.input_tokens)}</td>
                  <td>{run.output_token_limit}</td>
                  <td>{run.target_ratio == null ? '—' : `${Math.round(run.target_ratio * 100)}%`}</td>
                  <td>{runLoad(run)}</td>
                  <td>{run.request_count}{run.concurrency != null && run.request_count < run.concurrency ? ' ⚠' : ''}</td>
                  <td>{run.primer_requests}</td>
                </tr>)}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Button type="button" variant="outline" size="sm" disabled={busy || current.offset <= 0}
              onClick={() => void runPreview(Math.max(0, current.offset - PAGE_SIZE))}>{t('tasks.aiperf.matrix.previous')}</Button>
            <span>{t('tasks.aiperf.matrix.page', { page: page + 1, pages })}</span>
            <Button type="button" variant="outline" size="sm" disabled={busy || page + 1 >= pages}
              onClick={() => void runPreview(current.offset + PAGE_SIZE)}>{t('tasks.aiperf.matrix.next')}</Button>
          </div>
          <div>
            <h4 className="text-sm font-medium">{t('tasks.aiperf.planImport.diagnostics')}</h4>
            {(current.diagnostics ?? []).length === 0 ? <p className="text-xs">{t('tasks.aiperf.planImport.noDiagnostics')}</p> : (
              <ul className="space-y-1 text-xs">
                {(current.diagnostics ?? []).map((item, index) => <li key={`${item.code}-${index}`}>
                  <span className="font-mono">[{item.severity}] {item.code}</span>
                  {item.case_id ? ` (${item.case_id})` : ''} — {item.message}
                </li>)}
              </ul>
            )}
          </div>
          {(current.requires_acknowledgement ?? []).length > 0 && <fieldset className="space-y-1">
            <legend className="text-sm font-medium">{t('tasks.aiperf.planImport.acknowledgements')}</legend>
            {(current.requires_acknowledgement ?? []).map((code) => <label key={code} className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={acknowledged.includes(code)} onChange={() => toggleAck(code)} />
              <span className="font-mono">{code}</span>
            </label>)}
          </fieldset>}
        </div>}

        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="outline" disabled={busy || !capability?.available || disabled}>
            {t('tasks.aiperf.matrix.preview')}
          </Button>
          <Button type="button" disabled={busy || !submitState.allowed || stale || disabled} onClick={() => void runSubmit()}>
            {t('tasks.aiperf.matrix.start')}
          </Button>
          <Button type="button" variant="outline" disabled={busy || disabled} onClick={() => void runExport()}>
            {t('tasks.aiperf.matrix.export')}
          </Button>
        </div>
      </fieldset>
    </form>
  )
}
