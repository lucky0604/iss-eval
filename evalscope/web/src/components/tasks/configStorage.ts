import {
  DEFAULT_CONNECTION,
  DEFAULT_EVAL,
  DEFAULT_PERF,
  endpointOrigin,
  validateConnection,
  validateEvalDraft,
  validatePerfDraft,
  type EvalDraft,
  type ModelConnection,
  type PerfDraft,
} from './taskConfig'
import {
  aiperfTaskNeedsCompletion,
  defaultAIPerfDraft,
  sanitizedAIPerfTaskDraft,
  type AIPerfNodeDraft,
  type AIPerfTaskDraft,
} from './aiperfDraft'

export const CONFIG_STORAGE_KEY = 'evalscope-task-workspace-v3'
export const LEGACY_V2_CONFIG_STORAGE_KEY = 'evalscope-task-workspace-v2'
export const LEGACY_CONFIG_STORAGE_KEY = 'evalscope-task-workspace-v1'
export const LEGACY_AIPERF_STORAGE_KEY = 'evalscope-cache-experiment-draft-v1'
export const MAX_IMPORT_BYTES = 1024 * 1024
export interface Preset<T> {
  id: string
  name: string
  value: T
}
export interface ConnectionPreset extends Preset<ModelConnection> {
  rememberKey: boolean
}
export interface ConfigLibrary {
  version: 3
  connections: ConnectionPreset[]
  evalTemplates: Preset<EvalDraft>[]
  perfTemplates: Preset<PerfDraft>[]
  aiperfTemplates: Preset<AIPerfTaskDraft>[]
}
export interface Workspace extends ConfigLibrary {
  connection: ModelConnection
  eval: EvalDraft
  perf: PerfDraft
  engine: 'evalscope' | 'aiperf'
  aiperf: AIPerfTaskDraft
  selectedConnection: string
  selectedEval: string
  selectedPerf: string
  selectedAIPerf: string
  restoreSavedKey: boolean
}
export type StorageResult<T> = { ok: true; value: T } | { ok: false; error: 'invalidStorage' | 'storageFailed' | 'workspaceTooLarge' }

export function emptyWorkspace(): Workspace {
  return {
    version: 3,
    connections: [],
    evalTemplates: [],
    perfTemplates: [],
    aiperfTemplates: [],
    connection: { ...DEFAULT_CONNECTION },
    eval: { ...DEFAULT_EVAL },
    perf: { ...DEFAULT_PERF },
    engine: 'evalscope',
    aiperf: defaultAIPerfDraft(),
    selectedConnection: '',
    selectedEval: '',
    selectedPerf: '',
    selectedAIPerf: '',
    restoreSavedKey: false,
  }
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid object')
  return value as Record<string, unknown>
}

function exactKeys(value: Record<string, unknown>, keys: string[]): void {
  if (Object.keys(value).some((key) => !keys.includes(key))) throw new Error('Unknown field')
}

function parseJson(text: string): unknown {
  return JSON.parse(text, (key, value: unknown) => {
    if (['__proto__', 'prototype', 'constructor'].includes(key)) throw new Error('Unsafe field')
    return value
  })
}

/** Local storage and imported JSON are untrusted; reconstruct only known fields. */
function parseDraft<T extends object>(value: unknown, defaults: T): T {
  const raw = object(value)
  exactKeys(raw, Object.keys(defaults))
  const result = { ...defaults }
  for (const key of Object.keys(defaults) as (keyof T)[]) {
    if (!(key in raw)) continue
    if (typeof raw[String(key)] !== typeof defaults[key]) throw new Error('Invalid field')
    if (typeof raw[String(key)] === 'string' && String(raw[String(key)]).length > MAX_IMPORT_BYTES)
      throw new Error('Field too large')
    result[key] = raw[String(key)] as T[keyof T]
  }
  return result
}

function readString(value: unknown, fallback: string): string {
  if (value === undefined) return fallback
  if (typeof value !== 'string' || value.length > MAX_IMPORT_BYTES) throw new Error('Invalid string')
  return value
}

