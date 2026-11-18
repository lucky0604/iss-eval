import { useEffect, useRef, useState, type ReactNode, type SyntheticEvent } from 'react'
import { getPerfEngines, probePerfTelemetry, validatePerfSingle } from '@/api/perf'
import { exportPerfPlan, previewPerfPlan, submitPerfPlan, type PlanErrorPayload } from '@/api/perfPlans'
import { isDefiniteRejection } from '@/api/errors'
import type { SubmitResult } from '@/components/tasks/TaskRunnerShell'
import type { PerfPlanPreviewResponse, PerfSinglePlanResponse } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import { useAsyncResource } from '@/hooks/useAsyncResource'
import { useTaskForm } from '@/components/tasks/useTaskForm'
import { validateConnection, type FieldErrors, type ModelConnection } from '@/components/tasks/taskConfig'
import {
  buildAIPerfPromptPayload,
  buildPlanRuntime,
  generatedPlanInput,
  promptLines,
  telemetryConfiguration,
  validateAIPerfTask,
  type AIPerfTaskDraft,
} from '@/components/tasks/aiperfDraft'
import { buildPlanText, matrixRunCount } from '@/domain/perf/planDraft'
import { canSubmitPlan, formatTokens, planExportFileName } from '@/domain/perf/perfPlan'
import Field from '@/components/ui/Field'
import Button from '@/components/ui/Button'
import { FORM_INPUT_CLASS, inputClass } from '@/components/ui/formStyles'
import PerfPlanImportPanel from '@/components/perf/PerfPlanImportPanel'
import MetricsNodeEditor from '@/components/perf/MetricsNodeEditor'
import { metricsNodeFieldIds } from '@/domain/perf/metricsNodes'

interface Props {
  connection: ModelConnection
  /** Real-prompt submission through the task shell (task id, log, stop lifecycle). */
  onSubmit: (config: Record<string, unknown>) => Promise<SubmitResult | void>
  disabled: boolean
  draft: AIPerfTaskDraft
  onChange: (draft: AIPerfTaskDraft) => void
  toolbar?: ReactNode
  onValidateConnection: () => FieldErrors
  /** Called when a generated plan has been accepted and owns a task id. */
  onPlanSubmitted?: (taskId: string) => void
  /**
   * Called with the task id of an attempt whose outcome is unknown, so the task
   * monitor queries that original id instead of a fresh one being created.
   */
  onAttemptTask?: (taskId: string) => void
  /** Reports the synchronous submission lock so surrounding controls can disable. */
  onSubmittingChange?: (submitting: boolean) => void
}

type Preview =
  | { kind: 'plan'; value: PerfPlanPreviewResponse }
  | { kind: 'single'; value: PerfSinglePlanResponse }

interface PlanAttempt {
  planHash: string
  taskId: string
  accepted: boolean
  /** The service may have accepted the request; a retry reuses `taskId`. */
  unknown?: boolean
}

interface Feedback {
  key: string
  message: string
  error: string
  diagnostics: PlanErrorPayload | null
}

interface Acknowledgement {
  key: string
  codes: string[]
}

/** The real-prompt submission this form remembers across retries. */
interface PromptAttempt {
  identity: string
  taskId: string
  outcome: 'accepted' | 'unknown'
}

const EMPTY_FEEDBACK: Feedback = { key: '', message: '', error: '', diagnostics: null }

interface ProbeState {
  /**
   * The monitoring object this reply answers, compared by reference. A new
   * object means a real edit (including monitoring off and on again), so the
   * previous reply can never resurface as if it were still current.
   */
  monitoring: AIPerfTaskDraft['monitoring']
  nodes: NonNullable<PerfSinglePlanResponse['nodes']>
}

const PAGE_SIZE = 100
const id = (name: string) => `aiperf-${name}`

/** FNV-1a over the effective configuration: an in-memory identity, never printed. */
function digest(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16)
}

