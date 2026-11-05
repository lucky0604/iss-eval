import { describe, expect, it } from 'vitest'
import { buildAIPerfPayload, DEFAULT_AIPERF, sanitizedAIPerfDraft, validateAIPerfDraft } from './aiperfDraft'
import { DEFAULT_CONNECTION } from './taskConfig'

const valid = { ...DEFAULT_AIPERF, tokenizer: '/tokenizer' }
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
