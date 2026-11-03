import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  CONFIG_STORAGE_KEY,
  LEGACY_CONFIG_STORAGE_KEY,
  LEGACY_AIPERF_STORAGE_KEY,
  emptyWorkspace,
  exportLibrary,
  mergeLibrary,
  parseImport,
  readWorkspace,
  savePreset,
  storageSnapshot,
  writeWorkspace,
} from './configStorage'
import {
  DEFAULT_CONNECTION,
  DEFAULT_EVAL,
  editConnection,
  connectionPayload,
  validateEvalDraft,
  validatePerfDraft,
  DEFAULT_PERF,
} from './taskConfig'
import { DEFAULT_AIPERF } from './aiperfDraft'

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

function savedWorkspace() {
  const state = emptyWorkspace()
  state.connection = {
    ...DEFAULT_CONNECTION,
    model: 'same-model',
    url: 'http://server-a:8000/v1',
    apiKey: 'fake-secret',
  }
  state.connections = [{ id: 'a', name: 'Server A', value: { ...state.connection }, rememberKey: true }]
  state.selectedConnection = 'a'
  state.eval.datasets = 'gsm8k'
  return state
}

describe('Browser configuration storage', () => {
  it('returns independent defaults when empty', () => {
    const result = readWorkspace()
    expect(result).toEqual({ ok: true, value: emptyWorkspace() })
  })
  it('migrates version-1 native and AIPerf drafts without overwriting the originals', () => {
    const legacy = savedWorkspace() as unknown as Record<string, unknown>
    legacy.version = 1
    for (const key of ['engine', 'aiperf', 'aiperfTemplates', 'selectedAIPerf']) delete legacy[key]
    const oldText = JSON.stringify(legacy)
    const oldDraft = JSON.stringify({ tokenizer: '/tokenizer', ratios: '0, 80, 90', inputTokens: '20',
      requestCount: '6', outputTokens: '2', loads: '1', mode: 'closed_loop', repetitions: '1', pool: '1',
      prefill: 'http://p:9000/metrics', decode: 'http://d:9000/metrics', server: 'vllm' })
    localStorage.setItem(LEGACY_CONFIG_STORAGE_KEY, oldText)
    localStorage.setItem(LEGACY_AIPERF_STORAGE_KEY, oldDraft)
    const result = readWorkspace()
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.value.engine).toBe('evalscope')
    expect(result.value.perf).toEqual(DEFAULT_PERF)
    expect(result.value.aiperf.nodes.map((node) => node.role)).toEqual(['prefill', 'decode'])
    expect(writeWorkspace(result.value).ok).toBe(true)
    expect(localStorage.getItem(LEGACY_CONFIG_STORAGE_KEY)).toBe(oldText)
    expect(localStorage.getItem(LEGACY_AIPERF_STORAGE_KEY)).toBe(oldDraft)
    expect(JSON.parse(localStorage.getItem(CONFIG_STORAGE_KEY)!).version).toBe(2)
  })
  it('does not save or export metrics tokens or credentials embedded in URLs', () => {
    const state = savedWorkspace()
    state.engine = 'aiperf'
    state.aiperf = { ...DEFAULT_AIPERF, tokenizer: '/tokenizer', nodes: [
      { id: 'p', nodeId: 'p', role: 'prefill', engine: 'vllm', url: 'http://p:9000/metrics', labels: 'model=a', bearerToken: 'node-secret' },
      { id: 'd', nodeId: 'd', role: 'decode', engine: 'sglang', url: 'http://user:pass@d/metrics?key=other-secret', labels: '', bearerToken: 'other-token' },
    ] }
    state.aiperfTemplates = [{ id: 'preset', name: 'AIPerf', value: state.aiperf }]
    expect(writeWorkspace(state).ok).toBe(true)
    const raw = localStorage.getItem(CONFIG_STORAGE_KEY)!
    expect(raw).not.toContain('node-secret')
    expect(raw).not.toContain('other-token')
    expect(raw).not.toContain('other-secret')
    expect(raw).not.toContain('user:pass')
    expect(exportLibrary(state, true)).not.toContain('node-secret')
    expect(exportLibrary(state, true)).not.toContain('other-secret')
    expect(readWorkspace()).toMatchObject({ ok: true, value: { engine: 'aiperf', aiperf: {
      nodes: [{ url: 'http://p:9000/metrics', bearerToken: '' }, { url: '', bearerToken: '' }],
    } } })
  })
  it('fails closed when the legacy AIPerf draft is damaged', () => {
    localStorage.setItem(LEGACY_AIPERF_STORAGE_KEY, '{broken')
    expect(readWorkspace()).toEqual({ ok: false, error: 'invalidStorage' })
    expect(localStorage.getItem(LEGACY_AIPERF_STORAGE_KEY)).toBe('{broken')
    expect(localStorage.getItem(CONFIG_STORAGE_KEY)).toBeNull()
  })
  it.each(['connection', 'eval', 'perf'] as const)('restores missing %s fields from defaults', (field) => {
    const expected = emptyWorkspace()
    for (const key of Object.keys(expected[field])) {
      const state = emptyWorkspace()
      delete (state[field] as unknown as Record<string, unknown>)[key]
      localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(state))
      expect(readWorkspace()).toEqual({ ok: true, value: expected })
    }
  })
  it('only explicitly saved connection keys survive refresh', () => {
    const state = savedWorkspace()
    expect(writeWorkspace(state).ok).toBe(true)
    expect(readWorkspace()).toMatchObject({
      ok: true,
      value: { connection: { apiKey: 'fake-secret' }, eval: { datasets: 'gsm8k' } },
    })
    const raw = JSON.parse(localStorage.getItem(CONFIG_STORAGE_KEY)!)
    expect(raw.connection.apiKey).toBe('')
    expect(raw.connections[0].value.apiKey).toBe('fake-secret')
  })
  it('never persists unsaved keys in automatic drafts', () => {
    const state = savedWorkspace()
    state.connections = []
    writeWorkspace(state)
    expect(localStorage.getItem(CONFIG_STORAGE_KEY)).not.toContain('fake-secret')
    expect(readWorkspace()).toMatchObject({ ok: true, value: { connection: { apiKey: '' } } })
  })
  it('strips keys from records that did not opt in', () => {
    const state = savedWorkspace()
    state.connections[0].rememberKey = false
    writeWorkspace(state)
    expect(localStorage.getItem(CONFIG_STORAGE_KEY)).not.toContain('fake-secret')
  })
  it('does not restore a cleared or unsaved replacement key', () => {
    const state = savedWorkspace()
    state.connection.apiKey = 'new-unsaved-key'
    writeWorkspace(state)
    expect(readWorkspace()).toMatchObject({ ok: true, value: { connection: { apiKey: '' } } })
    state.connection.apiKey = ''
    writeWorkspace(state)
    expect(storageSnapshot(state).restoreSavedKey).toBe(false)
  })
  it('does not restore credentials to another origin or protocol', () => {
    const state = savedWorkspace()
    state.connection.url = 'http://server-b:8000/v1'
    writeWorkspace(state)
    expect(readWorkspace()).toMatchObject({ ok: true, value: { connection: { apiKey: '' } } })
    state.connection.api = 'local'
    expect(storageSnapshot(state).restoreSavedKey).toBe(false)
  })
  it.each(['broken-json', '{"version":2}', 'null', '[]'])('rejects damaged storage without overwriting %s', (text) => {
    localStorage.setItem(CONFIG_STORAGE_KEY, text)
    expect(readWorkspace()).toEqual({ ok: false, error: 'invalidStorage' })
    expect(localStorage.getItem(CONFIG_STORAGE_KEY)).toBe(text)
  })
  it('reports unavailable storage and write failures', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('Denied')
    })
    expect(readWorkspace()).toEqual({ ok: false, error: 'storageFailed' })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Quota')
    })
    expect(writeWorkspace(emptyWorkspace())).toEqual({ ok: false, error: 'storageFailed' })
  })
  it('excludes credentials by default and exports them only explicitly', () => {
    const state = savedWorkspace()
    expect(exportLibrary(state, false)).not.toContain('fake-secret')
    expect(parseImport(exportLibrary(state, true)).connections[0].value.apiKey).toBe('fake-secret')
    expect(parseImport(exportLibrary(state, false)).connections[0].rememberKey).toBe(false)
  })
  it('imports older eval templates with missing fields', () => {
    const state = savedWorkspace()
    const value = { ...DEFAULT_EVAL, datasets: 'gsm8k' }
    state.evalTemplates = [{ id: 'old', name: 'Old template', value }]
    const raw = JSON.parse(exportLibrary(state, false))
    delete raw.evalTemplates[0].value.reasoningEffort
    delete raw.evalTemplates[0].value.reasoningTokens
    delete raw.evalTemplates[0].value.enableThinking
    expect(parseImport(JSON.stringify(raw)).evalTemplates[0].value).toEqual(value)
  })
  it('imports version-1 libraries and round-trips nested version-2 presets without node secrets', () => {
    const state = savedWorkspace()
    const old = JSON.parse(exportLibrary(state, false))
    old.version = 1
    delete old.aiperfTemplates
    expect(parseImport(JSON.stringify(old))).toMatchObject({ version: 2, aiperfTemplates: [] })

    state.aiperfTemplates = [{ id: 'aiperf', name: 'Cache', value: { ...DEFAULT_AIPERF, tokenizer: '/tok', nodes: [
      { id: 'p', nodeId: 'p', role: 'prefill', engine: 'vllm', url: 'http://p/metrics', labels: '', bearerToken: 'private' },
    ] } }]
    const imported = parseImport(exportLibrary(state, true))
    expect(imported.aiperfTemplates[0].value.nodes[0]).toMatchObject({ nodeId: 'p', bearerToken: '' })
    const merged = mergeLibrary(emptyWorkspace(), imported)
    expect(merged.aiperfTemplates[0].value.ratios).toBe('0, 80, 90')
    expect(merged.aiperfTemplates[0].id).not.toBe('aiperf')
    state.aiperfTemplates[0].value.nodes[0].nodeId = 'changed'
    expect(merged.aiperfTemplates[0].value.nodes[0].nodeId).toBe('p')
  })
  it('imports as new ids without overwriting existing names and values', () => {
    const state = savedWorkspace()
    const merged = mergeLibrary(state, parseImport(exportLibrary(state, true)))
    expect(merged.connections).toHaveLength(2)
    expect(merged.connections[0]).toEqual(state.connections[0])
    expect(merged.connections[1].name).toBe('Server A (2)')
    expect(merged.connections[1].id).not.toBe('a')
    expect(state.connections).toHaveLength(1)
  })
  it('saves drafts as explicit independent templates', () => {
    const draft = { ...DEFAULT_EVAL, datasets: 'gsm8k' }
    const saved = savePreset([], '', 'QA', draft, false)
    draft.datasets = 'arc'
    expect(saved.entries[0].value.datasets).toBe('gsm8k')
    const replaced = savePreset(saved.entries, saved.selectedId, 'QA2', draft, false)
    expect(replaced.entries).toHaveLength(1)
    expect(replaced.selectedId).toBe(saved.selectedId)
    expect(savePreset(replaced.entries, replaced.selectedId, 'Copy', draft, true).entries).toHaveLength(2)
  })
  it.each(['api', 'value', 'id'])('rejects an invalid connection %s', (field) => {
    const raw = JSON.parse(exportLibrary(savedWorkspace(), true))
    if (field === 'api') raw.connections[0].value.api = 'unsupported'
    else raw.connections[0][field] = null
    expect(() => parseImport(JSON.stringify(raw))).toThrow()
  })
  it('rejects unexpected fields, dangerous keys and oversized imports', () => {
    const raw = JSON.parse(exportLibrary(savedWorkspace(), true))
    raw.connections[0].value.headers = { Authorization: 'secret' }
    expect(() => parseImport(JSON.stringify(raw))).toThrow()
    expect(() =>
      parseImport('{"version":1,"__proto__":{},"connections":[],"evalTemplates":[],"perfTemplates":[]}'),
    ).toThrow()
    expect(() => parseImport(' '.repeat(1024 * 1024 + 1))).toThrow()
  })
})

