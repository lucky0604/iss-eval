import { FORM_MESSAGE_KEYS, validateApiUrl } from '@/domain/form/validation'
import { connectionPayload, type FieldErrors, type ModelConnection } from './taskConfig'

export interface AIPerfNodeDraft {
  id: string
  nodeId: string
  role: 'prefill' | 'decode'
  engine: 'vllm' | 'sglang'
  url: string
  labels: string
  bearerToken: string
}

export interface AIPerfDraft {
  runType: 'cache' | 'single'
  prompts: string
  tokenizer: string
  ratios: string
  inputTokens: string
  requestCount: string
  outputTokens: string
  loads: string
  mode: 'closed_loop' | 'open_loop'
  deploymentMode: 'single' | 'pd'
  repetitions: string
  pool: string
  seed: string
  required: boolean
  sampleInterval: string
  maxGap: string
  nodes: AIPerfNodeDraft[]
}

export const DEFAULT_AIPERF: AIPerfDraft = {
  runType: 'cache', prompts: '', tokenizer: '', ratios: '0, 80, 90', inputTokens: '1024', requestCount: '10', outputTokens: '128',
  loads: '1', mode: 'closed_loop', deploymentMode: 'single', repetitions: '1', pool: '1', seed: '42', required: false,
  sampleInterval: '1', maxGap: '', nodes: [],
}

export function newAIPerfNode(): AIPerfNodeDraft {
  const id = Array.from(crypto.getRandomValues(new Uint32Array(4)), (value) => value.toString(16).padStart(8, '0')).join('')
  return { id, nodeId: '', role: 'prefill', engine: 'vllm', url: '', labels: '', bearerToken: '' }
}

/** Strip all Metrics credentials before persistence or export. */
export function sanitizedAIPerfDraft(draft: AIPerfDraft): AIPerfDraft {
  return { ...draft, nodes: draft.nodes.map((node) => {
    let url = node.url
    try {
      const parsed = new URL(url)
      if (parsed.username || parsed.password || parsed.search || parsed.hash) url = ''
    } catch {
      // Do not persist an incomplete URL that may already contain credentials.
      if (url.includes('@') || url.includes('?') || url.includes('#')) url = ''
    }
    return { ...node, url, bearerToken: '' }
  }) }
}

function parseList(value: string): number[] | null {
  const parts = value.split(',').map((part) => part.trim())
  if (!parts.length || parts.some((part) => !part || !Number.isFinite(Number(part)))) return null
  return parts.map(Number)
}

function parseLabels(value: string): Record<string, string> | null {
  const result: Record<string, string> = Object.create(null)
  if (!value.trim()) return result
  for (const item of value.split(',')) {
    const index = item.indexOf('=')
    const key = item.slice(0, index).trim()
    const label = item.slice(index + 1).trim()
    if (index < 1 || !/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(key) || !label || key in result ||
        ['__proto__', 'prototype', 'constructor'].includes(key)) return null
    result[key] = label
  }
  return result
}