function readBoolean(value: unknown, fallback: boolean): boolean {
  if (value === undefined) return fallback
  if (typeof value !== 'boolean') throw new Error('Invalid boolean')
  return value
}

function readEnum<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  if (value === undefined) return fallback
  if (typeof value !== 'string' || !allowed.includes(value as T)) throw new Error('Invalid enum')
  return value as T
}

function parseConnection(value: unknown): ModelConnection {
  const connection = parseDraft(value, DEFAULT_CONNECTION)
  if (!['openai', 'local'].includes(connection.api)) throw new Error('Invalid protocol')
  return connection
}

function parseEval(value: unknown): EvalDraft {
  const draft = parseDraft(value, DEFAULT_EVAL)
  if (!['docker', 'volcengine'].includes(draft.sandboxEngine)) throw new Error('Invalid engine')
  return draft
}

const NODE_KEYS = ['id', 'nodeId', 'role', 'engine', 'url', 'labels', 'bearerToken']

/** Metrics nodes are shared by every persisted branch; credentials never survive parsing. */
function parseNodes(value: unknown): AIPerfNodeDraft[] {
  const input = value ?? []
  if (!Array.isArray(input) || input.length > 64) throw new Error('Invalid metrics nodes')
  const ids = new Set<string>()
  const defaults: AIPerfNodeDraft = {
    id: '', nodeId: '', role: 'prefill', engine: 'vllm', url: '', labels: '', bearerToken: '',
  }
  return input.map((entry: unknown) => {
    const raw = object(entry)
    exactKeys(raw, NODE_KEYS)
    for (const key of NODE_KEYS) {
      if (raw[key] !== undefined && typeof raw[key] !== 'string') throw new Error('Invalid metrics node')
    }
    const node = parseDraft(raw, defaults)
    if (!/^[a-zA-Z0-9_-]{1,64}$/.test(node.id) || ids.has(node.id) ||
        !['prefill', 'decode'].includes(node.role) || !['vllm', 'sglang'].includes(node.engine))
      throw new Error('Invalid metrics node')
    ids.add(node.id)
    return { ...node, bearerToken: '' }
  })
}

const GENERATED_KEYS = ['inputLengths', 'outputLimits', 'mode', 'closedLoads', 'openRates', 'requestCount',
  'comparePrefix', 'ratios', 'pool', 'seed', 'repetitions', 'order', 'maxModelLen', 'warmup']
const WARMUP_KEYS = ['enabled', 'requests', 'inputTokens', 'outputTokens', 'concurrency']
const PROMPTS_KEYS = ['text', 'outputLimit', 'mode', 'concurrency', 'rate']
const MONITORING_KEYS = ['enabled', 'required', 'sampleInterval', 'maxGap', 'nodes']
const TASK_DRAFT_KEYS = ['schemaVersion', 'inputSource', 'tokenizer', 'name', 'deploymentMode', 'generated',
  'prompts', 'monitoring']

