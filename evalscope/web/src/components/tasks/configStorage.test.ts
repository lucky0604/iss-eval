import { beforeEach, describe, expect, it } from 'vitest'
import {
  CONFIG_STORAGE_KEY,
  LEGACY_AIPERF_STORAGE_KEY,
  LEGACY_CONFIG_STORAGE_KEY,
  LEGACY_V2_CONFIG_STORAGE_KEY,
  clearWorkspaceStorage,
  emptyWorkspace,
  exportLibrary,
  parseImport,
  readWorkspace,
  storageSnapshot,
  writeWorkspace,
  type Workspace,
} from './configStorage'
import { defaultAIPerfDraft, type AIPerfNodeDraft } from './aiperfDraft'
import { DEFAULT_CONNECTION, DEFAULT_EVAL, DEFAULT_PERF } from './taskConfig'

const node: AIPerfNodeDraft = {
  id: 'node-one', nodeId: 'prefill', role: 'prefill', engine: 'vllm',
  url: 'http://user:pass@10.0.0.1:9000/metrics', labels: 'model=alpha', bearerToken: 'node-secret',
}

function v2Workspace(aiperf: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  return {
    version: 2,
    connections: [],
    evalTemplates: [],
    perfTemplates: [],
    aiperfTemplates: [],
    connection: { ...DEFAULT_CONNECTION },
    eval: { ...DEFAULT_EVAL },
    perf: { ...DEFAULT_PERF },
    engine: 'aiperf',
    aiperf,
    selectedConnection: '',
    selectedEval: '',
    selectedPerf: '',
    selectedAIPerf: '',
    restoreSavedKey: false,
    ...extra,
  }
}

function readOk() {
  const result = readWorkspace()
  expect(result.ok).toBe(true)
  if (!result.ok) throw new Error(result.error)
  return result.value
}

beforeEach(() => {
  localStorage.clear()
})