function createPlanTaskId(): string {
  return `perf_matrix_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function errorDetails(error: unknown): PlanErrorPayload | null {
  const details = (error as { details?: unknown } | null)?.details
  if (!details || typeof details !== 'object') return null
  return details as PlanErrorPayload
}

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message
  return error == null ? '' : String(error)
}

function runLoad(run: PerfPlanPreviewResponse['runs'][number]): string {
  if (run.load_mode === 'open_loop' || run.rate != null) return `${run.rate}/s`
  return String(run.concurrency ?? '—')
}

/**
 * The single AIPerf creation form.
 *
 * One form, one controller: the input source only decides which fields are
 * active and which request contract is used (generated plan vs. the existing
 * inline-prompt single run). Prefix-reuse comparison is a switch inside the
 * generated branch, not another top-level mode.
 */
export default function AIPerfTaskForm({
  connection, onSubmit, disabled, draft, onChange, toolbar, onValidateConnection, onPlanSubmitted,
  onAttemptTask, onSubmittingChange,
}: Props) {
  const { t } = useLocale()
  const engines = useAsyncResource((signal) => getPerfEngines(signal), [])
  const capability = engines.data?.engines.find((item) => item.engine === 'aiperf')
  const generated = draft.inputSource === 'generated'
  const planText = generated ? buildPlanText(generatedPlanInput(draft), { model: connection.model, url: connection.url }) : ''
  const promptPayload = generated ? null : buildAIPerfPromptPayload(draft, connection)
  const runtime = generated ? buildPlanRuntime(draft, connection) : null
  // The preview identity covers the raw active inputs, the connection (including
  // API mode and authentication) and the monitoring revision. Raw inputs matter
  // because the builders normalize invalid active values to safe defaults; an
  // edit must invalidate the preview even when the normalized plan is unchanged.
  // Secrets only feed this local digest and are never rendered, stored or sent.
  const activeIdentity = generated
    ? JSON.stringify({
        tokenizer: draft.tokenizer, name: draft.name, deploymentMode: draft.deploymentMode, generated: draft.generated,
      })
    : JSON.stringify({
        tokenizer: draft.tokenizer, name: draft.name, deploymentMode: draft.deploymentMode, prompts: draft.prompts,
      })
  const connectionIdentity = JSON.stringify({
    model: connection.model, url: connection.url, api: connection.api, apiKey: connection.apiKey,
  })
  const monitoringIdentity = JSON.stringify(draft.monitoring)
  const key = `${draft.inputSource}:${digest(activeIdentity)}:${digest(connectionIdentity)}:${digest(monitoringIdentity)}`
  const promptIdentity = digest(JSON.stringify(promptPayload ?? {}))

  const [preview, setPreview] = useState<{ key: string; result: Preview } | null>(null)
  // Reply state is stamped with the identity it answers. Editing any effective
  // value only changes `key`, so stale replies stop being visible without an
  // effect writing state during render.
  const [feedback, setFeedback] = useState<Feedback>(EMPTY_FEEDBACK)
  const [acknowledgement, setAcknowledgement] = useState<Acknowledgement>({ key: '', codes: [] })
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [attempt, setAttempt] = useState<PlanAttempt | null>(null)
  const [promptAttempt, setPromptAttempt] = useState<PromptAttempt | null>(null)
  const [unknownAttempt, setUnknownAttempt] = useState<{ source: 'generated' | 'prompts' | 'import'; taskId: string } | null>(null)
  const [importEpoch, setImportEpoch] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [probe, setProbe] = useState<ProbeState | null>(null)
  const [probeBusyState, setProbeBusyState] = useState<
    { monitoring: AIPerfTaskDraft['monitoring']; request: number } | null
  >(null)
  const [probeErrorState, setProbeErrorState] = useState<
    { monitoring: AIPerfTaskDraft['monitoring']; message: string } | null
  >(null)
  const sequence = useRef(0)
  const pending = useRef<AbortController | null>(null)
  const probeSequence = useRef(0)
  const probePending = useRef<AbortController | null>(null)
  // Submission ownership is independent of the form key: editing a field while
  // the invoke is in flight must not release the lock or allow a second task.
  const inFlight = useRef(false)
  const mounted = useRef(true)
  const current = preview?.key === key ? preview.result : null
  const activeFeedback = feedback.key === key ? feedback : EMPTY_FEEDBACK
  const acknowledged = acknowledgement.key === key ? acknowledgement.codes : []
  const busy = busyKey === key
  const locked = disabled || submitting
  const { message, error, diagnostics } = activeFeedback
  const probeCurrent = probe?.monitoring === draft.monitoring ? probe : null
  const probeBusy = probeBusyState?.monitoring === draft.monitoring
  const probeError = probeErrorState?.monitoring === draft.monitoring ? probeErrorState.message : ''

  const updateFeedback = (patch: Partial<Omit<Feedback, 'key'>>) => {
    setFeedback((previous) => ({
      key, message: '', error: '', diagnostics: null, ...(previous.key === key ? previous : {}), ...patch,
    }))
  }

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      // Leaving the form abandons both in-flight requests. This is a client
      // abort only; it does not assert that the service stopped scraping.
      pending.current?.abort()
      probePending.current?.abort()
    }
  }, [])

  useEffect(() => {
    // Switching source or editing any effective value cancels the in-flight
    // preview so a late reply can never describe the previous branch.
    sequence.current += 1
    pending.current?.abort()
    return () => {
      sequence.current += 1
      pending.current?.abort()
    }
  }, [key])

  useEffect(() => {
    // Any monitoring edit makes the previous probe obsolete: cancel the client
    // request and bump the generation so late replies are ignored. The reply,
    // busy and error state are stamped with the monitoring object, so a new
    // object hides them without this effect writing state. This cancels the
    // client request; it does not claim the server stopped scraping its targets.
    probeSequence.current += 1
    probePending.current?.abort()
    probePending.current = null
  }, [draft.monitoring])

  const values: Record<string, string | boolean> = {
    'aiperf-tokenizer': draft.tokenizer,
    'aiperf-name': draft.name,
    'aiperf-inputSource': draft.inputSource,
    'aiperf-prompts': draft.prompts.text,
    'aiperf-inputLengths': draft.generated.inputLengths,
    'aiperf-outputLimits': draft.generated.outputLimits,
    'aiperf-mode': draft.generated.mode,
    'aiperf-closedLoads': draft.generated.closedLoads,
    'aiperf-openRates': draft.generated.openRates,
    'aiperf-requestCount': draft.generated.requestCount,
    'aiperf-comparePrefix': draft.generated.comparePrefix,
    'aiperf-ratios': draft.generated.ratios,
    'aiperf-pool': draft.generated.pool,
    'aiperf-seed': draft.generated.seed,
    'aiperf-repetitions': draft.generated.repetitions,
    'aiperf-order': draft.generated.order,
    'aiperf-deploymentMode': draft.deploymentMode,
    'aiperf-maxModelLen': draft.generated.maxModelLen,
    'aiperf-warmupEnabled': draft.generated.warmup.enabled,
    'aiperf-warmupRequests': draft.generated.warmup.requests,
    'aiperf-warmupInput': draft.generated.warmup.inputTokens,
    'aiperf-warmupOutput': draft.generated.warmup.outputTokens,
    'aiperf-warmupConcurrency': draft.generated.warmup.concurrency,
    'aiperf-promptMode': draft.prompts.mode,
    'aiperf-promptOutputLimit': draft.prompts.outputLimit,
    'aiperf-promptConcurrency': draft.prompts.concurrency,
    'aiperf-promptRate': draft.prompts.rate,
    'aiperf-monitoringEnabled': draft.monitoring.enabled,
    'aiperf-sampleInterval': draft.monitoring.sampleInterval,
    'aiperf-maxGap': draft.monitoring.maxGap,
    'aiperf-required': draft.monitoring.required,
  }
  for (const node of draft.monitoring.nodes)
    for (const name of ['nodeId', 'role', 'engine', 'url', 'labels', 'bearerToken'] as const)
      values[`aiperf-node-${node.id}-${name}`] = node[name]

  const advancedIds = new Set([
    'aiperf-mode',
    ...(draft.generated.comparePrefix ? ['aiperf-ratios', 'aiperf-pool'] : []),
    'aiperf-seed', 'aiperf-repetitions', 'aiperf-order', 'aiperf-deploymentMode',
    ...(draft.generated.warmup.enabled
      ? ['aiperf-warmupRequests', 'aiperf-warmupInput', 'aiperf-warmupOutput', 'aiperf-warmupConcurrency']
      : []),
  ])
  const domOrder = [
    'connection-api', 'connection-model', 'connection-url', 'aiperf-inputSource', 'aiperf-tokenizer',
    ...(generated
      ? ['aiperf-name', 'aiperf-inputLengths', 'aiperf-outputLimits', 'aiperf-requestCount', 'aiperf-maxModelLen',
        draft.generated.mode === 'closed_loop' ? 'aiperf-closedLoads' : 'aiperf-openRates',
        'aiperf-comparePrefix',
        'aiperf-mode', 'aiperf-seed', 'aiperf-repetitions', 'aiperf-order', 'aiperf-deploymentMode',
        ...(draft.generated.warmup.enabled
          ? ['aiperf-warmupRequests', 'aiperf-warmupInput', 'aiperf-warmupOutput', 'aiperf-warmupConcurrency']
          : []),
        ...(draft.generated.comparePrefix ? ['aiperf-ratios', 'aiperf-pool'] : [])]
      : ['aiperf-prompts', 'aiperf-promptOutputLimit', 'aiperf-promptMode',
        draft.prompts.mode === 'closed_loop' ? 'aiperf-promptConcurrency' : 'aiperf-promptRate']),
    'aiperf-monitoringEnabled',
    ...(draft.monitoring.enabled
      ? ['aiperf-sampleInterval', 'aiperf-maxGap', 'aiperf-required',
        ...metricsNodeFieldIds('aiperf', draft.monitoring.nodes), 'aiperf-nodes']
      : []),
  ]
  const { errorFor, validateAndPublish, revalidate, showMore, toggleMore } = useTaskForm({
    domOrder, values, moreParamsIds: [...advancedIds],
  })
  const draftErrors = () => Object.fromEntries(
    Object.entries(validateAIPerfTask(draft)).map(([field, fieldMessage]) => [id(field), fieldMessage]),
  )
  const connectionErrors = () => ({
    ...Object.fromEntries(Object.entries(validateConnection(connection))
      .map(([field, fieldMessage]) => [`connection-${field}`, fieldMessage])),
    ...(connection.api !== 'openai' ? { 'connection-api': 'tasks.aiperf.openaiRequired' } : {}),
  })
  // Pure client validation drives `canStart` during render; the parent callback
  // (which publishes connection errors) is only called from submit handlers.
  const clientErrors = { ...connectionErrors(), ...draftErrors() }
  const clientInvalid = Object.keys(clientErrors).length > 0
  const validate = () => ({ ...clientErrors, ...onValidateConnection(), ...draftErrors() })

  useEffect(() => {
    // A dependent edit must refresh errors an earlier submit published: fields
    // that are still invalid keep (or update) their message, fixed ones lose it,
    // and fields hidden by a source switch are dropped.
    revalidate(() => ({ ...clientErrors }), (fieldId) => domOrder.includes(fieldId))
  }, [clientErrors, domOrder, revalidate])
  const updateGenerated = <K extends keyof AIPerfTaskDraft['generated']>(
    field: K, value: AIPerfTaskDraft['generated'][K],
  ) => onChange({ ...draft, generated: { ...draft.generated, [field]: value } })
  const updateWarmup = <K extends keyof AIPerfTaskDraft['generated']['warmup']>(
    field: K, value: AIPerfTaskDraft['generated']['warmup'][K],
  ) => onChange({
    ...draft,
    generated: { ...draft.generated, warmup: { ...draft.generated.warmup, [field]: value } },
  })
  const updatePrompts = <K extends keyof AIPerfTaskDraft['prompts']>(
    field: K, value: AIPerfTaskDraft['prompts'][K],
  ) => onChange({ ...draft, prompts: { ...draft.prompts, [field]: value } })
  const updateMonitoring = <K extends keyof AIPerfTaskDraft['monitoring']>(
    field: K, value: AIPerfTaskDraft['monitoring'][K],
  ) => onChange({ ...draft, monitoring: { ...draft.monitoring, [field]: value } })

  const claimSubmit = (): boolean => {
    // Synchronous: state alone cannot stop two events dispatched before React
    // commits `submitting` from starting two model runs.
    if (inFlight.current) return false
    inFlight.current = true
    setSubmitting(true)
    onSubmittingChange?.(true)
    return true
  }
  const releaseSubmit = () => {
    inFlight.current = false
    onSubmittingChange?.(false)
    if (mounted.current) setSubmitting(false)
  }

  const runPreview = async (offset = 0) => {
    if (locked || inFlight.current) return
    const request = ++sequence.current
    const startedKey = key
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setBusyKey(startedKey)
    updateFeedback({ error: '', diagnostics: null })
    try {
      if (generated) {
        const result = await previewPerfPlan(planText, { acknowledged, offset, limit: PAGE_SIZE, signal: controller.signal })
        if (controller.signal.aborted || sequence.current !== request) return
        setPreview({ key: startedKey, result: { kind: 'plan', value: result } })
        setAcknowledgement((chosen) => ({
          key: startedKey,
          codes: (chosen.key === startedKey ? chosen.codes : [])
            .filter((code) => (result.requires_acknowledgement ?? []).includes(code)),
        }))
      } else {
        const singleTaskId = `perf_single_preview_${Date.now()}_${request}`
        const result = await validatePerfSingle(promptPayload ?? {}, singleTaskId, controller.signal)
        if (controller.signal.aborted || sequence.current !== request) return
        setPreview({ key: startedKey, result: { kind: 'single', value: result } })
      }
    } catch (err) {
      if (controller.signal.aborted || sequence.current !== request) return
      updateFeedback({ diagnostics: errorDetails(err), error: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusyKey((currentBusy) => (currentBusy === startedKey ? null : currentBusy))
    }
  }

  const runSubmit = async () => {
    if (locked) return
    // Every start re-runs the full active validation (connection, source branch,
    // monitoring) and publishes errors, even though `canStart` mirrors it during
    // render. A client-invalid form still enables Start so the click can reveal
    // the error; it never reaches an invoke.
    if (!validateAndPublish(validate)) return
    if (inFlight.current || !current) return
    if (unknownAttempt && !((unknownAttempt.source === 'generated' && current.kind === 'plan') ||
        (unknownAttempt.source === 'prompts' && current.kind === 'single'))) {
      onAttemptTask?.(unknownAttempt.taskId)
      updateFeedback({ message: t('tasks.aiperf.matrix.attemptPending') })
      return
    }
    if (attempt?.accepted || promptAttempt?.outcome === 'accepted') return
    if (attempt && !attempt.accepted &&
        attempt.planHash !== (current.kind === 'plan' ? current.value.plan_hash : '')) {
      // An earlier plan's outcome is still unknown. Rotating to a new task id
      // here would silently start a second model run; the user must choose
      // explicitly whether to abandon it.
      updateFeedback({ message: t('tasks.aiperf.matrix.attemptPending') })
      return
    }

    if (current.kind === 'single') {
      const payload = promptPayload
      if (!payload) return
      if (promptAttempt?.outcome === 'unknown') {
        // The first invoke may still be running. Only query that original task;
        // sending the payload again could start a second load under a new id.
        onAttemptTask?.(promptAttempt.taskId)
        updateFeedback({ message: t('tasks.aiperf.promptQueryOnly') })
        return
      }
      const startedKey = key
      const startedIdentity = promptIdentity
      if (!claimSubmit()) return
      setBusyKey(startedKey)
      updateFeedback({ error: '', diagnostics: null, message: '' })
      try {
        const result = await onSubmit(payload)
        const outcome = result?.outcome ?? 'accepted'
        const taskId = result?.taskId ?? ''
        if (outcome === 'rejected') {
          setPromptAttempt(null)
          updateFeedback({ diagnostics: errorDetails(result?.error), error: errorText(result?.error) })
        } else if (outcome === 'unknown') {
          setPromptAttempt({ identity: startedIdentity, taskId, outcome: 'unknown' })
          setUnknownAttempt({ source: 'prompts', taskId })
          onAttemptTask?.(taskId)
          updateFeedback({ message: t('tasks.aiperf.promptUnconfirmed') })
        } else {
          setPromptAttempt({ identity: startedIdentity, taskId, outcome: 'accepted' })
          updateFeedback({ message: t('tasks.aiperf.promptAccepted') })
        }
      } catch (err) {
        setPromptAttempt({ identity: startedIdentity, taskId: '', outcome: 'unknown' })
        updateFeedback({ diagnostics: errorDetails(err), message: t('tasks.aiperf.promptUnconfirmed') })
      } finally {
        setBusyKey((currentBusy) => (currentBusy === startedKey ? null : currentBusy))
        releaseSubmit()
      }
      return
    }

    const startedKey = key
    const startedPlanText = planText
    const startedHash = current.value.plan_hash
    const startedAck = acknowledged
    const startedRuntime = runtime ?? undefined
    const next: PlanAttempt = attempt && !attempt.accepted && attempt.planHash === startedHash
      ? attempt
      : { planHash: startedHash, taskId: createPlanTaskId(), accepted: false }
    if (!claimSubmit()) return
    setAttempt(next)
    setBusyKey(startedKey)
    updateFeedback({ error: '', diagnostics: null, message: '' })
    try {
      const accepted = await submitPerfPlan(startedPlanText, next.taskId, {
        acknowledged: startedAck,
        expectedPlanHash: startedHash,
        ...(startedRuntime ? { runtime: startedRuntime } : {}),
      })
      setUnknownAttempt(null)
      setAttempt({ ...next, accepted: true })
      updateFeedback({
        message: t(accepted.duplicate ? 'tasks.aiperf.matrix.duplicate' : 'tasks.aiperf.matrix.accepted',
          { state: accepted.state }),
      })
      onPlanSubmitted?.(accepted.task_id || next.taskId)
    } catch (err) {
      // Only a definite 400/422 is the user's to fix. Every other failure leaves
      // the outcome unknown: the same task id is retried (the plan endpoint is
      // task-id idempotent) and the monitor queries that original task.
      const rejected = isDefiniteRejection(err)
      if (rejected) setAttempt(null)
      else {
        setAttempt({ ...next, unknown: true })
        setUnknownAttempt({ source: 'generated', taskId: next.taskId })
        onAttemptTask?.(next.taskId)
      }
      updateFeedback(rejected
        ? { diagnostics: errorDetails(err), error: errorText(err) }
        : { diagnostics: errorDetails(err), message: t('tasks.aiperf.matrix.uncertain') })
    } finally {
      setBusyKey((currentBusy) => (currentBusy === startedKey ? null : currentBusy))
      releaseSubmit()
    }
  }

  const startNewAttempt = () => {
    if (locked) return
    setAttempt(null)
    setPromptAttempt(null)
    setUnknownAttempt(null)
    setImportEpoch((value) => value + 1)
    updateFeedback({ message: '', error: '', diagnostics: null })
  }

  const runProbe = async () => {
    const monitoring = draft.monitoring
    if (probeBusy || locked) return
    if (!monitoring.enabled || !monitoring.nodes.length) {
      setProbeErrorState({ monitoring, message: t('tasks.aiperf.probeEmpty') })
      return
    }
    const request = ++probeSequence.current
    probePending.current?.abort()
    const controller = new AbortController()
    probePending.current = controller
    setProbeBusyState({ monitoring, request })
    setProbeErrorState(null)
    try {
      const result = await probePerfTelemetry(telemetryConfiguration(draft), controller.signal)
      if (controller.signal.aborted || probeSequence.current !== request) return
      setProbe({ monitoring, nodes: result.nodes ?? [] })
    } catch (err) {
      if (controller.signal.aborted || probeSequence.current !== request) return
      setProbeErrorState({ monitoring, message: err instanceof Error ? err.message : String(err) })
    } finally {
      // Clear only this request's stamp; a newer probe must stay busy.
      setProbeBusyState((current) => (current?.request === request ? null : current))
    }
  }

  const runExport = async () => {
    if (!generated || locked || inFlight.current) return
    const startedKey = key
    setBusyKey(startedKey)
    updateFeedback({ error: '' })
    try {
      const exported = await exportPerfPlan(planText)
      const link = document.createElement('a')
      link.href = URL.createObjectURL(new Blob([exported.content], { type: 'application/json' }))
      link.download = planExportFileName(current?.kind === 'plan' ? current.value.normalized.name : draft.name)
      link.click()
      URL.revokeObjectURL(link.href)
    } catch (err) {
      updateFeedback({ error: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusyKey((currentBusy) => (currentBusy === startedKey ? null : currentBusy))
    }
  }

  const toggleAck = (code: string) => setAcknowledgement((chosen) => {
    const codes = chosen.key === key ? chosen.codes : []
    return { key, codes: codes.includes(code) ? codes.filter((item) => item !== code) : [...codes, code] }
  })

  const handleSubmit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (locked) return
    if (validateAndPublish(validate)) void runPreview(0)
  }

  const textField = (
    name: string, labelKey: string, paramName: string | undefined, value: string,
    setValue: (next: string) => void,
    options: { type?: string; hintKey?: string; required?: boolean; placeholder?: string; autoComplete?: string } = {},
  ) => {
    const fieldId = id(name)
    const fieldError = errorFor(fieldId)
    return (
      <Field id={fieldId} name={name} labelKey={labelKey} paramName={paramName} hintKey={options.hintKey}
        required={options.required} error={fieldError}>
        {(aria) => <input {...aria} type={options.type ?? 'text'}
          step={options.type === 'number' && (name === 'sampleInterval' || name === 'maxGap') ? 'any' : undefined}
          autoComplete={options.autoComplete} className={inputClass(fieldError)} value={value}
          placeholder={options.placeholder} onChange={(event) => setValue(event.target.value)} />}
      </Field>
    )
  }
  const checkField = (name: string, labelKey: string, paramName: string | undefined, checked: boolean,
    setChecked: (next: boolean) => void, hintKey?: string) => {
    const fieldId = id(name)
    const fieldError = errorFor(fieldId)
    return <Field id={fieldId} name={name} labelKey={labelKey} paramName={paramName} hintKey={hintKey} error={fieldError}>
      {(aria) => <input {...aria} type="checkbox" checked={checked}
        onChange={(event) => setChecked(event.target.checked)} />}
    </Field>
  }

  const planState = current?.kind === 'plan' ? canSubmitPlan(current.value, acknowledged) : null
  const singleReady = Boolean(current?.kind === 'single' && current.value.engine_available)
  const requiredProbeFailed = Boolean(draft.monitoring.enabled && draft.monitoring.required && probeCurrent &&
    probeCurrent.nodes.some((node) => node.status !== 'available'))
  // A plan retry may reuse its task id only for the exact plan that was sent;
  // the plan endpoint is task-id idempotent, so that retry still starts one run.
  const planRetryAllowed = Boolean(attempt && !attempt.accepted && current?.kind === 'plan' &&
    attempt.planHash === current.value.plan_hash)
  const promptPending = promptAttempt?.outcome === 'unknown'
  const acceptedAttempt = Boolean(attempt?.accepted || promptAttempt?.outcome === 'accepted')
  const attemptPending = Boolean((attempt && !attempt.accepted) || promptPending || unknownAttempt)
  // Starting while an earlier outcome is unconfirmed is legal only as that
  // idempotent same-plan retry (or, for prompts, as a query of the original
  // task). Everything else waits for an explicit new task.
  const attemptBlocks = Boolean((attempt && !planRetryAllowed) || acceptedAttempt ||
    (unknownAttempt && !((unknownAttempt.source === 'generated' && current?.kind === 'plan') ||
      (unknownAttempt.source === 'prompts' && current?.kind === 'single'))))
  const canStart = Boolean(capability?.available) && !busy && !locked && !attemptBlocks && !requiredProbeFailed &&
    (clientInvalid || (current !== null &&
      (current.kind === 'plan' ? Boolean(planState?.allowed) : singleReady)))
  const blockedKey = attemptBlocks
    ? (acceptedAttempt ? 'tasks.aiperf.attemptAccepted' : 'tasks.aiperf.matrix.attemptPending')
    : !current ? (generated ? 'tasks.aiperf.matrix.previewFirst' : 'tasks.aiperf.promptPreviewFirst')
      : current.kind === 'plan' && planState
        ? planState.allowed ? 'tasks.aiperf.matrix.ready'
          : planState.reason === 'engineUnavailable' ? 'tasks.aiperf.matrix.engineUnavailable'
            : planState.reason === 'blocked' ? 'tasks.aiperf.matrix.blocked'
              : planState.reason === 'missingAcknowledgement' ? 'tasks.aiperf.matrix.missingAck'
                : 'tasks.aiperf.matrix.previewFirst'
        : singleReady ? 'tasks.aiperf.matrix.ready'
          : 'tasks.aiperf.matrix.engineUnavailable'
  const plan = current?.kind === 'plan' ? current.value : null
  const single = current?.kind === 'single' ? current.value : null
  const runs = plan?.runs ?? []
  const total = plan?.total_runs ?? 0
  const page = plan ? Math.floor(plan.offset / PAGE_SIZE) : 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const localRuns = generated ? matrixRunCount(generatedPlanInput(draft)) : 0
  const lines = promptLines(draft.prompts.text)

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      <fieldset disabled={locked} className="min-w-0">{toolbar}</fieldset>
      <p className="text-sm" role="status">AIPerf 0.13.0: {engines.loading ? t('tasks.aiperf.checking') : capability?.available ? t('tasks.aiperf.available') : t('tasks.aiperf.unavailable')}
        {!capability?.available && (capability?.reason || engines.error) ? ` — ${capability?.reason || engines.error}` : ''}</p>
      <PerfPlanImportPanel key={importEpoch} disabled={locked || Boolean(unknownAttempt && unknownAttempt.source !== 'import')}
        pendingTaskId={unknownAttempt?.source === 'import' ? unknownAttempt.taskId : undefined} beginSubmit={claimSubmit} endSubmit={releaseSubmit}
        onAttempt={(taskId) => { setUnknownAttempt({ source: 'import', taskId }); onAttemptTask?.(taskId) }}
        onSubmitted={(taskId) => { setUnknownAttempt(null); onPlanSubmitted?.(taskId) }} />
      <fieldset disabled={locked} className="space-y-4">
        <Field id={id('inputSource')} name="inputSource" labelKey="tasks.aiperf.inputSource"
          hintKey="tasks.aiperf.sourceHint">
          {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.inputSource}
            onChange={(event) => onChange({ ...draft, inputSource: event.target.value as AIPerfTaskDraft['inputSource'] })}>
            <option value="generated">{t('tasks.aiperf.sourceGenerated')}</option>
            <option value="prompts">{t('tasks.aiperf.sourcePrompts')}</option>
          </select>}
        </Field>

        <section className="space-y-3" aria-labelledby="aiperf-input-title">
          <h3 id="aiperf-input-title" className="text-sm font-semibold">
            {t(generated ? 'tasks.aiperf.generatedTitle' : 'tasks.aiperf.promptsTitle')}
          </h3>
          {textField('tokenizer', 'tasks.aiperf.tokenizer', 'tokenizer_path', draft.tokenizer,
            (tokenizer) => onChange({ ...draft, tokenizer }), {
              required: true, placeholder: 'Qwen/Qwen2.5-0.5B-Instruct', hintKey: 'tasks.aiperf.matrix.tokenizerHint',
            })}
          {generated ? <>
            <div className="grid min-w-0 gap-3 md:grid-cols-2">
              {textField('name', 'tasks.aiperf.matrix.name', 'name', draft.name,
                (name) => onChange({ ...draft, name }), { placeholder: 'e.g. qwen-32k-4c' })}
              {textField('inputLengths', 'tasks.aiperf.matrix.inputLengths', 'workload.input_lengths',
                draft.generated.inputLengths, (value) => updateGenerated('inputLengths', value))}
              {textField('outputLimits', 'tasks.aiperf.matrix.outputLimits', 'load.output_token_limits',
                draft.generated.outputLimits, (value) => updateGenerated('outputLimits', value))}
              {textField('requestCount', 'tasks.aiperf.requestCount', 'load.request_count',
                draft.generated.requestCount, (value) => updateGenerated('requestCount', value), { type: 'number' })}
              {textField('maxModelLen', 'tasks.aiperf.matrix.maxModelLen', 'deployment.declared_max_model_len',
                draft.generated.maxModelLen, (value) => updateGenerated('maxModelLen', value),
                { type: 'number', required: true, hintKey: 'tasks.aiperf.matrix.maxModelLenHint' })}
              {draft.generated.mode === 'closed_loop'
                ? textField('closedLoads', 'tasks.aiperf.matrix.loadsClosed', 'load.points[].concurrency',
                  draft.generated.closedLoads, (value) => updateGenerated('closedLoads', value),
                  { hintKey: 'tasks.aiperf.loadModeHint' })
                : textField('openRates', 'tasks.aiperf.matrix.loadsOpen', 'load.points[].rate',
                  draft.generated.openRates, (value) => updateGenerated('openRates', value),
                  { hintKey: 'tasks.aiperf.loadModeHint' })}
            </div>
            <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.generatedHint')}</p>
            {checkField('comparePrefix', 'tasks.aiperf.comparePrefix', undefined, draft.generated.comparePrefix,
              (comparePrefix) => updateGenerated('comparePrefix', comparePrefix), 'tasks.aiperf.compareHint')}
            {draft.generated.comparePrefix && <div className="grid min-w-0 gap-3 md:grid-cols-2">
              {textField('ratios', 'tasks.aiperf.ratios', 'workload.target_ratios', draft.generated.ratios,
                (value) => updateGenerated('ratios', value), { hintKey: 'tasks.aiperf.ratiosHint' })}
              {textField('pool', 'tasks.aiperf.pool', 'workload.prefix_pool_size', draft.generated.pool,
                (value) => updateGenerated('pool', value), { type: 'number', hintKey: 'tasks.aiperf.matrix.poolHint' })}
            </div>}
          </> : <>
            <Field id={id('prompts')} name="prompts" labelKey="tasks.aiperf.prompts" paramName="aiperf.input_prompts"
              hintKey="tasks.aiperf.promptTextHint" error={errorFor(id('prompts'))}>
              {(aria) => <textarea {...aria} className={inputClass(errorFor(id('prompts')))} rows={5}
                value={draft.prompts.text} onChange={(event) => updatePrompts('text', event.target.value)} />}
            </Field>
            <div className="grid min-w-0 gap-3 md:grid-cols-2">
              <Field id={id('promptCount')} name="promptCount" labelKey="tasks.aiperf.promptCount"
                paramName="aiperf.load.request_count" hintKey="tasks.aiperf.promptCountHint">
                {(aria) => <input {...aria} readOnly value={lines.length} className={FORM_INPUT_CLASS} />}
              </Field>
              {textField('promptOutputLimit', 'tasks.aiperf.promptOutputLimit', 'aiperf.load.output_token_limit',
                draft.prompts.outputLimit, (value) => updatePrompts('outputLimit', value), { type: 'number' })}
              <Field id={id('promptMode')} name="promptMode" labelKey="tasks.aiperf.loadMode" paramName="aiperf.load.mode">
                {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.prompts.mode}
                  onChange={(event) => updatePrompts('mode', event.target.value as AIPerfTaskDraft['prompts']['mode'])}>
                  <option value="closed_loop">{t('tasks.aiperf.closed')}</option>
                  <option value="open_loop">{t('tasks.aiperf.open')}</option>
                </select>}
              </Field>
              {draft.prompts.mode === 'closed_loop'
                ? textField('promptConcurrency', 'tasks.aiperf.promptConcurrency', 'aiperf.load.points[0].concurrency',
                  draft.prompts.concurrency, (value) => updatePrompts('concurrency', value), { type: 'number' })
                : textField('promptRate', 'tasks.aiperf.promptRate', 'aiperf.load.points[0].rate',
                  draft.prompts.rate, (value) => updatePrompts('rate', value), { type: 'number' })}
            </div>
            <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.promptLimitHint')}</p>
          </>}
        </section>

        {generated && <details className="rounded border border-[var(--border)] p-3" open={showMore}
          onToggle={(event) => {
            if ((event.target as HTMLDetailsElement).open !== showMore) toggleMore()
          }}>
          <summary className="cursor-pointer text-sm font-medium">{t('tasks.aiperf.matrix.advanced')}</summary>
          <div className="mt-3 space-y-3">
            <div className="grid min-w-0 gap-3 md:grid-cols-2">
              <Field id={id('mode')} name="mode" labelKey="tasks.aiperf.loadMode" paramName="load.mode"
                hintKey="tasks.aiperf.loadModeHint">
                {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.generated.mode}
                  onChange={(event) => updateGenerated('mode', event.target.value as AIPerfTaskDraft['generated']['mode'])}>
                  <option value="closed_loop">{t('tasks.aiperf.closed')}</option>
                  <option value="open_loop">{t('tasks.aiperf.open')}</option>
                </select>}
              </Field>
              {textField('seed', 'tasks.aiperf.seed', 'workload.seed', draft.generated.seed,
                (value) => updateGenerated('seed', value), { type: 'number' })}
              {textField('repetitions', 'tasks.aiperf.repetitions', 'load.repetitions', draft.generated.repetitions,
                (value) => updateGenerated('repetitions', value), { type: 'number', hintKey: 'tasks.aiperf.repetitionsHint' })}
              <Field id={id('order')} name="order" labelKey="tasks.aiperf.matrix.order" paramName="execution.order">
                {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.generated.order}
                  onChange={(event) => updateGenerated('order', event.target.value as AIPerfTaskDraft['generated']['order'])}>
                  <option value="seeded_shuffle">{t('tasks.aiperf.matrix.orderShuffle')}</option>
                  <option value="load_then_input">{t('tasks.aiperf.matrix.orderLoad')}</option>
                </select>}
              </Field>
              <Field id={id('deploymentMode')} name="deploymentMode" labelKey="tasks.aiperf.deployment" paramName="deployment.mode">
                {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={draft.deploymentMode}
                  onChange={(event) => onChange({ ...draft, deploymentMode: event.target.value as AIPerfTaskDraft['deploymentMode'] })}>
                  <option value="single">{t('tasks.aiperf.single')}</option>
                  <option value="pd">{t('tasks.aiperf.pd')}</option>
                </select>}
              </Field>
            </div>
            {checkField('warmupEnabled', 'tasks.aiperf.matrix.warmup', undefined, draft.generated.warmup.enabled,
              (enabled) => updateWarmup('enabled', enabled), 'tasks.aiperf.warmupHint')}
            {draft.generated.warmup.enabled && <div className="grid min-w-0 gap-3 md:grid-cols-2">
              {textField('warmupRequests', 'tasks.aiperf.matrix.warmupRequests', 'warmup.request_count',
                draft.generated.warmup.requests, (value) => updateWarmup('requests', value), { type: 'number' })}
              {textField('warmupInput', 'tasks.aiperf.matrix.warmupInput', 'warmup.input_tokens',
                draft.generated.warmup.inputTokens, (value) => updateWarmup('inputTokens', value), { type: 'number' })}
              {textField('warmupOutput', 'tasks.aiperf.matrix.warmupOutput', 'warmup.output_token_limit',
                draft.generated.warmup.outputTokens, (value) => updateWarmup('outputTokens', value), { type: 'number' })}
              {textField('warmupConcurrency', 'tasks.aiperf.matrix.warmupConcurrency', 'warmup.concurrency',
                draft.generated.warmup.concurrency, (value) => updateWarmup('concurrency', value), { type: 'number' })}
            </div>}
          </div>
        </details>}

        <section className="space-y-3" aria-labelledby="aiperf-monitoring-title">
          <h3 id="aiperf-monitoring-title" className="text-sm font-semibold">{t('tasks.aiperf.matrix.monitoring')}</h3>
          {checkField('monitoringEnabled', 'tasks.aiperf.matrix.monitoringEnable', undefined, draft.monitoring.enabled,
            (enabled) => updateMonitoring('enabled', enabled), 'tasks.aiperf.matrix.monitoringHint')}
          {draft.monitoring.enabled ? <>
            <div className="grid min-w-0 gap-3 md:grid-cols-2">
              {textField('sampleInterval', 'tasks.aiperf.sampleInterval', 'telemetry.sample_interval_seconds',
                draft.monitoring.sampleInterval, (value) => updateMonitoring('sampleInterval', value), { type: 'number' })}
              {textField('maxGap', 'tasks.aiperf.maxGap', 'telemetry.max_scrape_gap_seconds',
                draft.monitoring.maxGap, (value) => updateMonitoring('maxGap', value), { type: 'number' })}
            </div>
            {checkField('required', 'tasks.aiperf.required', 'telemetry.required', draft.monitoring.required,
              (required) => updateMonitoring('required', required))}
            <MetricsNodeEditor idPrefix="aiperf" paramPrefix={generated ? 'runtime.telemetry' : 'aiperf.telemetry'}
              nodes={draft.monitoring.nodes} errorFor={errorFor} disabled={locked}
              onChange={(nodes) => updateMonitoring('nodes', nodes)} />
          </> : <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.monitorOffHint')}</p>}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" variant="outline" size="sm"
              disabled={locked || probeBusy || !draft.monitoring.enabled || !draft.monitoring.nodes.length}
              onClick={() => void runProbe()}>
              {t('tasks.aiperf.probeCheck')}
            </Button>
            <span className="text-xs text-[var(--text-muted)]">{probeBusy ? t('tasks.aiperf.checking') : t('tasks.aiperf.probeHint')}</span>
          </div>
          {probeError && <p role="alert" className="text-sm text-[var(--danger)]">{probeError}</p>}
          {probeCurrent && <div className="text-sm" role="status">
            {probeCurrent.nodes.map((node) => <p key={node.node_id}>{node.node_id}: {node.status} ({node.metric_names?.join(', ') || '—'})</p>)}
          </div>}
          {probe && !probeCurrent && draft.monitoring.enabled &&
            <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.probeStale')}</p>}
          {requiredProbeFailed && <p role="alert" className="text-sm text-[var(--danger)]">{t('tasks.aiperf.nodeUnavailable')}</p>}
        </section>

        <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.limitations')}</p>
        {generated && <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.matrix.axesHint', { count: formatTokens(localRuns) })}</p>}
        {(error || message || !canStart) && <p role="alert" className={error ? 'text-sm text-[var(--danger)]' : 'text-sm'}>
          {error || message || t(blockedKey)}
        </p>}
        {(diagnostics?.diagnostics ?? []).length > 0 && <ul className="space-y-1 text-xs">
          {diagnostics?.diagnostics?.map((item, index) => <li key={`${item.code}-${index}`}>
            <span className="font-mono">[{item.severity}] {item.code}</span> — {item.message}
          </li>)}
        </ul>}

        {(attemptPending || acceptedAttempt) && <div className="space-y-2 rounded border border-[var(--border)] p-3 text-sm" role="status">
          <p>{t(attemptPending ? 'tasks.aiperf.attemptUnconfirmed' : 'tasks.aiperf.attemptAccepted')}</p>
          <p className="truncate font-mono text-xs">{attempt?.taskId ?? promptAttempt?.taskId}</p>
          <div className="flex flex-wrap gap-2">
            {attemptPending && onAttemptTask && <Button type="button" variant="outline" size="sm" disabled={locked}
              onClick={() => onAttemptTask(attempt?.taskId ?? promptAttempt?.taskId ?? '')}>
              {t('tasks.aiperf.attemptQuery')}
            </Button>}
            <Button type="button" variant="outline" size="sm" disabled={locked} onClick={startNewAttempt}>
              {t('tasks.aiperf.attemptNew')}
            </Button>
          </div>
        </div>}

        {plan && <div className="space-y-3" role="status">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs md:grid-cols-4">
            <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.matrix.totalRuns')}</dt>
              <dd>{formatTokens(plan.total_runs)}</dd></div>
            <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.matrix.budget')}</dt>
              <dd>{formatTokens(plan.budgets.formal_requests)} + {formatTokens(plan.budgets.warmup_requests)} + {formatTokens(plan.budgets.primer_requests)}</dd></div>
            <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.matrix.outputLimits')}</dt>
              <dd>{(plan.output_token_limits ?? []).join(', ') || '—'}</dd></div>
            <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.planImport.planHash')}</dt>
              <dd className="truncate font-mono">{plan.plan_hash.slice(0, 12)}</dd></div>
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
            <Button type="button" variant="outline" size="sm" disabled={busy || locked || plan.offset <= 0}
              onClick={() => void runPreview(Math.max(0, plan.offset - PAGE_SIZE))}>{t('tasks.aiperf.matrix.previous')}</Button>
            <span>{t('tasks.aiperf.matrix.page', { page: page + 1, pages })}</span>
            <Button type="button" variant="outline" size="sm" disabled={busy || locked || page + 1 >= pages}
              onClick={() => void runPreview(plan.offset + PAGE_SIZE)}>{t('tasks.aiperf.matrix.next')}</Button>
          </div>
          <div>
            <h4 className="text-sm font-medium">{t('tasks.aiperf.planImport.diagnostics')}</h4>
            {(plan.diagnostics ?? []).length === 0 ? <p className="text-xs">{t('tasks.aiperf.planImport.noDiagnostics')}</p> : (
              <ul className="space-y-1 text-xs">
                {(plan.diagnostics ?? []).map((item, index) => <li key={`${item.code}-${index}`}>
                  <span className="font-mono">[{item.severity}] {item.code}</span>
                  {item.case_id ? ` (${item.case_id})` : ''} — {item.message}
                </li>)}
              </ul>
            )}
          </div>
          {(plan.requires_acknowledgement ?? []).length > 0 && <fieldset className="space-y-1">
            <legend className="text-sm font-medium">{t('tasks.aiperf.planImport.acknowledgements')}</legend>
            {(plan.requires_acknowledgement ?? []).map((code) => <label key={code} className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={acknowledged.includes(code)} onChange={() => toggleAck(code)} />
              <span className="font-mono">{code}</span>
            </label>)}
          </fieldset>}
        </div>}

        {single && <div className="space-y-1 text-sm" role="status">
          <p>{t('tasks.aiperf.singleSummary', { requests: single.profiling_requests })}</p>
          <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.singleUnknown')}</p>
          {single.warnings?.map((warning) => <p key={warning}>{warning}</p>)}
        </div>}

        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="outline" disabled={busy || !capability?.available || locked}>
            {t('tasks.aiperf.preview')}
          </Button>
          <Button type="button" disabled={!canStart} onClick={() => void runSubmit()}>
            {t('tasks.aiperf.start')}
          </Button>
          {generated && <Button type="button" variant="outline" disabled={busy || locked} onClick={() => void runExport()}>
            {t('tasks.aiperf.matrix.export')}
          </Button>}
        </div>
      </fieldset>
    </form>
  )
}