/** Structural parse of a schemaVersion 3 draft; execution values are validated later. */
function parseAIPerfTask(value: unknown): AIPerfTaskDraft {
  const raw = object(value)
  exactKeys(raw, TASK_DRAFT_KEYS)
  if (raw.schemaVersion !== 3) throw new Error('Unsupported AIPerf draft version')
  const draft = defaultAIPerfDraft()
  draft.inputSource = readEnum(raw.inputSource, ['generated', 'prompts'], 'generated')
  draft.tokenizer = readString(raw.tokenizer, '')
  draft.name = readString(raw.name, '')
  draft.deploymentMode = readEnum(raw.deploymentMode, ['single', 'pd'], 'single')

  const generated = object(raw.generated ?? {})
  exactKeys(generated, GENERATED_KEYS)
  draft.generated.inputLengths = readString(generated.inputLengths, draft.generated.inputLengths)
  draft.generated.outputLimits = readString(generated.outputLimits, draft.generated.outputLimits)
  draft.generated.mode = readEnum(generated.mode, ['closed_loop', 'open_loop'], 'closed_loop')
  draft.generated.closedLoads = readString(generated.closedLoads, draft.generated.closedLoads)
  draft.generated.openRates = readString(generated.openRates, draft.generated.openRates)
  draft.generated.requestCount = readString(generated.requestCount, draft.generated.requestCount)
  draft.generated.comparePrefix = readBoolean(generated.comparePrefix, false)
  draft.generated.ratios = readString(generated.ratios, draft.generated.ratios)
  draft.generated.pool = readString(generated.pool, draft.generated.pool)
  draft.generated.seed = readString(generated.seed, draft.generated.seed)
  draft.generated.repetitions = readString(generated.repetitions, draft.generated.repetitions)
  draft.generated.order = readEnum(generated.order, ['seeded_shuffle', 'load_then_input'], 'seeded_shuffle')
  draft.generated.maxModelLen = readString(generated.maxModelLen, draft.generated.maxModelLen)
  const warmup = object(generated.warmup ?? {})
  exactKeys(warmup, WARMUP_KEYS)
  draft.generated.warmup = {
    enabled: readBoolean(warmup.enabled, draft.generated.warmup.enabled),
    requests: readString(warmup.requests, draft.generated.warmup.requests),
    inputTokens: readString(warmup.inputTokens, draft.generated.warmup.inputTokens),
    outputTokens: readString(warmup.outputTokens, draft.generated.warmup.outputTokens),
    concurrency: readString(warmup.concurrency, draft.generated.warmup.concurrency),
  }

  const prompts = object(raw.prompts ?? {})
  exactKeys(prompts, PROMPTS_KEYS)
  draft.prompts = {
    text: readString(prompts.text, ''),
    outputLimit: readString(prompts.outputLimit, draft.prompts.outputLimit),
    mode: readEnum(prompts.mode, ['closed_loop', 'open_loop'], 'closed_loop'),
    concurrency: readString(prompts.concurrency, draft.prompts.concurrency),
    rate: readString(prompts.rate, draft.prompts.rate),
  }

  const monitoring = object(raw.monitoring ?? {})
  exactKeys(monitoring, MONITORING_KEYS)
  draft.monitoring = {
    enabled: readBoolean(monitoring.enabled, false),
    required: readBoolean(monitoring.required, false),
    sampleInterval: readString(monitoring.sampleInterval, draft.monitoring.sampleInterval),
    maxGap: readString(monitoring.maxGap, draft.monitoring.maxGap),
    nodes: parseNodes(monitoring.nodes),
  }
  return sanitizedAIPerfTaskDraft(draft)
}

// ------------------------------------------------------------------ //
// Migration from the workspace v2 / standalone cache draft             //
// ------------------------------------------------------------------ //

const V2_AIPERF_DEFAULTS = {
  runType: 'matrix', prompts: '', tokenizer: '', ratios: '0, 80, 90', inputTokens: '1024', requestCount: '10',
  outputTokens: '128', loads: '1', mode: 'closed_loop', deploymentMode: 'single', repetitions: '1', pool: '1',
  seed: '42', required: false, sampleInterval: '1', maxGap: '', nodes: [] as AIPerfNodeDraft[],
  matrixScene: 'performance', matrixName: '', matrixInputLengths: '1024, 8192', matrixOutputLimits: '128, 512',
  matrixOrder: 'seeded_shuffle', matrixMaxModelLen: '', matrixWarmupEnabled: true, matrixWarmupRequests: '2',
  matrixWarmupInput: '1024', matrixWarmupOutput: '128', matrixWarmupConcurrency: '1', matrixMonitoringEnabled: false,
}

function numericText(value: string): boolean {
  return /^\d+$/.test(value.trim()) && Number(value) > 0
}

