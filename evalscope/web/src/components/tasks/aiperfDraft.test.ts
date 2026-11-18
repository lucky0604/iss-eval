import { describe, expect, it } from 'vitest'
import { buildAIPerfPayload, DEFAULT_AIPERF, sanitizedAIPerfDraft, validateAIPerfDraft } from './aiperfDraft'
import { DEFAULT_CONNECTION } from './taskConfig'

const valid = { ...DEFAULT_AIPERF, tokenizer: '/tokenizer' }
const node = { id: 'one', nodeId: 'p', role: 'prefill' as const, engine: 'vllm' as const,
  url: 'http://p:9000/metrics', labels: '', bearerToken: '' }

describe('AIPerf experiment configuration', () => {
  it('preserves zero, target ratios, seed and open-loop rates in the backend envelope', () => {
    const draft = {
      ...valid, ratios: '0, 80, 90', seed: '0', mode: 'open_loop' as const, loads: '0.5, 3', requestMultiplier: '',
    }
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
  it('sends GPU telemetry switches and DCGM URLs to the backend envelope', () => {
    const draft = { ...valid, gpuTelemetryUrls: 'http://10.0.0.1:9400\nhttp://10.0.0.2:9400/metrics' }
    expect(validateAIPerfDraft(draft)).toEqual({})
    expect(buildAIPerfPayload(draft, { ...DEFAULT_CONNECTION, model: 'm', url: 'http://host/v1' })).toMatchObject({
      engine: 'aiperf',
      aiperf: { gpu_telemetry: true, gpu_telemetry_urls: ['http://10.0.0.1:9400', 'http://10.0.0.2:9400/metrics'] },
    })
    expect(buildAIPerfPayload({ ...draft, gpuTelemetry: false }, { ...DEFAULT_CONNECTION, model: 'm', url: 'http://host/v1' }))
      .toMatchObject({ aiperf: { gpu_telemetry: false } })
  })
  it('rejects duplicate and credential-bearing GPU DCGM URLs', () => {
    expect(validateAIPerfDraft({ ...valid, gpuTelemetryUrls: 'http://gpu:9400\nhttp://gpu:9400/metrics' }))
      .toHaveProperty('gpuTelemetryUrls')
    expect(validateAIPerfDraft({ ...valid, gpuTelemetryUrls: 'http://user:secret@gpu:9400/metrics' }))
      .toHaveProperty('gpuTelemetryUrls')
  })
  it('sweeps every input length as its own subrun', () => {
    const draft = { ...valid, inputTokens: '8192, 32768, 65536, 131072' }
    expect(validateAIPerfDraft(draft)).toEqual({})
    expect(buildAIPerfPayload(draft, { ...DEFAULT_CONNECTION, model: 'm', url: 'http://host/v1' })).toMatchObject({
      aiperf: { cache_experiment: { total_input_tokens: 8192, input_token_values: [8192, 32768, 65536, 131072] } },
    })
  })
  it('keeps a single length on total_input_tokens so existing plans keep their configuration hash', () => {
    const request = buildAIPerfPayload({ ...valid, inputTokens: '32768' },
      { ...DEFAULT_CONNECTION, model: 'm', url: 'http://host/v1' })
    const cache = (request.aiperf as Record<string, Record<string, unknown>>).cache_experiment
    expect(cache.total_input_tokens).toBe(32768)
    expect(cache.input_token_values).toBeUndefined()
  })
  it.each(['8192, 8192', '8192,,1', '8k', '-1', ''])('rejects invalid input lengths %s', (inputTokens) => {
    expect(validateAIPerfDraft({ ...valid, inputTokens })).toHaveProperty('inputTokens')
  })
  it('derives the request budget from concurrency times multiplier', () => {
    const draft = { ...valid, loads: '1, 8, 16', requestMultiplier: '3', requestCount: '' }
    expect(validateAIPerfDraft(draft)).toEqual({})
    expect(buildAIPerfPayload(draft, { ...DEFAULT_CONNECTION, model: 'm', url: 'http://host/v1' })).toMatchObject({
      aiperf: { load: { request_count: 3, request_count_multiplier: 3 } },
    })
  })
  it('requires a fixed request count when open loop has no concurrency to multiply', () => {
    expect(validateAIPerfDraft({ ...valid, mode: 'open_loop', requestMultiplier: '', requestCount: '' }))
      .toHaveProperty('requestCount')
  })
  it('counts input lengths in the experiment matrix limit', () => {
    expect(validateAIPerfDraft({ ...valid, inputTokens: '8192, 32768, 65536, 131072', repetitions: '1000' }))
      .toHaveProperty('repetitions')
  })
  it('sends per-point request multiplier, SLOs and failed threshold', () => {
    const draft = { ...valid, requestMultiplier: '3', slos: 'request_latency:250, inter_token_latency:10', failedThreshold: '0.05' }
    expect(validateAIPerfDraft(draft)).toEqual({})
    expect(buildAIPerfPayload(draft, { ...DEFAULT_CONNECTION, model: 'm', url: 'http://host/v1' })).toMatchObject({
      aiperf: {
        load: { request_count_multiplier: 3 },
        slos: { request_latency: 250, inter_token_latency: 10 },
        failed_request_threshold: 0.05,
      },
    })
  })
  it.each(['abc', '1.5', '0', '1001'])('rejects invalid request multiplier %s', (requestMultiplier) => {
    expect(validateAIPerfDraft({ ...valid, requestMultiplier })).toHaveProperty('requestMultiplier')
  })
  it('checks the shared prefix pool against the multiplied request budget', () => {
    const draft = { ...valid, requestCount: '10', loads: '8', pool: '12', requestMultiplier: '2' }
    expect(validateAIPerfDraft(draft)).toEqual({})
    expect(validateAIPerfDraft({ ...draft, requestMultiplier: '' })).toHaveProperty('pool')
  })
  it('rejects a request multiplier outside closed-loop multi-run mode', () => {
    expect(validateAIPerfDraft({ ...valid, requestMultiplier: '3', mode: 'open_loop' })).toHaveProperty('requestMultiplier')
    expect(validateAIPerfDraft({ ...valid, requestMultiplier: '3', runType: 'single', prompts: 'one' }))
      .toHaveProperty('requestMultiplier')
  })
  it.each(['request_latency', 'request_latency:0', 'Bad Key:1', 'request_latency:1,request_latency:2'])(
    'rejects malformed SLOs %s',
    (slos) => {
      expect(validateAIPerfDraft({ ...valid, slos })).not.toEqual({})
    },
  )
  it('rejects out-of-range failed thresholds and contexts above 1M', () => {
    expect(validateAIPerfDraft({ ...valid, slos: 'request_latency:250', failedThreshold: '1.5' }))
      .toHaveProperty('failedThreshold')
    expect(validateAIPerfDraft({ ...valid, inputTokens: '1048576' })).toHaveProperty('inputTokens')
  })
  it('strips credentials from persisted GPU DCGM URLs', () => {
    const sanitized = sanitizedAIPerfDraft({ ...valid, gpuTelemetryUrls: 'http://gpu:9400/metrics\nhttps://u:p@gpu2:9400/metrics' })
    expect(sanitized.gpuTelemetryUrls).toBe('http://gpu:9400/metrics')
  })
})