describe('v2 migration', () => {
  it('maps a matrix draft, preferring the matrix axes over the shared token fields', () => {
    localStorage.setItem(LEGACY_V2_CONFIG_STORAGE_KEY, JSON.stringify(v2Workspace({
      runType: 'matrix',
      inputTokens: '4096',
      outputTokens: '256',
      matrixName: 'nightly',
      matrixInputLengths: '1024, 2048',
      matrixOutputLimits: '128, 256',
      matrixMaxModelLen: '32768',
      matrixScene: 'cache_comparison',
      matrixWarmupEnabled: true,
      loads: '1, 2',
      requestCount: '20',
      mode: 'closed_loop',
    })))
    const aiperf = readOk().aiperf
    expect(aiperf.inputSource).toBe('generated')
    expect(aiperf.name).toBe('nightly')
    expect(aiperf.generated.inputLengths).toBe('1024, 2048')
    expect(aiperf.generated.outputLimits).toBe('128, 256')
    expect(aiperf.generated.maxModelLen).toBe('32768')
    expect(aiperf.generated.comparePrefix).toBe(true)
    expect(aiperf.generated.closedLoads).toBe('1, 2')
    expect(aiperf.generated.openRates).toBe('')
    expect(aiperf.generated.requestCount).toBe('20')
    expect(aiperf.generated.warmup.enabled).toBe(true)
  })

  it('falls back to the shared token fields when a matrix draft never had matrix axes', () => {
    localStorage.setItem(LEGACY_V2_CONFIG_STORAGE_KEY, JSON.stringify(v2Workspace({
      runType: 'matrix', inputTokens: '4096', outputTokens: '256', mode: 'open_loop', loads: '8',
    })))
    const aiperf = readOk().aiperf
    expect(aiperf.generated.inputLengths).toBe('4096')
    expect(aiperf.generated.outputLimits).toBe('256')
    expect(aiperf.generated.openRates).toBe('8')
    expect(aiperf.generated.closedLoads).toBe('1')
  })

  it('keeps a cache experiment on its own authoritative sizes and adds no traffic', () => {
    localStorage.setItem(LEGACY_V2_CONFIG_STORAGE_KEY, JSON.stringify(v2Workspace({
      runType: 'cache',
      inputTokens: '2048',
      outputTokens: '256',
      matrixInputLengths: '1024, 8192',
      matrixOutputLimits: '128, 512',
      matrixMaxModelLen: '32768',
      matrixWarmupEnabled: true,
      matrixMonitoringEnabled: false,
      ratios: '0, 80',
      nodes: [node],
    })))
    const aiperf = readOk().aiperf
    expect(aiperf.inputSource).toBe('generated')
    expect(aiperf.generated.inputLengths).toBe('2048')
    expect(aiperf.generated.outputLimits).toBe('256')
    expect(aiperf.generated.comparePrefix).toBe(true)
    expect(aiperf.generated.ratios).toBe('0, 80')
    // No declared context existed for the cache experiment, and warmup was never
    // part of it: neither may be invented from the leftover matrix defaults.
    expect(aiperf.generated.maxModelLen).toBe('')
    expect(aiperf.generated.warmup.enabled).toBe(false)
    // The experiment's own metrics nodes still enable monitoring.
    expect(aiperf.monitoring.enabled).toBe(true)
    expect(aiperf.monitoring.nodes).toHaveLength(1)
  })

  it('carries a single-run draft into the real-prompt branch with its text verbatim', () => {
    const text = 'first question \n\n  spaced second question\n'
    localStorage.setItem(LEGACY_V2_CONFIG_STORAGE_KEY, JSON.stringify(v2Workspace({
      runType: 'single', prompts: text, outputTokens: '64', loads: '2', mode: 'closed_loop', tokenizer: 'tok',
    })))
    const aiperf = readOk().aiperf
    expect(aiperf.inputSource).toBe('prompts')
    expect(aiperf.tokenizer).toBe('tok')
    expect(aiperf.prompts).toEqual({
      text, outputLimit: '64', mode: 'closed_loop', concurrency: '2', rate: '',
    })
  })

  it('moves an open-loop single draft to the rate control instead of concurrency', () => {
    localStorage.setItem(LEGACY_V2_CONFIG_STORAGE_KEY, JSON.stringify(v2Workspace({
      runType: 'single', prompts: 'p', loads: '0.5', mode: 'open_loop',
    })))
    const { prompts: draft } = readOk().aiperf
    expect(draft.rate).toBe('0.5')
    expect(draft.concurrency).toBe('1')
  })

  it('reads the discriminator before defaults, so a cache draft with untouched matrix axes is not read as a matrix', () => {
    // V2_AIPERF_DEFAULTS includes matrixInputLengths; a cache draft never sets runType
    // to matrix, and a missing discriminator must still mean matrix, not cache.
    localStorage.setItem(LEGACY_V2_CONFIG_STORAGE_KEY, JSON.stringify(v2Workspace({ runType: 'cache' })))
    expect(readOk().aiperf.generated.comparePrefix).toBe(true)
    localStorage.setItem(LEGACY_V2_CONFIG_STORAGE_KEY, JSON.stringify(v2Workspace({})))
    expect(readOk().aiperf.generated.comparePrefix).toBe(false)
  })
})

describe('legacy v1 and standalone drafts', () => {
  it('builds the AIPerf branch from the standalone cache draft when v1 has none', () => {
    localStorage.setItem(LEGACY_CONFIG_STORAGE_KEY, JSON.stringify({
      version: 1,
      connections: [],
      evalTemplates: [],
      perfTemplates: [],
      connection: { ...DEFAULT_CONNECTION },
      eval: { ...DEFAULT_EVAL },
      perf: { ...DEFAULT_PERF },
      selectedConnection: '',
      selectedEval: '',
      selectedPerf: '',
      restoreSavedKey: false,
    }))
    localStorage.setItem(LEGACY_AIPERF_STORAGE_KEY, JSON.stringify({
      tokenizer: 'tok', inputTokens: '512', outputTokens: '64', loads: '2', ratios: '0, 50',
      prefill: 'http://p:9000/metrics', decode: 'http://d:9000/metrics', server: 'sglang',
    }))
    const workspace = readOk()
    expect(workspace.aiperfTemplates).toEqual([])
    expect(workspace.aiperf.generated.inputLengths).toBe('512')
    expect(workspace.aiperf.generated.comparePrefix).toBe(true)
    expect(workspace.aiperf.deploymentMode).toBe('pd')
    expect(workspace.aiperf.monitoring.enabled).toBe(true)
    expect(workspace.aiperf.monitoring.nodes.map((entry) => entry.nodeId)).toEqual(['prefill', 'decode'])
  })

  it('reads a standalone draft on its own', () => {
    localStorage.setItem(LEGACY_AIPERF_STORAGE_KEY, JSON.stringify({
      tokenizer: 'tok', inputTokens: '1024', outputTokens: '128', loads: '1',
    }))
    expect(readOk().aiperf.generated.inputLengths).toBe('1024')
  })
})