function migrateV2AIPerf(value: unknown): AIPerfTaskDraft {
  const raw = object(value)
  exactKeys(raw, Object.keys(V2_AIPERF_DEFAULTS))
  // Read the discriminator from the raw object, before any defaults are filled:
  // a cache draft also carries untouched matrix default lengths.
  const runType = readEnum(raw.runType, ['matrix', 'cache', 'single'], 'matrix')
  const draft = defaultAIPerfDraft()
  const mode = readEnum(raw.mode, ['closed_loop', 'open_loop'], 'closed_loop')
  const deploymentMode = readEnum(raw.deploymentMode, ['single', 'pd'], 'single')
  const nodes = parseNodes(raw.nodes)
  const required = readBoolean(raw.required, false)
  const sampleInterval = readString(raw.sampleInterval, '1')
  const maxGap = readString(raw.maxGap, '')
  const loads = readString(raw.loads, '1')
  const tokenizer = readString(raw.tokenizer, '')
  draft.tokenizer = tokenizer
  draft.deploymentMode = deploymentMode
  draft.monitoring = {
    enabled: nodes.length > 0 || required,
    required,
    sampleInterval,
    maxGap,
    nodes,
  }

  if (runType === 'matrix') {
    const matrixInputLengths = 'matrixInputLengths' in raw
      ? readString(raw.matrixInputLengths, V2_AIPERF_DEFAULTS.matrixInputLengths)
      : numericText(readString(raw.inputTokens, '1024'))
        ? readString(raw.inputTokens, '1024')
        : V2_AIPERF_DEFAULTS.matrixInputLengths
    const matrixOutputLimits = 'matrixOutputLimits' in raw
      ? readString(raw.matrixOutputLimits, V2_AIPERF_DEFAULTS.matrixOutputLimits)
      : numericText(readString(raw.outputTokens, '128'))
        ? readString(raw.outputTokens, '128')
        : V2_AIPERF_DEFAULTS.matrixOutputLimits
    draft.inputSource = 'generated'
    draft.name = readString(raw.matrixName, '')
    draft.generated = {
      inputLengths: matrixInputLengths,
      outputLimits: matrixOutputLimits,
      mode,
      closedLoads: mode === 'closed_loop' ? loads : '1',
      openRates: mode === 'open_loop' ? loads : '',
      requestCount: readString(raw.requestCount, '10'),
      comparePrefix: readEnum(raw.matrixScene, ['performance', 'cache_comparison'], 'performance') === 'cache_comparison',
      ratios: readString(raw.ratios, '0, 80, 90'),
      pool: readString(raw.pool, '1'),
      seed: readString(raw.seed, '42'),
      repetitions: readString(raw.repetitions, '1'),
      order: readEnum(raw.matrixOrder, ['seeded_shuffle', 'load_then_input'], 'seeded_shuffle'),
      maxModelLen: readString(raw.matrixMaxModelLen, ''),
      warmup: {
        enabled: readBoolean(raw.matrixWarmupEnabled, true),
        requests: readString(raw.matrixWarmupRequests, '2'),
        inputTokens: readString(raw.matrixWarmupInput, '1024'),
        outputTokens: readString(raw.matrixWarmupOutput, '128'),
        concurrency: readString(raw.matrixWarmupConcurrency, '1'),
      },
    }
    // The old matrix only exposed monitoring through its own switch; leftover
    // nodes must not silently turn it on.
    draft.monitoring.enabled = readBoolean(raw.matrixMonitoringEnabled, false)
    return sanitizedAIPerfTaskDraft(draft)
  }

  if (runType === 'cache') {
    draft.inputSource = 'generated'
    draft.generated = {
      ...draft.generated,
      // The cache experiment's own sizes are authoritative even when matrix
      // default lengths are also present in the same object.
      inputLengths: readString(raw.inputTokens, '1024'),
      outputLimits: readString(raw.outputTokens, '128'),
      mode,
      closedLoads: mode === 'closed_loop' ? loads : '1',
      openRates: mode === 'open_loop' ? loads : '',
      requestCount: readString(raw.requestCount, '10'),
      comparePrefix: true,
      ratios: readString(raw.ratios, '0, 80, 90'),
      pool: readString(raw.pool, '1'),
      seed: readString(raw.seed, '42'),
      repetitions: readString(raw.repetitions, '1'),
      order: 'seeded_shuffle',
      // The cache experiment never declared a context limit; a residual matrix
      // value is not evidence for this workload.
      maxModelLen: '',
      // The old cache experiment configured no ordinary warmup. Keeping it off
      // avoids adding traffic the user never asked for.
      warmup: { ...draft.generated.warmup, enabled: false },
    }
    draft.monitoring.enabled = nodes.length > 0 || required
    return sanitizedAIPerfTaskDraft(draft)
  }

  draft.inputSource = 'prompts'
  draft.prompts = {
    text: readString(raw.prompts, ''),
    outputLimit: readString(raw.outputTokens, '128'),
    mode,
    concurrency: mode === 'closed_loop' ? loads : '1',
    rate: mode === 'open_loop' ? loads : '',
  }
  draft.monitoring.enabled = nodes.length > 0 || required
  return sanitizedAIPerfTaskDraft(draft)
}

