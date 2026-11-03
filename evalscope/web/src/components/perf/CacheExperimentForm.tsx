import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { getPerfEngines, validatePerfExperiment } from '@/api/perf'
import type { PerfPlanResponse } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import { useAsyncResource } from '@/hooks/useAsyncResource'
import { useTaskForm } from '@/components/tasks/useTaskForm'
import { endpointOrigin, type FieldErrors, type ModelConnection } from '@/components/tasks/taskConfig'
import {
  buildAIPerfPayload, newAIPerfNode, validateAIPerfDraft, type AIPerfDraft, type AIPerfNodeDraft,
} from '@/components/tasks/aiperfDraft'
import Field from '@/components/ui/Field'
import { FORM_INPUT_CLASS, inputClass } from '@/components/ui/formStyles'
import Button from '@/components/ui/Button'

interface Props {
  connection: ModelConnection
  onSubmit: (config: Record<string, unknown>) => Promise<void>
  disabled: boolean
  draft: AIPerfDraft
  onChange: (draft: AIPerfDraft) => void
  toolbar?: ReactNode
  onValidateConnection: () => FieldErrors
}

type TextField = Exclude<keyof AIPerfDraft, 'nodes' | 'required' | 'mode' | 'deploymentMode'>

export default function CacheExperimentForm({ connection, onSubmit, disabled, draft, onChange, toolbar, onValidateConnection }: Props) {
  const { t } = useLocale()
  const engines = useAsyncResource((signal) => getPerfEngines(signal), [])
  const capability = engines.data?.engines.find((item) => item.engine === 'aiperf')
  const [preview, setPreview] = useState<{ key: string; value: PerfPlanResponse } | null>(null)
  const [error, setError] = useState('')
  const [checkingKey, setCheckingKey] = useState<string | null>(null)
  const [probe, setProbe] = useState(false)
  const sequence = useRef(0)
  const pending = useRef<AbortController | null>(null)
  const payload = buildAIPerfPayload(draft, connection)
  const key = JSON.stringify({ payload, probe })
  const checking = checkingKey === key
  const current = preview?.key === key ? preview.value : null
  const requiredNodesFailed = Boolean(draft.required && current?.metrics_probe === 'completed' &&
    current.nodes?.some((node) => node.status !== 'available'))

  useEffect(() => {
    sequence.current += 1
    pending.current?.abort()
    return () => pending.current?.abort()
  }, [key])

  const values: Record<string, string | boolean> = {
    ...Object.fromEntries(Object.entries(draft).filter(([name]) => name !== 'nodes').map(([name, value]) => [`aiperf-${name}`, value])),
    'aiperf-nodes': JSON.stringify(draft.nodes.map((node) => ({ ...node, bearerToken: '' }))),
  }
  for (const node of draft.nodes) {
    for (const name of ['nodeId', 'role', 'engine', 'url', 'labels', 'bearerToken'] as const)
      values[`aiperf-node-${node.id}-${name}`] = node[name]
  }
  const domOrder = ['connection-api', 'connection-model', 'connection-url',
    ...['tokenizer', 'ratios', 'inputTokens', 'requestCount', 'outputTokens', 'loads', 'repetitions', 'pool',
      'seed', 'sampleInterval', 'maxGap'].map((name) => `aiperf-${name}`),
    ...draft.nodes.flatMap((node) => ['nodeId', 'url', 'labels', 'bearerToken'].map((name) => `aiperf-node-${node.id}-${name}`)),
    'aiperf-nodes']
  const { errorFor, submitHandler } = useTaskForm({ domOrder, values })
  const validate = () => ({
    ...onValidateConnection(),
    ...Object.fromEntries(Object.entries(validateAIPerfDraft(draft)).map(([field, message]) => [`aiperf-${field}`, message])),
  })
  const update = <K extends keyof AIPerfDraft>(field: K, value: AIPerfDraft[K]) => onChange({ ...draft, [field]: value })
  const updateNode = (id: string, patch: Partial<AIPerfNodeDraft>) => update('nodes', draft.nodes.map((node) => {
    if (node.id !== id) return node
    const clearToken = patch.url !== undefined && endpointOrigin(node.url) !== endpointOrigin(patch.url)
    return { ...node, ...patch, ...(clearToken ? { bearerToken: '' } : {}) }
  }))
  const previewPlan = async () => {
    const request = ++sequence.current
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setCheckingKey(key)
    setError('')
    try {
      const value = await validatePerfExperiment(payload, `preview_${Date.now()}_${request}`, controller.signal, probe)
      if (!controller.signal.aborted && sequence.current === request) setPreview({ key, value })
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
    return <Field id={id} name={name} labelKey={`tasks.aiperf.${name}`} error={error}>
      {(aria) => <input {...aria} type={type} step={name === 'sampleInterval' || name === 'maxGap' ? 'any' : undefined}
        className={inputClass(error)} value={draft[name]}
        onChange={(event) => update(name, event.target.value)} />}
    </Field>
  }
  const nodeField = (node: AIPerfNodeDraft, name: 'nodeId' | 'url' | 'labels' | 'bearerToken', type = 'text') => {
    const id = `aiperf-node-${node.id}-${name}`
    const error = errorFor(id)
    const label = name === 'bearerToken' ? 'token' : name
    return <Field id={id} name={id} labelKey={`tasks.aiperf.${label}`} error={error}
      autoComplete={name === 'bearerToken' ? 'off' : undefined}>
      {(aria) => <input {...aria} type={type} className={inputClass(error)} value={node[name]}
        onChange={(event) => updateNode(node.id, { [name]: event.target.value })} />}
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
      <fieldset disabled={disabled} className="space-y-4">
        <div className="grid min-w-0 gap-3 md:grid-cols-2">
          {textField('tokenizer')}{textField('ratios')}{textField('inputTokens', 'number')}
          {textField('requestCount', 'number')}{textField('outputTokens', 'number')}{textField('loads')}
          {textField('repetitions', 'number')}{textField('pool', 'number')}{textField('seed', 'number')}
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
        <section className="space-y-3" aria-labelledby="aiperf-nodes-title">
          <h3 id="aiperf-nodes-title" className="text-sm font-semibold">{t('tasks.aiperf.nodes')}</h3>
          {draft.nodes.map((node, index) => <fieldset key={node.id} className="min-w-0 space-y-3 rounded border border-[var(--border)] p-3">
            <legend className="px-1 text-sm">{t('tasks.aiperf.node', { number: index + 1 })}</legend>
            <div className="grid min-w-0 gap-3 md:grid-cols-2">
              {nodeField(node, 'nodeId')}
              <Field id={`aiperf-node-${node.id}-role`} name={`role-${node.id}`} labelKey="tasks.aiperf.role">
                {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={node.role}
                  onChange={(event) => updateNode(node.id, { role: event.target.value as AIPerfNodeDraft['role'] })}>
                  <option value="prefill">{t('tasks.aiperf.prefill')}</option><option value="decode">{t('tasks.aiperf.decode')}</option>
                </select>}
              </Field>
              <Field id={`aiperf-node-${node.id}-engine`} name={`engine-${node.id}`} labelKey="tasks.aiperf.engine">
                {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={node.engine}
                  onChange={(event) => updateNode(node.id, { engine: event.target.value as AIPerfNodeDraft['engine'] })}>
                  <option value="vllm">vLLM</option><option value="sglang">SGLang</option>
                </select>}
              </Field>
              {nodeField(node, 'url')}{nodeField(node, 'labels')}{nodeField(node, 'bearerToken', 'password')}
            </div>
            <Button type="button" variant="ghost" size="sm" onClick={() => update('nodes', draft.nodes.filter((item) => item.id !== node.id))}>
              {t('tasks.aiperf.removeNode')}
            </Button>
          </fieldset>)}
          <Button id="aiperf-nodes" type="button" variant="outline" size="sm" disabled={draft.nodes.length >= 64}
            aria-describedby={errorFor('aiperf-nodes') ? 'aiperf-nodes-error' : undefined}
            onClick={() => update('nodes', [...draft.nodes, newAIPerfNode()])}>{t('tasks.aiperf.addNode')}</Button>
          {errorFor('aiperf-nodes') && <p id="aiperf-nodes-error" role="alert" className="text-xs text-[var(--danger)]">{errorFor('aiperf-nodes')}</p>}
          <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.tokenHint')}</p>
        </section>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={probe}
          onChange={(event) => setProbe(event.target.checked)} />{t('tasks.aiperf.probe')}</label>
        <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.limitations')}</p>
        {error && <p role="alert" className="text-sm text-[var(--danger)]">{error}</p>}
        {current && <div className="text-sm" role="status">
          <p>{t('tasks.aiperf.plan')}: {current.plan.subruns.length} {t('tasks.aiperf.runs')}, {current.plan.profiling_requests} {t('tasks.aiperf.requests')}, {current.plan.primer_requests} {t('tasks.aiperf.primers')}</p>
          <p>{t('tasks.aiperf.maxOutput')}: {current.plan.max_profiling_output_tokens ?? '—'}</p>
          {current.nodes?.map((node) => <p key={node.node_id}>{node.node_id}: {node.status} ({node.metric_names?.join(', ') || '—'})</p>)}
          {current.warnings?.map((warning) => <p key={warning}>{warning}</p>)}
          {requiredNodesFailed && <p className="text-[var(--danger)]">{t('tasks.aiperf.nodeUnavailable')}</p>}
        </div>}
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="outline" disabled={checking || !capability?.available}>{t('tasks.aiperf.preview')}</Button>
          <Button type="button" disabled={checking || !current?.engine_available || requiredNodesFailed}
            onClick={() => void onSubmit(payload)}>{t('tasks.aiperf.start')}</Button>
        </div>
      </fieldset>
    </form>
  )
}