describe('v3 workspace', () => {
  it('round-trips both input branches and the monitoring switch', () => {
    const workspace: Workspace = {
      ...emptyWorkspace(),
      aiperf: {
        ...defaultAIPerfDraft(),
        inputSource: 'prompts',
        tokenizer: 'tok',
        prompts: { text: 'line one\n\nline two', outputLimit: '32', mode: 'open_loop', concurrency: '1', rate: '2' },
        generated: { ...defaultAIPerfDraft().generated, inputLengths: '2048', comparePrefix: true },
      },
      engine: 'aiperf',
      selectedAIPerf: 'tpl',
    }
    expect(writeWorkspace(workspace).ok).toBe(true)
    const restored = readOk()
    expect(restored.aiperf.inputSource).toBe('prompts')
    expect(restored.aiperf.prompts.text).toBe('line one\n\nline two')
    // Switching back keeps the generated branch intact.
    expect(restored.aiperf.generated.inputLengths).toBe('2048')
    expect(restored.aiperf.generated.comparePrefix).toBe(true)
    expect(restored.engine).toBe('aiperf')
  })

  it('rejects a damaged v3 value instead of silently falling back to v2', () => {
    localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify({ version: 3, connections: [] }))
    localStorage.setItem(LEGACY_V2_CONFIG_STORAGE_KEY, JSON.stringify(v2Workspace({ runType: 'single', prompts: 'p' })))
    expect(readWorkspace()).toEqual({ ok: false, error: 'invalidStorage' })
  })

  it('rejects unknown AIPerf fields', () => {
    const workspace = emptyWorkspace()
    expect(writeWorkspace(workspace).ok).toBe(true)
    const raw = JSON.parse(localStorage.getItem(CONFIG_STORAGE_KEY)!)
    raw.aiperf.generated.surprise = 'x'
    localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(raw))
    expect(readWorkspace()).toEqual({ ok: false, error: 'invalidStorage' })
  })
})

