import Button from '@/components/ui/Button'
import Field from '@/components/ui/Field'
import { FORM_INPUT_CLASS, inputClass } from '@/components/ui/formStyles'
import { useLocale } from '@/contexts/LocaleContext'
import { endpointOrigin } from '@/components/tasks/taskConfig'
import { newAIPerfNode, type AIPerfNodeDraft } from '@/components/tasks/aiperfDraft'

const NODE_LABELS = {
  nodeId: 'tasks.aiperf.nodeId',
  url: 'tasks.aiperf.url',
  labels: 'tasks.aiperf.labels',
  bearerToken: 'tasks.aiperf.token',
} as const

const NODE_PARAMS = {
  nodeId: 'targets[].node_id',
  url: 'targets[].url',
  labels: 'targets[].labels',
  bearerToken: 'targets[].headers.Authorization',
} as const

interface Props {
  /** Prefix for control ids; node ids must stay unique across sibling forms. */
  idPrefix: string
  /** Full parameter path of the telemetry block this editor writes. */
  paramPrefix: string
  nodes: AIPerfNodeDraft[]
  onChange: (nodes: AIPerfNodeDraft[]) => void
  errorFor: (id: string) => string | undefined
  disabled?: boolean
}

/**
 * Metrics endpoint editor shared by both AIPerf input sources.
 *
 * A node's bearer token is cleared whenever its URL moves to another origin: a
 * credential pasted for one endpoint must never be sent to another.
 */
export default function MetricsNodeEditor({
  idPrefix, paramPrefix, nodes, onChange, errorFor, disabled = false,
}: Props) {
  const { t } = useLocale()
  const addId = `${idPrefix}-nodes`
  const updateNode = (id: string, patch: Partial<AIPerfNodeDraft>) => onChange(nodes.map((node) => {
    if (node.id !== id) return node
    const clearToken = patch.url !== undefined && endpointOrigin(node.url) !== endpointOrigin(patch.url)
    return { ...node, ...patch, ...(clearToken ? { bearerToken: '' } : {}) }
  }))
  const nodeField = (node: AIPerfNodeDraft, name: keyof typeof NODE_LABELS, type = 'text') => {
    const id = `${idPrefix}-node-${node.id}-${name}`
    const error = errorFor(id)
    return (
      <Field id={id} name={id} labelKey={NODE_LABELS[name]} paramName={`${paramPrefix}.${NODE_PARAMS[name]}`}
        error={error}
        autoComplete={name === 'bearerToken' ? 'off' : undefined}>
        {(aria) => <input {...aria} type={type} className={inputClass(error)} value={node[name]}
          onChange={(event) => updateNode(node.id, { [name]: event.target.value })} />}
      </Field>
    )
  }
  return (
    <section className="space-y-3" aria-labelledby={`${idPrefix}-nodes-title`}>
      <h3 id={`${idPrefix}-nodes-title`} className="text-sm font-semibold">{t('tasks.aiperf.nodes')}</h3>
      <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.nodesHint')}</p>
      {nodes.map((node, index) => (
        <fieldset key={node.id} className="min-w-0 space-y-3 rounded border border-[var(--border)] p-3">
          <legend className="px-1 text-sm">{t('tasks.aiperf.node', { number: index + 1 })}</legend>
          <div className="grid min-w-0 gap-3 md:grid-cols-2">
            {nodeField(node, 'nodeId')}
            <Field id={`${idPrefix}-node-${node.id}-role`} name={`role-${node.id}`} labelKey="tasks.aiperf.role"
              paramName={`${paramPrefix}.targets[].role`}>
              {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={node.role}
                onChange={(event) => updateNode(node.id, { role: event.target.value as AIPerfNodeDraft['role'] })}>
                <option value="prefill">{t('tasks.aiperf.prefill')}</option>
                <option value="decode">{t('tasks.aiperf.decode')}</option>
              </select>}
            </Field>
            <Field id={`${idPrefix}-node-${node.id}-engine`} name={`engine-${node.id}`} labelKey="tasks.aiperf.engine"
              paramName={`${paramPrefix}.targets[].engine`}>
              {(aria) => <select {...aria} className={FORM_INPUT_CLASS} value={node.engine}
                onChange={(event) => updateNode(node.id, { engine: event.target.value as AIPerfNodeDraft['engine'] })}>
                <option value="vllm">vLLM</option><option value="sglang">SGLang</option>
              </select>}
            </Field>
            {nodeField(node, 'url')}{nodeField(node, 'labels')}{nodeField(node, 'bearerToken', 'password')}
          </div>
          <Button type="button" variant="ghost" size="sm" disabled={disabled}
            onClick={() => onChange(nodes.filter((item) => item.id !== node.id))}>
            {t('tasks.aiperf.removeNode')}
          </Button>
        </fieldset>
      ))}
      <Button id={addId} type="button" variant="outline" size="sm" disabled={disabled || nodes.length >= 64}
        aria-describedby={errorFor(addId) ? `${addId}-error` : undefined}
        onClick={() => onChange([...nodes, newAIPerfNode()])}>{t('tasks.aiperf.addNode')}</Button>
      {errorFor(addId) && <p id={`${addId}-error`} role="alert" className="text-xs text-[var(--danger)]">{errorFor(addId)}</p>}
      <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.tokenHint')}</p>
    </section>
  )
}