const LEGACY_AIPERF_DEFAULTS = {
  tokenizer: '', ratios: '0, 80, 90', inputTokens: '1024', requestCount: '10', outputTokens: '128',
  loads: '1', mode: 'closed_loop', repetitions: '1', pool: '1', prefill: '', decode: '', server: 'vllm',
}

/** Standalone cache draft from before the workspace existed. */
function parseLegacyAIPerf(value: unknown): AIPerfTaskDraft {
  const old = parseDraft(value, LEGACY_AIPERF_DEFAULTS)
  if (!['closed_loop', 'open_loop'].includes(old.mode) || !['vllm', 'sglang'].includes(old.server))
    throw new Error('Invalid legacy AIPerf draft')
  const nodes: AIPerfNodeDraft[] = []
  for (const role of ['prefill', 'decode'] as const) {
    if (old[role].trim()) nodes.push({ id: `legacy-${role}`, nodeId: role, role,
                                      engine: old.server as AIPerfNodeDraft['engine'], url: old[role],
                                      labels: '', bearerToken: '' })
  }
  const draft = defaultAIPerfDraft()
  draft.inputSource = 'generated'
  draft.tokenizer = old.tokenizer
  draft.deploymentMode = nodes.length ? 'pd' : 'single'
  draft.generated = {
    ...draft.generated,
    inputLengths: old.inputTokens,
    outputLimits: old.outputTokens,
    mode: old.mode as 'closed_loop' | 'open_loop',
    closedLoads: old.mode === 'closed_loop' ? old.loads : '1',
    openRates: old.mode === 'open_loop' ? old.loads : '',
    requestCount: old.requestCount,
    comparePrefix: true,
    ratios: old.ratios,
    pool: old.pool,
    repetitions: old.repetitions,
    seed: draft.generated.seed,
    order: 'seeded_shuffle',
    // No real declared context limit in the legacy draft: leave it empty and
    // mark the template as needing completion instead of guessing a value.
    maxModelLen: '',
    warmup: { ...draft.generated.warmup, enabled: false },
  }
  draft.monitoring = { ...draft.monitoring, enabled: nodes.length > 0, nodes }
  return sanitizedAIPerfTaskDraft(draft)
}

function parsePresets<T>(value: unknown, parse: (value: unknown) => T, allowCredentialChoice = false): Preset<T>[] {
  if (!Array.isArray(value) || value.length > 200) throw new Error('Invalid presets')
  const ids = new Set<string>()
  return value.map((entry: unknown) => {
    const raw = object(entry)
    exactKeys(raw, allowCredentialChoice ? ['id', 'name', 'value', 'rememberKey'] : ['id', 'name', 'value'])
    if (typeof raw.id !== 'string' || !raw.id || ids.has(raw.id)) throw new Error('Invalid id')
    if (typeof raw.name !== 'string' || !raw.name.trim() || raw.name.length > 120) throw new Error('Invalid name')
    ids.add(raw.id)
    return { id: raw.id, name: raw.name.trim(), value: parse(raw.value) }
  })
}

