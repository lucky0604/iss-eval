import { FORM_MESSAGE_KEYS, validateApiUrl } from '@/domain/form/validation'
import {
  listErrorKey,
  matrixRunCount,
  parseIntegerList,
  parseRatioList,
  parseRateList,
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

export type AIPerfInputSource = 'generated' | 'prompts'
export type AIPerfLoadMode = 'closed_loop' | 'open_loop'

/** Generated-input branch: token-generated managed workload, one tier or a matrix. */
export interface AIPerfGeneratedDraft {
  inputLengths: string
  outputLimits: string
  mode: AIPerfLoadMode
  /** Values used while {@link AIPerfGeneratedDraft.mode} is `closed_loop`. */
  closedLoads: string
  /** Values used while {@link AIPerfGeneratedDraft.mode} is `open_loop`; empty until filled. */
  openRates: string
  requestCount: string
  /** Optional prefix-reuse comparison; not a separate execution mode. */
  comparePrefix: boolean
  ratios: string
  pool: string
  seed: string
  repetitions: string
  order: 'seeded_shuffle' | 'load_then_input'
  maxModelLen: string
  warmup: {
    enabled: boolean
    requests: string
    inputTokens: string
    outputTokens: string
    concurrency: string
  }
}

/** Real-prompt branch: one request per non-blank line, one load point, one output limit. */
export interface AIPerfPromptsDraft {
  text: string
  outputLimit: string
  mode: AIPerfLoadMode
  concurrency: string
  rate: string
}

/** Optional server metrics, shared by both input sources. */
export interface AIPerfMonitoringDraft {
  enabled: boolean
  required: boolean
  sampleInterval: string
  maxGap: string
  nodes: AIPerfNodeDraft[]
}

/**
 * Frontend workspace draft (schemaVersion 3).
 *
 * Inactive branches keep their edited values so switching the input source never
 * resets work or leaks one branch's units into the other. Only the active branch
 * plus the explicit monitoring switch take part in validation, budgets and
 * submission.
 */
export interface AIPerfTaskDraft {
  schemaVersion: 3
  inputSource: AIPerfInputSource
  tokenizer: string
  name: string
  deploymentMode: 'single' | 'pd'
  generated: AIPerfGeneratedDraft
  prompts: AIPerfPromptsDraft
  monitoring: AIPerfMonitoringDraft
}

/** Limits shared with the single-run API; the server re-checks both. */
export const PROMPT_MAX_LINES = 10000
export const PROMPT_MAX_BYTES = 512 * 1024

export function defaultAIPerfDraft(): AIPerfTaskDraft {
  return {
    schemaVersion: 3,
    inputSource: 'generated',
    tokenizer: '',
    name: '',
    deploymentMode: 'single',
    generated: {
      inputLengths: '1024, 8192',
      outputLimits: '128, 512',
      mode: 'closed_loop',
      closedLoads: '1',
      openRates: '',
      requestCount: '10',
      comparePrefix: false,
      ratios: '0, 80, 90',
      pool: '1',
      seed: '42',
      repetitions: '1',
      order: 'seeded_shuffle',
      maxModelLen: '',
      warmup: { enabled: true, requests: '2', inputTokens: '1024', outputTokens: '128', concurrency: '1' },
    },
    prompts: { text: '', outputLimit: '128', mode: 'closed_loop', concurrency: '1', rate: '' },
    monitoring: { enabled: false, required: false, sampleInterval: '1', maxGap: '', nodes: [] },
  }
}

export function newAIPerfNode(): AIPerfNodeDraft {
  const id = Array.from(crypto.getRandomValues(new Uint32Array(4)), (value) => value.toString(16).padStart(8, '0')).join('')
  return { id, nodeId: '', role: 'prefill', engine: 'vllm', url: '', labels: '', bearerToken: '' }
}

/**
 * Split prompt text into the exact request strings.
 *
 * A line is a request; blank lines (whitespace only) are dropped, but a kept line
 * is never trimmed — spaces and Unicode are the user's payload, not formatting.
 */
export function promptLines(text: string): string[] {
  return text.split(/\r?\n/).filter((line) => line.trim())
}

/** Strip all node credentials before persistence or export, in every branch. */
export function sanitizedAIPerfTaskDraft(draft: AIPerfTaskDraft): AIPerfTaskDraft {
  return {
    ...draft,
    monitoring: {
      ...draft.monitoring,
      nodes: draft.monitoring.nodes.map((node) => {
        let url = node.url
        try {
          const parsed = new URL(url)
          if (parsed.username || parsed.password || parsed.search || parsed.hash) url = ''
        } catch {
          // Do not persist an incomplete URL that may already contain credentials.
          if (url.includes('@') || url.includes('?') || url.includes('#')) url = ''
        }
        return { ...node, url, bearerToken: '' }
      }),
    },
  }
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

function positiveInteger(value: string): boolean {
  const text = value.trim()
  return Boolean(text) && Number.isSafeInteger(Number(text)) && Number(text) > 0
}

function integer(value: string): boolean {
  const text = value.trim()
  return Boolean(text) && Number.isSafeInteger(Number(text))
}

/** A single positive integer control; lists are an explicit error, never truncated. */
function singleInteger(value: string): number | null {
  const text = value.trim()
  if (!text || /[,，;；\s]/.test(text) || !Number.isSafeInteger(Number(text)) || Number(text) <= 0) return null
  return Number(text)
}

/** A single positive rate control; lists are an explicit error, never truncated. */
function singleRate(value: string): number | null {
  const text = value.trim()
  if (!text || /[,，;；\s]/.test(text)) return null
  const rate = Number(text)
  return Number.isFinite(rate) && rate > 0 ? rate : null
}

function activePoints(generated: AIPerfGeneratedDraft) {
  return generated.mode === 'closed_loop'
    ? parseIntegerList(generated.closedLoads, 32)
    : parseRateList(generated.openRates, 32)
}

/** Ratios that would actually be sent; compare off always measures the 0 % baseline. */
function effectiveRatios(generated: AIPerfGeneratedDraft): number[] | null {
  if (!generated.comparePrefix) return [0]
  return parseRatioList(generated.ratios).values ?? null
}

function generatedErrors(draft: AIPerfTaskDraft): FieldErrors {
  const errors: FieldErrors = {}
  const generated = draft.generated
  const invalid = 'tasks.aiperf.invalid'
  const inputs = parseIntegerList(generated.inputLengths, 64)
  if (!inputs.values) errors.inputLengths = listErrorKey(inputs.error)
  const outputs = parseIntegerList(generated.outputLimits, 64)
  if (!outputs.values) errors.outputLimits = listErrorKey(outputs.error)
  const points = activePoints(generated)
  if (!points.values) errors[generated.mode === 'closed_loop' ? 'closedLoads' : 'openRates'] = listErrorKey(points.error)
  if (!positiveInteger(generated.requestCount)) errors.requestCount = invalid
  if (!positiveInteger(generated.repetitions)) errors.repetitions = invalid
  if (!integer(generated.seed)) errors.seed = invalid
  if (!positiveInteger(generated.maxModelLen)) errors.maxModelLen = invalid
  else if (inputs.values && outputs.values &&
           Math.max(...inputs.values) + Math.max(...outputs.values) > Number(generated.maxModelLen))
    errors.maxModelLen = 'tasks.aiperf.contextTooSmall'
  if (generated.comparePrefix) {
    const ratios = parseRatioList(generated.ratios)
    if (!ratios.values) errors.ratios = listErrorKey(ratios.error)
    // The shared-prefix pool only constrains non-zero ratios; with a 0 %-only list
    // it is irrelevant and must not block the run.
    else if (ratios.values.some((value) => value > 0)) {
      if (!positiveInteger(generated.pool)) errors.pool = invalid
      else if (positiveInteger(generated.requestCount) && Number(generated.pool) > Number(generated.requestCount))
        errors.pool = 'tasks.aiperf.poolTooLarge'
    }
  }
  if (generated.warmup.enabled) {
    if (!positiveInteger(generated.warmup.requests)) errors.warmupRequests = invalid
    if (!positiveInteger(generated.warmup.inputTokens)) errors.warmupInput = invalid
    if (!positiveInteger(generated.warmup.outputTokens)) errors.warmupOutput = invalid
    if (!positiveInteger(generated.warmup.concurrency)) errors.warmupConcurrency = invalid
  }
  if (inputs.values && outputs.values && points.values && positiveInteger(generated.repetitions) &&
      positiveInteger(generated.requestCount)) {
    const ratios = effectiveRatios(generated)
    if (ratios && matrixRunCount(generatedPlanInput(draft)) > 10000) errors.repetitions = 'tasks.aiperf.matrixTooLarge'
  }
  return errors
}

function promptErrors(draft: AIPerfTaskDraft): FieldErrors {
  const errors: FieldErrors = {}
  const prompts = draft.prompts
  const lines = promptLines(prompts.text)
  if (!lines.length || lines.length > PROMPT_MAX_LINES ||
      new TextEncoder().encode(prompts.text).length > PROMPT_MAX_BYTES)
    errors.prompts = 'tasks.aiperf.invalidPrompts'
  if (!positiveInteger(prompts.outputLimit)) errors.promptOutputLimit = 'tasks.aiperf.singleOutputLimit'
  if (prompts.mode === 'closed_loop') {
    const concurrency = singleInteger(prompts.concurrency)
    if (concurrency === null) errors.promptConcurrency = 'tasks.aiperf.singleLoad'
    else if (lines.length > 0 && lines.length < concurrency) errors.promptConcurrency = 'tasks.aiperf.promptConcurrencyTooHigh'
  } else if (singleRate(prompts.rate) === null) errors.promptRate = 'tasks.aiperf.singleLoad'
  return errors
}

function monitoringErrors(draft: AIPerfTaskDraft): FieldErrors {
  const errors: FieldErrors = {}
  if (!draft.monitoring.enabled) return errors
  const invalid = 'tasks.aiperf.invalid'
  const monitoring = draft.monitoring
  const interval = Number(monitoring.sampleInterval)
  if (!monitoring.sampleInterval.trim() || !Number.isFinite(interval) || interval < 0.001 || interval > 300)
    errors.sampleInterval = invalid
  if (monitoring.maxGap.trim() &&
      (!Number.isFinite(Number(monitoring.maxGap)) || Number(monitoring.maxGap) < 1e-9 || Number(monitoring.maxGap) > 86400))
    errors.maxGap = invalid
  if (monitoring.nodes.length > 64) errors.nodes = invalid
  const ids = new Set<string>()
  const endpoints = new Set<string>()
  for (const node of monitoring.nodes) {
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
  if (monitoring.required && (monitoring.nodes.length === 0 || (draft.deploymentMode === 'pd' &&
      (!monitoring.nodes.some((node) => node.role === 'prefill') ||
       !monitoring.nodes.some((node) => node.role === 'decode')))))
    errors.nodes = 'tasks.aiperf.requiredRoles'
  return errors
}

/**
 * Validate the active input source plus the explicit monitoring switch.
 *
 * Inactive branch values (hidden ratios/pool, the other load mode, disabled
 * warmup or monitoring sub-fields) never participate: a stale value in a hidden
 * control cannot block the task the user is actually assembling.
 */
export function validateAIPerfTask(draft: AIPerfTaskDraft): FieldErrors {
  const errors: FieldErrors = {}
  if (!draft.tokenizer.trim()) errors.tokenizer = FORM_MESSAGE_KEYS.required
  Object.assign(errors, draft.inputSource === 'generated' ? generatedErrors(draft) : promptErrors(draft))
  Object.assign(errors, monitoringErrors(draft))
  return errors
}

/** Whether a stored template still needs execution-required values (migrated drafts). */
export function aiperfTaskNeedsCompletion(draft: AIPerfTaskDraft): boolean {
  return Object.keys(validateAIPerfTask(draft)).length > 0
}

function planInteger(value: string, fallback: number): number {
  return positiveInteger(value) ? Number(value) : fallback
}

/**
 * Typed matrix input for the generated branch, using only effective values.
 *
 * Invalid text can only produce an empty axis, never a silent default; the
 * server remains authoritative for anything the browser accepts.
 */
export function generatedPlanInput(draft: AIPerfTaskDraft): PlanMatrixInput {
  const generated = draft.generated
  const points = activePoints(generated)
  const ratios = effectiveRatios(generated) ?? []
  return {
    scene: generated.comparePrefix ? 'cache_comparison' : 'performance',
    name: draft.name,
    tokenizer: draft.tokenizer,
    inputLengths: parseIntegerList(generated.inputLengths, 64).values ?? [],
    outputLimits: parseIntegerList(generated.outputLimits, 64).values ?? [],
    ratios: generated.comparePrefix ? ratios : [],
    // Off, or a 0 %-only comparison: the plan still needs a positive pool but no
    // pool ≤ N constraint applies.
    pool: generated.comparePrefix && positiveInteger(generated.pool) ? Number(generated.pool) : 1,
    seed: integer(generated.seed) ? Number(generated.seed) : 0,
    mode: generated.mode,
    points: points.values ?? [],
    requestCount: planInteger(generated.requestCount, 0),
    repetitions: planInteger(generated.repetitions, 0),
    order: generated.order,
    deploymentMode: draft.deploymentMode === 'pd' ? 'disaggregated' : 'aggregated',
    maxModelLen: planInteger(generated.maxModelLen, 0),
    // A disabled warmup uses the document's own public defaults so the plan stays
    // schema-valid; the compiler sends nothing because enabled is false.
    warmup: {
      enabled: generated.warmup.enabled,
      requestCount: planInteger(generated.warmup.requests, 3),
      inputTokens: planInteger(generated.warmup.inputTokens, 16000),
      outputTokenLimit: planInteger(generated.warmup.outputTokens, 1000),
      concurrency: planInteger(generated.warmup.concurrency, 1),
    },
  }
}

/** Metrics targets for the runtime binding; credentials never enter a plan. */
export function monitoringTargets(draft: AIPerfTaskDraft) {
  return draft.monitoring.nodes.map((node) => ({
    node_id: node.nodeId.trim(),
    role: node.role,
    engine: node.engine,
    url: node.url.trim(),
    labels: parseLabels(node.labels) ?? {},
    ...(node.bearerToken ? { headers: { Authorization: `Bearer ${node.bearerToken}` } } : {}),
  }))
}

/** Telemetry body for runtime-only submission or an explicit endpoint probe. */
export function telemetryConfiguration(draft: AIPerfTaskDraft) {
  const interval = Number(draft.monitoring.sampleInterval)
  return {
    required: draft.monitoring.required,
    sample_interval_seconds: Number.isFinite(interval) && interval > 0 ? interval : 1,
    targets: monitoringTargets(draft),
    ...(draft.monitoring.maxGap.trim() && Number.isFinite(Number(draft.monitoring.maxGap))
      ? { max_scrape_gap_seconds: Number(draft.monitoring.maxGap) } : {}),
  }
}

/** Metrics targets and the model key as worker-only runtime input, never plan fields. */
export function buildPlanRuntime(draft: AIPerfTaskDraft, connection: ModelConnection): PlanRuntimeBinding {
  const runtime: PlanRuntimeBinding = {}
  if (connection.api === 'openai' && connection.apiKey) runtime.api_key = connection.apiKey
  if (draft.monitoring.enabled) {
    const targets = monitoringTargets(draft)
    if (targets.length || draft.monitoring.required) runtime.telemetry = telemetryConfiguration(draft)
  }
  return runtime
}

/**
 * Legacy single-run envelope for the real-prompt branch.
 *
 * The prompt strings are sent verbatim (no trim/truncate/copy), N is derived from
 * the non-blank lines, and repetitions stay at 1. This path never uses the cache
 * experiment block or a generated plan.
 */
export function buildAIPerfPromptPayload(draft: AIPerfTaskDraft, connection: ModelConnection): Record<string, unknown> {
  const lines = promptLines(draft.prompts.text)
  const point = draft.prompts.mode === 'closed_loop'
    ? { concurrency: singleInteger(draft.prompts.concurrency) ?? 0 }
    : { rate: singleRate(draft.prompts.rate) ?? 0 }
  return {
    engine: 'aiperf',
    aiperf: {
      ...connectionPayload(connection, 'perf'),
      tokenizer_path: draft.tokenizer.trim(),
      stream: true,
      deployment_mode: draft.deploymentMode,
      input_prompts: lines,
      load: {
        mode: draft.prompts.mode,
        points: [point],
        request_count: lines.length,
        output_token_limit: planInteger(draft.prompts.outputLimit, 0),
        repetitions: 1,
      },
      ...(draft.monitoring.enabled ? { telemetry: telemetryConfiguration(draft) } : {}),
    },
  }
}
