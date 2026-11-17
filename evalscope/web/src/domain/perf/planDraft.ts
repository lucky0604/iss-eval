/**
 * Pure construction of a schema 1.1 portable plan from the matrix form.
 *
 * The form's text controls are parsed here, never by `Number('1,2')`: a list is
 * split on English/Chinese commas, semicolons, ideographic spaces and newlines,
 * and every item is validated before a plan is built. The result is the exact
 * document JSON the server compiles, so the preview, the submission and the
 * exported file all describe the same matrix.
 */

import type {
  DeploymentFacts,
  PerfPlanDocumentV11,
  PlanLoadPointV11,
  TokenPrefixWorkload,
} from '../../api/generated/contracts'

/** Business purpose of the matrix; both stay on the managed token-prefix path. */
export type MatrixScene = 'performance' | 'cache_comparison'

/** Load pacing of the matrix. */
export type MatrixMode = 'closed_loop' | 'open_loop'

/** Client-side mirror of the compiler limit; the server stays authoritative. */
export const MATRIX_MAX_RUNS = 10000

/** Separators accepted in list controls (commas, semicolons, spaces, newlines). */
const LIST_SEPARATOR = /[,，;；\s]+/

export type ListError =
  | 'empty'
  | 'blankItem'
  | 'notInteger'
  | 'notPositive'
  | 'notFinite'
  | 'outOfRange'
  | 'duplicate'
  | 'tooMany'

export interface ParsedList {
  values?: number[]
  error?: ListError
}

function split(text: string, maxItems: number, emptyError: ListError = 'empty'): string[] | ParsedList {
  if (!text.trim()) return { error: emptyError }
  const items = text.trim().split(LIST_SEPARATOR)
  // A leading or trailing separator produces a blank item: dropping it silently
  // would turn a typo into a different matrix. Repeated separators in the middle
  // collapse into one under the separator regex, so `1024,,8192` is accepted.
  if (items.some((item) => !item)) return { error: 'blankItem' }
  if (items.length > maxItems) return { error: 'tooMany' }
  return items
}

/** Parse a list of positive integers (input lengths, output limits, concurrency). */
export function parseIntegerList(text: string, maxItems = 64): ParsedList {
  const items = split(text, maxItems)
  if (!Array.isArray(items)) return items
  const values: number[] = []
  for (const item of items) {
    const value = Number(item)
    if (!Number.isFinite(value) || !Number.isSafeInteger(value)) return { error: 'notInteger' }
    if (value <= 0) return { error: 'notPositive' }
    values.push(value)
  }
  if (new Set(values).size !== values.length) return { error: 'duplicate' }
  return { values }
}

/** Parse a list of positive finite rates; decimals are allowed, duplicates are not. */
export function parseRateList(text: string, maxItems = 32): ParsedList {
  const items = split(text, maxItems)
  if (!Array.isArray(items)) return items
  const values: number[] = []
  for (const item of items) {
    const value = Number(item)
    if (!Number.isFinite(value)) return { error: 'notFinite' }
    if (value <= 0) return { error: 'notPositive' }
    values.push(value)
  }
  if (new Set(values).size !== values.length) return { error: 'duplicate' }
  return { values }
}

/**
 * Parse target ratios in percent.
 *
 * Zero is a real point here (the cache-off baseline), so unlike the load list it
 * is accepted; 100 is not, because a fully shared prompt leaves no distinct
 * suffix and the document requires ``0 <= ratio < 1``.
 */
export function parseRatioList(text: string): ParsedList {
  const items = split(text, 16)
  if (!Array.isArray(items)) return items
  const values: number[] = []
  for (const item of items) {
    const value = Number(item)
    if (!Number.isFinite(value)) return { error: 'notFinite' }
    if (value < 0 || value >= 100) return { error: 'outOfRange' }
    values.push(value)
  }
  if (new Set(values).size !== values.length) return { error: 'duplicate' }
  return { values }
}

/** Everything a matrix draft contributes to the document, already typed. */
export interface PlanMatrixInput {
  scene: MatrixScene
  name: string
  tokenizer: string
  inputLengths: number[]
  outputLimits: number[]
  /** Percent values as entered; {@link buildPlanDocument} converts them to fractions. */
  ratios: number[]
  pool: number
  seed: number
  mode: MatrixMode
  points: number[]
  requestCount: number
  repetitions: number
  order: 'seeded_shuffle' | 'load_then_input'
  deploymentMode: 'aggregated' | 'disaggregated'
  maxModelLen: number
  warmup: {
    enabled: boolean
    requestCount: number
    inputTokens: number
    outputTokenLimit: number
    concurrency: number
  }
}