function parseAIPerfPreset(value: unknown, version: 2 | 3): AIPerfTaskDraft {
  return version === 2 ? migrateV2AIPerf(value) : parseAIPerfTask(value)
}

function parseLibrary(raw: Record<string, unknown>): ConfigLibrary {
  if (raw.version !== 1 && raw.version !== 2 && raw.version !== 3) throw new Error('Unsupported version')
  const version = raw.version
  const connections = parsePresets(raw.connections, parseConnection, true).map((entry, index) => {
    const source = object((raw.connections as unknown[])[index])
    if (typeof source.rememberKey !== 'boolean') throw new Error('Invalid credential choice')
    return {
      ...entry,
      rememberKey: source.rememberKey,
      value: { ...entry.value, apiKey: source.rememberKey ? entry.value.apiKey : '' },
    }
  })
  return {
    version: 3,
    connections,
    evalTemplates: parsePresets(raw.evalTemplates, parseEval),
    perfTemplates: parsePresets(raw.perfTemplates, (value) => parseDraft(value, DEFAULT_PERF)),
    aiperfTemplates: version === 1 ? []
      : parsePresets(raw.aiperfTemplates, (value) => parseAIPerfPreset(value, version)),
  }
}

const LIBRARY_KEYS = ['version', 'connections', 'evalTemplates', 'perfTemplates', 'aiperfTemplates']
const LIBRARY_KEYS_V1 = ['version', 'connections', 'evalTemplates', 'perfTemplates']

export function parseImport(text: string): ConfigLibrary {
  if (new TextEncoder().encode(text).length > MAX_IMPORT_BYTES) throw new Error('Import too large')
  const raw = object(parseJson(text))
  exactKeys(raw, raw.version === 1 ? LIBRARY_KEYS_V1 : LIBRARY_KEYS)
  const library = parseLibrary(raw)
  if (
    library.connections.some((entry) => Object.keys(validateConnection(entry.value)).length > 0) ||
    library.evalTemplates.some((entry) => Object.keys(validateEvalDraft(entry.value)).length > 0) ||
    library.perfTemplates.some((entry) => Object.keys(validatePerfDraft(entry.value)).length > 0)
  ) {
    throw new Error('Invalid configuration values')
  }
  // AIPerf templates are allowed to arrive incomplete: structure, enums and
  // credentials are enforced above, while execution values are checked only
  // when the user previews or starts a run.
  return library
}

/** Whether an AIPerf template still needs execution-required values after migration. */
export { aiperfTaskNeedsCompletion }

function parseWorkspace(raw: Record<string, unknown>, aiperf: AIPerfTaskDraft): Workspace {
  const library = parseLibrary(raw)
  const state: Workspace = {
    ...library,
    connection: parseConnection(raw.connection),
    eval: parseEval(raw.eval),
    perf: parseDraft(raw.perf, DEFAULT_PERF),
    engine: 'evalscope',
    aiperf,
    selectedConnection: '',
    selectedEval: '',
    selectedPerf: '',
    selectedAIPerf: '',
    restoreSavedKey: false,
  }
  for (const key of ['selectedConnection', 'selectedEval', 'selectedPerf'] as const) {
    if (typeof raw[key] !== 'string') throw new Error('Invalid selection')
    state[key] = raw[key]
  }
  if (typeof raw.restoreSavedKey !== 'boolean') throw new Error('Invalid credential restore')
  state.connection.apiKey = ''
  const selected = state.connections.find((entry) => entry.id === state.selectedConnection)
  if (
    raw.restoreSavedKey &&
    selected?.rememberKey &&
    selected.value.api === state.connection.api &&
    endpointOrigin(selected.value.url) === endpointOrigin(state.connection.url)
  ) {
    state.connection.apiKey = selected.value.apiKey
  }
  return state
}

