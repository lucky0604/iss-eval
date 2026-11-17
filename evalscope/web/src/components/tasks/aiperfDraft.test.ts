import { describe, expect, it } from 'vitest'
import {
  buildAIPerfPayload,
  buildPlanRuntime,
  DEFAULT_AIPERF,
  matrixInputFromDraft,
  sanitizedAIPerfDraft,
  validateAIPerfDraft,
} from './aiperfDraft'
import { buildPlanDocument, buildPlanText } from '@/domain/perf/planDraft'
import { DEFAULT_CONNECTION } from './taskConfig'

const valid = { ...DEFAULT_AIPERF, runType: 'cache' as const, tokenizer: '/tokenizer' }
const node = { id: 'one', nodeId: 'p', role: 'prefill' as const, engine: 'vllm' as const,
  url: 'http://p:9000/metrics', labels: '', bearerToken: '' }

describe('AIPerf experiment configuration', () => {
  it('preserves zero, target ratios, seed and open-loop rates in the backend envelope', () => {
    const draft = { ...valid, ratios: '0, 80, 90', seed: '0', mode: 'open_loop' as const, loads: '0.5, 3' }
    expect(validateAIPerfDraft(draft)).toEqual({})
    expect(buildAIPerfPayload(draft, { ...DEFAULT_CONNECTION, model: 'm', url: 'http://host/v1' })).toMatchObject({
      engine: 'aiperf', aiperf: { cache_experiment: { target_ratios: [0, 0.8, 0.9], seed: 0 },
        load: { mode: 'open_loop', points: [{ rate: 0.5 }, { rate: 3 }] } },
    })
  })
  it.each(['', '0,', '0,0', '100', '-1', 'NaN'])('rejects invalid ratios %s', (ratios) => {
    expect(validateAIPerfDraft({ ...valid, ratios })).toHaveProperty('ratios')
  })
  it.each(['model=a,model=b', '__proto__=x', 'constructor=x', 'bad label=a', 'x='])('rejects invalid labels %s', (labels) => {
    expect(validateAIPerfDraft({ ...valid, nodes: [{ ...node, labels }] })).toHaveProperty('node-one-labels')
  })
  it('rejects duplicate endpoints and requires both roles when PD metrics are required', () => {
    const errors = validateAIPerfDraft({ ...valid, required: true, deploymentMode: 'pd', nodes: [node,
      { ...node, id: 'two', nodeId: 'p2', url: 'http://p:9000/metrics/' }] })
    expect(errors).toHaveProperty('node-two-url')
    expect(errors).toHaveProperty('nodes')
  })
  it('clears tokens and potentially credential-bearing incomplete URLs from persisted drafts', () => {
    const draft = { ...valid, nodes: [{ ...node, url: 'https://host/path?token=secret', bearerToken: 'secret' }] }
    expect(sanitizedAIPerfDraft(draft).nodes[0]).toMatchObject({ url: '', bearerToken: '' })
    expect(draft.nodes[0].bearerToken).toBe('secret')
  })
})

const connectionWithKey = () => ({ ...DEFAULT_CONNECTION, model: 'm', url: 'http://h/v1', api: 'openai' as const, apiKey: 'sk' })

describe('AIPerf full matrix draft', () => {
  const matrix = { ...DEFAULT_AIPERF, tokenizer: '/tokenizer', matrixMaxModelLen: '32768' }

  it('requires the declared context limit before a plan can be compiled', () => {
    expect(validateAIPerfDraft(matrix)).toEqual({})
    expect(validateAIPerfDraft({ ...matrix, matrixMaxModelLen: '' })).toHaveProperty('matrixMaxModelLen')
    expect(validateAIPerfDraft({ ...matrix, matrixMaxModelLen: '0' })).toHaveProperty('matrixMaxModelLen')
  })

  it('reports each list control on its own field', () => {
    expect(validateAIPerfDraft({ ...matrix, matrixInputLengths: '1024,' })).toHaveProperty('matrixInputLengths')
    expect(validateAIPerfDraft({ ...matrix, matrixOutputLimits: '128, 128' })).toHaveProperty('matrixOutputLimits')
    expect(validateAIPerfDraft({ ...matrix, matrixScene: 'cache_comparison', ratios: '100' }))
      .toHaveProperty('ratios')
    expect(validateAIPerfDraft({ ...matrix, mode: 'open_loop', loads: '0' })).toHaveProperty('loads')
    expect(validateAIPerfDraft({ ...matrix, matrixWarmupInput: '0' })).toHaveProperty('matrixWarmupInput')
  })

  it('ignores the ratio list in a plain performance run but requires it for a comparison', () => {
    expect(validateAIPerfDraft({ ...matrix, ratios: 'bad' })).toEqual({})
    expect(validateAIPerfDraft({ ...matrix, matrixScene: 'cache_comparison', ratios: '100' }))
      .toHaveProperty('ratios')
  })

  it('maps the draft onto the typed matrix input, including PD and open-loop points', () => {
    const input = matrixInputFromDraft({
      ...matrix, matrixInputLengths: '1024，8192', matrixOutputLimits: '128 512', ratios: '0, 90',
      deploymentMode: 'pd', mode: 'open_loop', loads: '0.5, 3', matrixOrder: 'load_then_input',
    })
    expect(input).toMatchObject({
      tokenizer: '/tokenizer', inputLengths: [1024, 8192], outputLimits: [128, 512], ratios: [0, 90],
      mode: 'open_loop', points: [0.5, 3], deploymentMode: 'disaggregated', order: 'load_then_input',
      maxModelLen: 32768,
    })
  })

  it('keeps the model key and node targets in runtime input only', () => {
    const connection = { ...DEFAULT_CONNECTION, model: 'm', url: 'http://host/v1', api: 'openai' as const, apiKey: 'sk-secret' }
    const draft = { ...matrix, matrixMonitoringEnabled: true,
      nodes: [{ ...node, labels: 'model=a', bearerToken: 'node-secret' }] }
    expect(buildPlanRuntime(draft, connection)).toMatchObject({
      api_key: 'sk-secret',
      telemetry: { required: false, targets: [{ node_id: 'p', labels: { model: 'a' },
        headers: { Authorization: 'Bearer node-secret' } }] },
    })
    const text = buildPlanText(matrixInputFromDraft(draft), connection)
    expect(text).not.toContain('sk-secret')
    expect(text).not.toContain('node-secret')
    expect(buildPlanDocument(matrixInputFromDraft(draft), connection).telemetry).toEqual({ required: false, targets: [] })
  })

  it('labels a required node metric with real targets and blocks an empty requirement', () => {
    expect(validateAIPerfDraft({ ...matrix, matrixMonitoringEnabled: true, required: true }))
      .toHaveProperty('nodes')
    const runtime = buildPlanRuntime({ ...matrix, matrixMonitoringEnabled: true, required: true },
      { ...DEFAULT_CONNECTION, model: 'm', url: 'http://h/v1' })
    expect(runtime.telemetry).toMatchObject({ required: true, targets: [] })
  })

  it('ignores hidden monitoring inputs until the user enables node metrics', () => {
    const draft = { ...matrix, nodes: [{ ...node, url: '' }] }
    expect(validateAIPerfDraft(draft)).toEqual({})
    expect(buildPlanRuntime(draft, connectionWithKey())).not.toHaveProperty('telemetry')
    expect(validateAIPerfDraft({ ...draft, matrixMonitoringEnabled: true })).toHaveProperty('node-one-url')
  })
})