export function validateAIPerfDraft(draft: AIPerfDraft): FieldErrors {
  const errors: FieldErrors = {}
  const required = FORM_MESSAGE_KEYS.required
  const invalid = 'tasks.aiperf.invalid'
  if (!draft.tokenizer.trim()) errors.tokenizer = required
  const ratios = parseList(draft.ratios)
  if (draft.runType === 'cache' && (!ratios || ratios.some((value) => value < 0 || value >= 100) || new Set(ratios).size !== ratios.length))
    errors.ratios = 'tasks.aiperf.invalidRatios'
  const integers = draft.runType === 'cache' ? ['inputTokens', 'requestCount', 'outputTokens', 'repetitions', 'pool'] as const : ['outputTokens'] as const
  for (const field of integers) {
    const value = draft[field].trim()
    if (!value || !Number.isSafeInteger(Number(value)) || Number(value) <= 0) errors[field] = invalid
  }
  if (draft.runType === 'cache' && (!draft.seed.trim() || !Number.isSafeInteger(Number(draft.seed)))) errors.seed = invalid
  const loads = parseList(draft.loads)
  if (!loads || loads.some((value) => value <= 0 || (draft.mode === 'closed_loop' && !Number.isSafeInteger(value))) ||
      new Set(loads).size !== loads.length) errors.loads = invalid
  if (draft.runType === 'single') {
    const prompts = draft.prompts.split(/\r?\n/).filter((value) => value.trim())
    if (!prompts.length || prompts.length > 10000 || new TextEncoder().encode(draft.prompts).length > 512 * 1024)
      errors.prompts = 'tasks.aiperf.invalidPrompts'
    if (loads?.length !== 1) errors.loads = 'tasks.aiperf.singleLoad'
  }
  if (draft.runType === 'cache' && ratios && ratios.some((value) => value > 0) && Number(draft.pool) > Number(draft.requestCount))
    errors.pool = 'tasks.aiperf.poolTooLarge'
  if (draft.runType === 'cache' && ratios && loads && ratios.length * loads.length * Number(draft.repetitions) > 10000)
    errors.repetitions = 'tasks.aiperf.matrixTooLarge'
  const interval = Number(draft.sampleInterval)
  if (!draft.sampleInterval.trim() || !Number.isFinite(interval) || interval < 0.001 || interval > 300)
    errors.sampleInterval = invalid
  if (draft.maxGap.trim() && (!Number.isFinite(Number(draft.maxGap)) || Number(draft.maxGap) < 1e-9 || Number(draft.maxGap) > 86400))
    errors.maxGap = invalid
  if (draft.nodes.length > 64) errors.nodes = invalid
  const ids = new Set<string>()
  const endpoints = new Set<string>()
  for (const node of draft.nodes) {
    const prefix = `node-${node.id}-`
    if (!node.nodeId.trim() || ids.has(node.nodeId.trim())) errors[`${prefix}nodeId`] = invalid
    ids.add(node.nodeId.trim())
    const urlError = validateApiUrl(node.url, true)
    if (urlError) errors[`${prefix}url`] = urlError
    else {
      const parsed = new URL(node.url.trim())
      if (parsed.search || parsed.hash) errors[`${prefix}url`] = 'tasks.invalidUrl'
      const normalized = `${parsed.origin}${parsed.pathname.replace(/\/$/, '') || '/metrics'}`
      if (endpoints.has(normalized)) errors[`${prefix}url`] = 'tasks.aiperf.duplicateNodeUrl'
      endpoints.add(normalized)
    }
    if (parseLabels(node.labels) === null) errors[`${prefix}labels`] = 'tasks.aiperf.invalidLabels'
    if (/[\r\n]/.test(node.bearerToken) || new TextEncoder().encode(node.bearerToken).length > 4000)
      errors[`${prefix}bearerToken`] = invalid
  }
  if (draft.required && (draft.nodes.length === 0 || (draft.deploymentMode === 'pd' &&
      (!draft.nodes.some((node) => node.role === 'prefill') || !draft.nodes.some((node) => node.role === 'decode')))))
    errors.nodes = 'tasks.aiperf.requiredRoles'
  return errors
}

/** Construct the exact backend envelope; target ratios and node values affect actual requests. */
export function buildAIPerfPayload(draft: AIPerfDraft, connection: ModelConnection): Record<string, unknown> {
  const targets = draft.nodes.map((node) => ({
    node_id: node.nodeId.trim(), role: node.role, engine: node.engine, url: node.url.trim(),
    labels: parseLabels(node.labels) ?? {},
    ...(node.bearerToken ? { headers: { Authorization: `Bearer ${node.bearerToken}` } } : {}),
  }))
  return {
    engine: 'aiperf',
    aiperf: {
      ...connectionPayload(connection, 'perf'), tokenizer_path: draft.tokenizer.trim(), stream: true,
      deployment_mode: draft.deploymentMode,
      ...(draft.runType === 'single' ? { input_prompts: draft.prompts.split(/\r?\n/).filter((value) => value.trim()) } : { cache_experiment: {
        target_ratios: (parseList(draft.ratios) ?? []).map((value) => value / 100),
        total_input_tokens: Number(draft.inputTokens), prefix_pool_size: Number(draft.pool), seed: Number(draft.seed),
      } }),
      load: {
        mode: draft.mode,
        points: (parseList(draft.loads) ?? []).map((value) => draft.mode === 'closed_loop' ? { concurrency: value } : { rate: value }),
        request_count: draft.runType === 'single' ? draft.prompts.split(/\r?\n/).filter((value) => value.trim()).length : Number(draft.requestCount),
        output_token_limit: Number(draft.outputTokens),
        repetitions: draft.runType === 'single' ? 1 : Number(draft.repetitions),
      },
      telemetry: {
        required: draft.required, sample_interval_seconds: Number(draft.sampleInterval), targets,
        ...(draft.maxGap.trim() ? { max_scrape_gap_seconds: Number(draft.maxGap) } : {}),
      },
    },
  }
}