export function readWorkspace(): StorageResult<Workspace> {
  try {
    const current = localStorage.getItem(CONFIG_STORAGE_KEY)
    const legacyV2 = current ? null : localStorage.getItem(LEGACY_V2_CONFIG_STORAGE_KEY)
    const legacyV1 = current || legacyV2 ? null : localStorage.getItem(LEGACY_CONFIG_STORAGE_KEY)
    const legacyAIPerf = current || legacyV2 ? null : localStorage.getItem(LEGACY_AIPERF_STORAGE_KEY)
    if (!current && !legacyV2 && !legacyV1 && !legacyAIPerf) return { ok: true, value: emptyWorkspace() }
    try {
      if ([current, legacyV2, legacyV1, legacyAIPerf].some((text) =>
        text && new TextEncoder().encode(text).length > MAX_IMPORT_BYTES))
        throw new Error('Storage too large')
      if (!current && !legacyV2 && !legacyV1) {
        return { ok: true, value: { ...emptyWorkspace(), aiperf: parseLegacyAIPerf(parseJson(legacyAIPerf!)) } }
      }
      // The newest existing key is authoritative. A damaged v3 value is an
      // explicit error, never a silent fallback that overwrites it from v2.
      const text = (current ?? legacyV2 ?? legacyV1)!
      const raw = object(parseJson(text))
      const version = current ? 3 : legacyV2 ? 2 : 1
      if (raw.version !== version) throw new Error('Invalid workspace version')
      if (version === 3) {
        exactKeys(raw, Object.keys(emptyWorkspace()))
        const aiperf = parseAIPerfTask(raw.aiperf)
        if (raw.engine !== 'evalscope' && raw.engine !== 'aiperf') throw new Error('Invalid executor')
        if (typeof raw.selectedAIPerf !== 'string') throw new Error('Invalid selection')
        const state = parseWorkspace(raw, aiperf)
        state.engine = raw.engine
        state.selectedAIPerf = raw.selectedAIPerf
        return { ok: true, value: state }
      }
      const v2Keys = ['version', 'connections', 'evalTemplates', 'perfTemplates', 'aiperfTemplates', 'connection',
        'eval', 'perf', 'engine', 'aiperf', 'selectedConnection', 'selectedEval', 'selectedPerf', 'selectedAIPerf',
        'restoreSavedKey']
      const v1Keys = ['version', 'connections', 'evalTemplates', 'perfTemplates', 'connection', 'eval', 'perf',
        'selectedConnection', 'selectedEval', 'selectedPerf', 'restoreSavedKey']
      exactKeys(raw, version === 2 ? v2Keys : v1Keys)
      const aiperf = version === 2
        ? migrateV2AIPerf(raw.aiperf)
        : legacyAIPerf
          ? parseLegacyAIPerf(parseJson(legacyAIPerf))
          : defaultAIPerfDraft()
      const state = parseWorkspace(raw, aiperf)
      if (version === 2) {
        if (raw.engine !== 'evalscope' && raw.engine !== 'aiperf') throw new Error('Invalid executor')
        if (typeof raw.selectedAIPerf !== 'string') throw new Error('Invalid selection')
        state.engine = raw.engine
        state.selectedAIPerf = raw.selectedAIPerf
      }
      return { ok: true, value: state }
    } catch {
      return { ok: false, error: 'invalidStorage' }
    }
  } catch {
    return { ok: false, error: 'storageFailed' }
  }
}

export function storageSnapshot(workspace: Workspace): Workspace {
  const selected = workspace.connections.find((entry) => entry.id === workspace.selectedConnection)
  return {
    ...workspace,
    connections: workspace.connections.map((entry) => ({
      ...entry,
      value: { ...entry.value, apiKey: entry.rememberKey ? entry.value.apiKey : '' },
    })),
    connection: { ...workspace.connection, apiKey: '' },
    aiperf: sanitizedAIPerfTaskDraft(workspace.aiperf),
    aiperfTemplates: workspace.aiperfTemplates.map((entry) => ({
      ...entry, value: sanitizedAIPerfTaskDraft(entry.value),
    })),
    restoreSavedKey: Boolean(
      selected?.rememberKey &&
        selected.value.apiKey &&
        selected.value.apiKey === workspace.connection.apiKey &&
        selected.value.api === workspace.connection.api &&
        endpointOrigin(selected.value.url) === endpointOrigin(workspace.connection.url),
    ),
  }
}

