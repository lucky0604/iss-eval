import { FORM_MESSAGE_KEYS, validateApiUrl } from '@/domain/form/validation'
import {
  listErrorKey,
  matrixRunCount,
  parseIntegerList,
  parseRatioList,
  parseRateList,
  type MatrixScene,
  type PlanMatrixInput,
} from '@/domain/perf/planDraft'
import { connectionPayload, type FieldErrors, type ModelConnection } from './taskConfig'
import type { PlanRuntimeBinding } from '@/api/perfPlans'

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
  runType: 'matrix' | 'cache' | 'single'
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
  /** Matrix-only: ordinary performance or a cache-on/cache-off comparison. */
  matrixScene: MatrixScene
  matrixName: string
  matrixInputLengths: string
  matrixOutputLimits: string
  matrixOrder: 'seeded_shuffle' | 'load_then_input'
  matrixMaxModelLen: string
  matrixWarmupEnabled: boolean
  matrixWarmupRequests: string
  matrixWarmupInput: string
  matrixWarmupOutput: string
  matrixWarmupConcurrency: string
  /** Matrix-only: whether node metrics are configured at all. */
  matrixMonitoringEnabled: boolean
}

export const DEFAULT_AIPERF: AIPerfDraft = {
  runType: 'matrix', prompts: '', tokenizer: '', ratios: '0, 80, 90', inputTokens: '1024', requestCount: '10',
  outputTokens: '128', loads: '1', mode: 'closed_loop', deploymentMode: 'single', repetitions: '1', pool: '1',
  seed: '42', required: false, sampleInterval: '1', maxGap: '', nodes: [],
  matrixScene: 'performance', matrixName: '', matrixInputLengths: '1024, 8192', matrixOutputLimits: '128, 512',
  matrixOrder: 'seeded_shuffle', matrixMaxModelLen: '', matrixWarmupEnabled: true, matrixWarmupRequests: '2',
  matrixWarmupInput: '1024', matrixWarmupOutput: '128', matrixWarmupConcurrency: '1',
  matrixMonitoringEnabled: false,
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

/** List control parser shared by the matrix axes and the legacy load/ratio fields. */
function parseList(value: string): number[] | null {
  const text = value.trim()
  if (!text) return null
  const parts = text.split(/[,，;；\s]+/)
  if (parts.some((part) => !part || !Number.isFinite(Number(part)))) return null
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

/** Positive integer text field check shared by the draft validators. */
function positiveInteger(value: string): boolean {
  const text = value.trim()
  return Boolean(text) && Number.isSafeInteger(Number(text)) && Number(text) > 0
}

function integer(value: string): boolean {
  const text = value.trim()
  return Boolean(text) && Number.isSafeInteger(Number(text))
}

function matrixErrors(draft: AIPerfDraft): FieldErrors {
  const errors: FieldErrors = {}
  const invalid = 'tasks.aiperf.invalid'
  const inputs = parseIntegerList(draft.matrixInputLengths)
  if (!inputs.values) errors.matrixInputLengths = listErrorKey(inputs.error)
  const outputs = parseIntegerList(draft.matrixOutputLimits)
  if (!outputs.values) errors.matrixOutputLimits = listErrorKey(outputs.error)
  if (draft.matrixScene === 'cache_comparison') {
    const ratios = parseRatioList(draft.ratios)
    if (!ratios.values) errors.ratios = listErrorKey(ratios.error)
  }
  if (!positiveInteger(draft.matrixMaxModelLen)) errors.matrixMaxModelLen = invalid
  if (!integer(draft.seed)) errors.seed = invalid
  if (draft.matrixWarmupEnabled) {
    if (!positiveInteger(draft.matrixWarmupRequests)) errors.matrixWarmupRequests = invalid
    if (!positiveInteger(draft.matrixWarmupInput)) errors.matrixWarmupInput = invalid
    if (!positiveInteger(draft.matrixWarmupOutput)) errors.matrixWarmupOutput = invalid
    if (!positiveInteger(draft.matrixWarmupConcurrency)) errors.matrixWarmupConcurrency = invalid
  }
  const points = draft.mode === 'closed_loop' ? parseIntegerList(draft.loads, 32) : parseRateList(draft.loads, 32)
  if (points.values && inputs.values && outputs.values && positiveInteger(draft.repetitions)) {
    // The count mirrors the compiler's own axis product; the server re-checks it.

    const input = matrixInputFromDraft(draft)
    if (matrixRunCount(input) > 10000) errors.repetitions = 'tasks.aiperf.matrixTooLarge'
  }
  return errors
}

export function validateAIPerfDraft(draft: AIPerfDraft): FieldErrors {
  const errors: FieldErrors = {}
  const required = FORM_MESSAGE_KEYS.required
  const invalid = 'tasks.aiperf.invalid'
  if (!draft.tokenizer.trim()) errors.tokenizer = required
  const ratios = parseList(draft.ratios)
  if (draft.runType === 'cache' && (!ratios || ratios.some((value) => value < 0 || value >= 100) || new Set(ratios).size !== ratios.length))
    errors.ratios = 'tasks.aiperf.invalidRatios'
  const integers = draft.runType === 'cache' ? ['inputTokens', 'requestCount', 'outputTokens', 'repetitions', 'pool'] as const
    : draft.runType === 'matrix' ? ['requestCount', 'repetitions', 'pool'] as const : ['outputTokens'] as const
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
  if (draft.runType !== 'single' && ratios && ratios.some((value) => value > 0) && Number(draft.pool) > Number(draft.requestCount))
    errors.pool = 'tasks.aiperf.poolTooLarge'
  if (draft.runType === 'cache' && ratios && loads && ratios.length * loads.length * Number(draft.repetitions) > 10000)
    errors.repetitions = 'tasks.aiperf.matrixTooLarge'
  if (draft.runType === 'matrix') Object.assign(errors, matrixErrors(draft))
  // Monitoring is optional in the matrix: hidden controls must not block a
  // submission with errors the user cannot see.
  if (!monitoringEnabled(draft)) return errors
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

/** Whether the draft's monitoring inputs are part of the submitted configuration. */
export function monitoringEnabled(draft: AIPerfDraft): boolean {
  return draft.runType !== 'matrix' || draft.matrixMonitoringEnabled
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

/**
 * Typed matrix input from a draft that already passed {@link validateAIPerfDraft}.
 *
 * Invalid text falls back to an empty axis instead of `NaN`, so a stale build can
 * only produce a plan the server rejects — never one with a silent default.
 */
export function matrixInputFromDraft(draft: AIPerfDraft): PlanMatrixInput {
  return {
    scene: draft.matrixScene,
    name: draft.matrixName,
    tokenizer: draft.tokenizer,
    inputLengths: parseIntegerList(draft.matrixInputLengths).values ?? [],
    outputLimits: parseIntegerList(draft.matrixOutputLimits).values ?? [],
    ratios: parseRatioList(draft.ratios).values ?? [],
    pool: positiveInteger(draft.pool) ? Number(draft.pool) : 1,
    seed: integer(draft.seed) ? Number(draft.seed) : 0,
    mode: draft.mode,
    points: (draft.mode === 'closed_loop' ? parseIntegerList(draft.loads, 32) : parseRateList(draft.loads, 32)).values ?? [],
    requestCount: positiveInteger(draft.requestCount) ? Number(draft.requestCount) : 0,
    repetitions: positiveInteger(draft.repetitions) ? Number(draft.repetitions) : 0,
    order: draft.matrixOrder,
    deploymentMode: draft.deploymentMode === 'pd' ? 'disaggregated' : 'aggregated',
    maxModelLen: positiveInteger(draft.matrixMaxModelLen) ? Number(draft.matrixMaxModelLen) : 0,
    warmup: {
      enabled: draft.matrixWarmupEnabled,
      requestCount: positiveInteger(draft.matrixWarmupRequests) ? Number(draft.matrixWarmupRequests) : 0,
      inputTokens: positiveInteger(draft.matrixWarmupInput) ? Number(draft.matrixWarmupInput) : 0,
      outputTokenLimit: positiveInteger(draft.matrixWarmupOutput) ? Number(draft.matrixWarmupOutput) : 0,
      concurrency: positiveInteger(draft.matrixWarmupConcurrency) ? Number(draft.matrixWarmupConcurrency) : 0,
    },
  }
}

/** Metrics targets and the model key as worker-only runtime input, never plan fields. */
export function buildPlanRuntime(draft: AIPerfDraft, connection: ModelConnection): PlanRuntimeBinding {
  const runtime: PlanRuntimeBinding = {}
  if (connection.api === 'openai' && connection.apiKey) runtime.api_key = connection.apiKey
  const targets = draft.nodes.map((node) => ({
    node_id: node.nodeId.trim(), role: node.role, engine: node.engine, url: node.url.trim(),
    labels: parseLabels(node.labels) ?? {},
    ...(node.bearerToken ? { headers: { Authorization: `Bearer ${node.bearerToken}` } } : {}),
  }))
  if (monitoringEnabled(draft) && (targets.length || draft.required)) {
    runtime.telemetry = {
      required: draft.required,
      sample_interval_seconds: Number(draft.sampleInterval),
      targets,
      ...(draft.maxGap.trim() ? { max_scrape_gap_seconds: Number(draft.maxGap) } : {}),
    }
  }
  return runtime
}