describe('credentials', () => {
  it('strips node and model credentials from storage, then restores only a chosen remembered key', () => {
    const workspace: Workspace = {
      ...emptyWorkspace(),
      connection: { model: 'm', url: 'http://host:1234/v1', apiKey: 'sk-model-secret', alias: '', api: 'openai' },
      connections: [{
        id: 'c1', name: 'prod', rememberKey: true,
        value: { model: 'm', url: 'http://host:1234/v1', apiKey: 'sk-model-secret', alias: '', api: 'openai' },
      }],
      selectedConnection: 'c1',
      aiperf: {
        ...defaultAIPerfDraft(),
        monitoring: { enabled: true, required: false, sampleInterval: '1', maxGap: '', nodes: [node] },
      },
      aiperfTemplates: [{
        id: 't1', name: 'cache', value: {
          ...defaultAIPerfDraft(),
          monitoring: { enabled: true, required: false, sampleInterval: '1', maxGap: '', nodes: [node] },
        },
      }],
    }
    expect(writeWorkspace(workspace).ok).toBe(true)
    const text = localStorage.getItem(CONFIG_STORAGE_KEY)!
    expect(text).not.toContain('node-secret')
    expect(text).not.toContain('user:pass')
    expect(text).not.toContain('"connection":{"model":"m","url":"http://host:1234/v1","apiKey":"sk-model-secret"')

    const restored = readOk()
    expect(restored.connection.apiKey).toBe('sk-model-secret')
    expect(restored.aiperf.monitoring.nodes[0].bearerToken).toBe('')
    expect(restored.aiperf.monitoring.nodes[0].url).toBe('')
    expect(restored.aiperfTemplates[0].value.monitoring.nodes[0].bearerToken).toBe('')
  })

  it('does not restore a remembered key that was edited or points elsewhere', () => {
    const workspace: Workspace = {
      ...emptyWorkspace(),
      connection: { model: 'm', url: 'http://other:1234/v1', apiKey: 'sk-model-secret', alias: '', api: 'openai' },
      connections: [{
        id: 'c1', name: 'prod', rememberKey: true,
        value: { model: 'm', url: 'http://host:1234/v1', apiKey: 'sk-model-secret', alias: '', api: 'openai' },
      }],
      selectedConnection: 'c1',
    }
    expect(writeWorkspace(workspace).ok).toBe(true)
    expect(readOk().connection.apiKey).toBe('')
  })

  it('exports without credentials unless the user opts in', () => {
    const library = storageSnapshot({
      ...emptyWorkspace(),
      connections: [{
        id: 'c1', name: 'prod', rememberKey: true,
        value: { model: 'm', url: 'http://host:1234/v1', apiKey: 'sk-model-secret', alias: '', api: 'openai' },
      }],
      aiperfTemplates: [{
        id: 't1', name: 'cache', value: {
          ...defaultAIPerfDraft(),
          monitoring: { enabled: true, required: false, sampleInterval: '1', maxGap: '', nodes: [node] },
        },
      }],
    })
    const plain = exportLibrary(library, false)
    expect(plain).not.toContain('sk-model-secret')
    expect(plain).not.toContain('node-secret')
    const withKeys = exportLibrary(library, true)
    expect(withKeys).toContain('sk-model-secret')
    expect(withKeys).not.toContain('node-secret')
  })
})

describe('import and clear', () => {
  const base = { version: 3, connections: [], evalTemplates: [], perfTemplates: [], aiperfTemplates: [] }

  it('accepts a structurally valid template that still needs execution values', () => {
    const library = parseImport(JSON.stringify({
      ...base,
      aiperfTemplates: [{ id: 'tpl', name: 'draft', value: { schemaVersion: 3 } }],
    }))
    expect(library.aiperfTemplates[0].value.tokenizer).toBe('')
    expect(library.aiperfTemplates[0].value.generated.inputLengths).toBe('1024, 8192')
  })

  it('rejects incomplete connections, evaluation and performance templates', () => {
    expect(() => parseImport(JSON.stringify({
      ...base,
      connections: [{ id: 'c', name: 'c', rememberKey: false, value: { ...DEFAULT_CONNECTION } }],
    }))).toThrow()
    expect(() => parseImport(JSON.stringify({
      ...base,
      evalTemplates: [{ id: 'e', name: 'e', value: { ...DEFAULT_EVAL, sandboxEngine: 'podman' } }],
    }))).toThrow()
    expect(() => parseImport(JSON.stringify({
      ...base,
      perfTemplates: [{ id: 'p', name: 'p', value: { ...DEFAULT_PERF, parallel: 2 } }],
    }))).toThrow()
  })

  it('clears every managed key, so an older draft cannot reappear', () => {
    localStorage.setItem(LEGACY_V2_CONFIG_STORAGE_KEY, JSON.stringify(v2Workspace({ runType: 'single', prompts: 'p' })))
    localStorage.setItem(LEGACY_CONFIG_STORAGE_KEY, '{"version":1}')
    localStorage.setItem(LEGACY_AIPERF_STORAGE_KEY, '{"inputTokens":"1024"}')
    expect(clearWorkspaceStorage()).toEqual({ ok: true, value: null })
    expect(localStorage.getItem(LEGACY_V2_CONFIG_STORAGE_KEY)).toBeNull()
    expect(localStorage.getItem(LEGACY_CONFIG_STORAGE_KEY)).toBeNull()
    expect(localStorage.getItem(LEGACY_AIPERF_STORAGE_KEY)).toBeNull()
    expect(readOk().aiperf).toEqual(defaultAIPerfDraft())
  })
})