/** Ratios actually sent: an ordinary performance run measures the cache-off baseline. */
export function matrixRatios(input: PlanMatrixInput): number[] {
  if (input.scene === 'performance') return [0]
  return input.ratios.map((percent) => percentileToRatio(percent))
}

/**
 * Percent input to the document's fraction, rounded to six decimals.
 *
 * The rounding keeps 33 % from arriving as 0.33000000000000002, which would make
 * two matrices that print the same ratio compare as different documents.
 */
export function percentileToRatio(percent: number): number {
  return Math.round(percent * 10000) / 1000000
}

/** Run count of the compiled matrix, computed before anything is sent. */
export function matrixRunCount(input: PlanMatrixInput): number {
  return (
    input.inputLengths.length *
    input.outputLimits.length *
    matrixRatios(input).length *
    input.points.length *
    input.repetitions
  )
}

/** Document JSON of the matrix: schema 1.1, managed workload, one point per load value. */
export function buildPlanDocument(
  input: PlanMatrixInput,
  connection: { model: string; url: string },
): PerfPlanDocumentV11 {
  const deployment: DeploymentFacts = {
    mode: input.deploymentMode,
    declared_max_model_len: input.maxModelLen,
    evidence: 'declared',
  }
  const workload: TokenPrefixWorkload = {
    kind: 'token_prefix',
    input_lengths: input.inputLengths as [number, ...number[]],
    target_ratios: matrixRatios(input) as unknown as TokenPrefixWorkload['target_ratios'],
    prefix_pool_size: input.pool,
    seed: input.seed,
  }
  const points: PlanLoadPointV11[] = input.points.map((value) =>
    input.mode === 'closed_loop' ? { concurrency: value } : { rate: value },
  )
  return {
    kind: 'iss-eval.perf-plan',
    schema_version: '1.1',
    name: input.name.trim() || 'Performance matrix',
    intent: 'benchmark',
    connection: {
      model: connection.model.trim(),
      url: connection.url.trim(),
      tokenizer_path: input.tokenizer.trim(),
      protocol: 'openai_chat',
      streaming: true,
    },
    deployment,
    workload,
    load: {
      mode: input.mode,
      points: points as [PlanLoadPointV11, ...PlanLoadPointV11[]],
      request_count: input.requestCount,
      output_token_limits: input.outputLimits as [number, ...number[]],
      repetitions: input.repetitions,
    },
    warmup: {
      enabled: input.warmup.enabled,
      request_count: input.warmup.requestCount,
      input_tokens: input.warmup.inputTokens,
      output_token_limit: input.warmup.outputTokenLimit,
      concurrency: input.warmup.concurrency,
      failure_policy: 'abort',
    },
    // Prefix priming is derived, not asked: it runs for every non-zero ratio and
    // is skipped for the cache-off baseline.
    priming: { policy: 'prime_all_prefixes', timeout_seconds: 30 },
    execution:
      input.order === 'seeded_shuffle' ? { order: 'seeded_shuffle', seed: input.seed } : { order: 'load_then_input' },
    telemetry: { required: false, targets: [] },
  }
}

/** Text submitted to the server: the exact document, never a parse round trip. */
export function buildPlanText(input: PlanMatrixInput, connection: { model: string; url: string }): string {
  return JSON.stringify(buildPlanDocument(input, connection), null, 2)
}

/** Message key for a list parse failure. */
export function listErrorKey(error: ListError | undefined): string {
  if (error === 'blankItem') return 'tasks.aiperf.matrix.listBlank'
  if (error === 'notInteger') return 'tasks.aiperf.matrix.listNotInteger'
  if (error === 'notPositive') return 'tasks.aiperf.matrix.listNotPositive'
  if (error === 'notFinite') return 'tasks.aiperf.matrix.listNotFinite'
  if (error === 'outOfRange') return 'tasks.aiperf.invalidRatios'
  if (error === 'duplicate') return 'tasks.aiperf.matrix.listDuplicate'
  if (error === 'tooMany') return 'tasks.aiperf.matrix.listTooMany'
  return 'tasks.aiperf.matrix.listEmpty'
}