export function writeWorkspace(workspace: Workspace): StorageResult<null> {
  try {
    if (
      [workspace.connections, workspace.evalTemplates, workspace.perfTemplates, workspace.aiperfTemplates].some((entries) => entries.length > 200)
    ) {
      return { ok: false, error: 'invalidStorage' }
    }
    const text = JSON.stringify(storageSnapshot(workspace))
    if (new TextEncoder().encode(text).length > MAX_IMPORT_BYTES) return { ok: false, error: 'workspaceTooLarge' }
    localStorage.setItem(CONFIG_STORAGE_KEY, text)
    return { ok: true, value: null }
  } catch {
    return { ok: false, error: 'storageFailed' }
  }
}

export function clearWorkspaceStorage(): StorageResult<null> {
  try {
    const result = writeWorkspace(emptyWorkspace())
    if (!result.ok) return result
    // Remove every managed workspace key so an older draft cannot reappear on
    // the next read; the legacy standalone key is managed as well.
    localStorage.removeItem(LEGACY_V2_CONFIG_STORAGE_KEY)
    localStorage.removeItem(LEGACY_CONFIG_STORAGE_KEY)
    localStorage.removeItem(LEGACY_AIPERF_STORAGE_KEY)
    return result
  } catch {
    return { ok: false, error: 'storageFailed' }
  }
}

export function exportLibrary(workspace: ConfigLibrary, includeKeys: boolean): string {
  return JSON.stringify(
    {
      version: 3,
      connections: workspace.connections.map((entry) => ({
        ...entry,
        rememberKey: includeKeys && entry.rememberKey,
        value: { ...entry.value, apiKey: includeKeys && entry.rememberKey ? entry.value.apiKey : '' },
      })),
      evalTemplates: workspace.evalTemplates,
      perfTemplates: workspace.perfTemplates,
      aiperfTemplates: workspace.aiperfTemplates.map((entry) => ({
        ...entry, value: sanitizedAIPerfTaskDraft(entry.value),
      })),
    },
    null,
    2,
  )
}

function uniqueName(name: string, existing: string[]): string {
  const base = name.slice(0, 110)
  let result = name
  let index = 2
  while (existing.includes(result)) result = `${base} (${index++})`
  return result
}

export function createPresetId(): string {
  return Array.from(crypto.getRandomValues(new Uint32Array(4)), (part) => part.toString(16).padStart(8, '0')).join('')
}

export function savePreset<T extends object>(
  entries: Preset<T>[],
  selectedId: string,
  name: string,
  value: T,
  asNew: boolean,
): { entries: Preset<T>[]; selectedId: string } {
  const id = !asNew && entries.some((entry) => entry.id === selectedId) ? selectedId : createPresetId()
  const entry = { id, name, value: structuredClone(value) }
  return { entries: [...entries.filter((item) => item.id !== id), entry], selectedId: id }
}

export function mergeLibrary(workspace: Workspace, imported: ConfigLibrary): Workspace {
  function append<T>(existing: Preset<T>[], incoming: Preset<T>[]): Preset<T>[] {
    const result = [...existing]
    for (const entry of incoming) {
      result.push({
        ...entry,
        id: createPresetId(),
        name: uniqueName(
          entry.name,
          result.map((item) => item.name),
        ),
      })
    }
    return result
  }
  const connections = [...workspace.connections]
  for (const entry of imported.connections) {
    connections.push({
      ...entry,
      id: createPresetId(),
      name: uniqueName(
        entry.name,
        connections.map((item) => item.name),
      ),
    })
  }
  return {
    ...workspace,
    connections,
    evalTemplates: append(workspace.evalTemplates, imported.evalTemplates),
    perfTemplates: append(workspace.perfTemplates, imported.perfTemplates),
    aiperfTemplates: append(workspace.aiperfTemplates, imported.aiperfTemplates),
  }
}