describe('Shared connection and template semantics', () => {
  it('maps the same connection into distinct backend contracts', () => {
    const connection = savedWorkspace().connection
    expect(connectionPayload(connection, 'eval')).toMatchObject({ api_url: connection.url, api_key: 'fake-secret' })
    expect(connectionPayload(connection, 'perf')).toMatchObject({
      url: connection.url,
      api: 'openai',
      api_key: 'fake-secret',
    })
    expect(connectionPayload({ ...connection, api: 'local' }, 'perf')).not.toHaveProperty('api_key')
  })
  it('clears credentials when switching destinations, but not model or URL path', () => {
    const connection = savedWorkspace().connection
    expect(editConnection(connection, { url: 'http://server-b:8000/v1' }).apiKey).toBe('')
    expect(editConnection(connection, { api: 'local' }).apiKey).toBe('')
    expect(editConnection(connection, { url: 'http://server-a:8000/v1/chat/completions' }).apiKey).toBe('fake-secret')
    expect(editConnection(connection, { model: 'other-model' }).apiKey).toBe('fake-secret')
    expect(connection.apiKey).toBe('fake-secret')
  })
  it('validates templates without requiring a model connection', () => {
    expect(validateEvalDraft({ ...DEFAULT_EVAL, datasets: 'gsm8k', limit: '.25', topP: '.95' })).toEqual({})
    expect(validatePerfDraft({ ...DEFAULT_PERF, parallel: '1,2', number: '4,8', rate: '2.5' })).toEqual({})
    expect(validatePerfDraft({ ...DEFAULT_PERF, parallel: '1,2' })).toHaveProperty('number')
    expect(validateEvalDraft({ ...DEFAULT_EVAL, datasets: 'gsm8k', timeout: '0' })).toHaveProperty('timeout')
  })
})
