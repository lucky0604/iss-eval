import { describe, expect, it } from 'vitest'
import {
  aiperfTaskNeedsCompletion,
  buildAIPerfPromptPayload,
  buildPlanRuntime,
  defaultAIPerfDraft,
  generatedPlanInput,
  promptLines,
  sanitizedAIPerfTaskDraft,
  validateAIPerfTask,
  type AIPerfTaskDraft,
} from './aiperfDraft'

const connection = {
  model: 'served-model', url: 'http://localhost:1234/v1', apiKey: 'model-secret', alias: '', api: 'openai' as const,
}

function generated(overrides: Partial<AIPerfTaskDraft['generated']> = {}): AIPerfTaskDraft {
  const draft = defaultAIPerfDraft()
  return {
    ...draft,
    tokenizer: '/tokenizer',
    generated: { ...draft.generated, maxModelLen: '32768', ...overrides },
  }
}

function prompts(overrides: Partial<AIPerfTaskDraft['prompts']> = {}): AIPerfTaskDraft {
  const draft = defaultAIPerfDraft()
  return {
    ...draft,
    inputSource: 'prompts',
    tokenizer: '/tokenizer',
    prompts: { ...draft.prompts, text: 'first request\n\n second request ', ...overrides },
  }
}

describe('promptLines', () => {
  it('drops only blank lines and never trims a kept request', () => {
    expect(promptLines('first\n  \n second request \n\n第三行')).toEqual(['first', ' second request ', '第三行'])
  })

  it('treats CRLF input the same way', () => {
    expect(promptLines('a\r\n\r\nb\r\n')).toEqual(['a', 'b'])
  })
})

describe('validateAIPerfTask — generated branch', () => {
  it('accepts the public defaults once the tokenizer and context are known', () => {
    expect(validateAIPerfTask(generated())).toEqual({})
    expect(aiperfTaskNeedsCompletion(generated())).toBe(false)
  })

  it('requires the tokenizer and a declared context', () => {
    expect(validateAIPerfTask({ ...generated(), tokenizer: '' })).toHaveProperty('tokenizer')
    expect(validateAIPerfTask(generated({ maxModelLen: '' }))).toHaveProperty('maxModelLen')
  })

  it('rejects a context smaller than the largest input plus largest output', () => {
    expect(validateAIPerfTask(generated({ inputLengths: '8192', outputLimits: '512', maxModelLen: '8000' })))
      .toHaveProperty('maxModelLen', 'tasks.aiperf.contextTooSmall')
  })

  it('ignores residual ratio and pool text while prefix comparison is off', () => {
    const draft = generated({ comparePrefix: false, ratios: 'not-a-list', pool: '999' })
    expect(validateAIPerfTask(draft)).toEqual({})
  })

  it('validates ratios only when comparison is on, and a 0%-only list does not constrain the pool', () => {
    expect(validateAIPerfTask(generated({ comparePrefix: true, ratios: 'bad' }))).toHaveProperty('ratios')
    expect(validateAIPerfTask(generated({ comparePrefix: true, ratios: '0', pool: '999' }))).toEqual({})
    expect(validateAIPerfTask(generated({ comparePrefix: true, ratios: '90', pool: '999', requestCount: '10' })))
      .toHaveProperty('pool', 'tasks.aiperf.poolTooLarge')
  })

  it('ignores warmup text while warmup is disabled and validates it when enabled', () => {
    const off = generated({ warmup: { enabled: false, requests: 'x', inputTokens: '', outputTokens: '0', concurrency: '' } })
    expect(validateAIPerfTask(off)).toEqual({})
    const on = generated({ warmup: { enabled: true, requests: '0', inputTokens: '', outputTokens: '0', concurrency: '' } })
    expect(validateAIPerfTask(on)).toMatchObject({
      warmupRequests: 'tasks.aiperf.invalid',
      warmupInput: 'tasks.aiperf.invalid',
      warmupOutput: 'tasks.aiperf.invalid',
      warmupConcurrency: 'tasks.aiperf.invalid',
    })
  })

  it('ignores node text while monitoring is off and reports node fields by editor id when on', () => {
    const draft = generated()
    draft.monitoring = {
      ...draft.monitoring,
      enabled: false,
      nodes: [{ id: 'one', nodeId: '', role: 'prefill', engine: 'vllm', url: '', labels: '', bearerToken: '' }],
    }
    expect(validateAIPerfTask(draft)).toEqual({})

    draft.monitoring = { ...draft.monitoring, enabled: true }
    expect(validateAIPerfTask(draft)).toMatchObject({
      'node-one-nodeId': 'tasks.aiperf.invalid',
      'node-one-url': 'form.validation.required',
    })
  })

  it('keeps the required-role rule for P/D while monitoring is on', () => {
    const draft = generated()
    draft.deploymentMode = 'pd'
    draft.monitoring = {
      enabled: true,
      required: true,
      sampleInterval: '1',
      maxGap: '',
      nodes: [{ id: 'one', nodeId: 'p', role: 'prefill', engine: 'vllm', url: 'http://p:9000/metrics',
        labels: '', bearerToken: '' }],
    }
    expect(validateAIPerfTask(draft)).toHaveProperty('nodes', 'tasks.aiperf.requiredRoles')
  })

  it('flags an oversized matrix on the repetitions field', () => {
    const draft = generated({
      inputLengths: '1024, 2048, 4096', outputLimits: '128, 256, 512', requestCount: '10', repetitions: '1200',
    })
    expect(validateAIPerfTask(draft)).toHaveProperty('repetitions', 'tasks.aiperf.matrixTooLarge')
  })
})

describe('validateAIPerfTask — real prompts', () => {
  it('accepts nonblank lines with one load point and one output limit', () => {
    expect(validateAIPerfTask(prompts())).toEqual({})
  })

  it('derives N from the lines and refuses concurrency above N', () => {
    const draft = prompts()
    draft.prompts.concurrency = '3'
    expect(validateAIPerfTask(draft)).toHaveProperty('promptConcurrency', 'tasks.aiperf.promptConcurrencyTooHigh')
    draft.prompts.concurrency = '2'
    expect(validateAIPerfTask(draft)).toEqual({})
  })

  it('requires a positive single output limit and a single positive rate in open loop', () => {
    expect(validateAIPerfTask(prompts({ outputLimit: '0' }))).toHaveProperty('promptOutputLimit')
    const open = prompts({ mode: 'open_loop', rate: '1, 2' })
    expect(validateAIPerfTask(open)).toHaveProperty('promptRate', 'tasks.aiperf.singleLoad')
    expect(validateAIPerfTask(prompts({ mode: 'open_loop', rate: '0.5' }))).toEqual({})
  })

  it('rejects empty and oversized prompt input', () => {
    expect(validateAIPerfTask(prompts({ text: ' \n\t' }))).toHaveProperty('prompts', 'tasks.aiperf.invalidPrompts')
    const tooMany = Array.from({ length: 10_001 }, (_, index) => `prompt ${index}`).join('\n')
    expect(validateAIPerfTask(prompts({ text: tooMany }))).toHaveProperty('prompts', 'tasks.aiperf.invalidPrompts')
  })
})

describe('effective generated input', () => {
  it('projects comparison off to the 0% performance baseline', () => {
    const input = generatedPlanInput(generated({ comparePrefix: false, ratios: 'bad', pool: '9' }))
    expect(input.scene).toBe('performance')
    expect(input.ratios).toEqual([])
    expect(input.pool).toBe(1)
  })

  it('keeps a disabled warmup schema-valid with public defaults but reports it disabled', () => {
    const input = generatedPlanInput(generated({
      warmup: { enabled: false, requests: 'x', inputTokens: '', outputTokens: '0', concurrency: '' },
    }))
    expect(input.warmup).toEqual({
      enabled: false, requestCount: 3, inputTokens: 16000, outputTokenLimit: 1000, concurrency: 1,
    })
  })

  it('uses edited ratios and pool only when comparison is on', () => {
    const input = generatedPlanInput(generated({ comparePrefix: true, ratios: '0, 80', pool: '2' }))
    expect(input.scene).toBe('cache_comparison')
    expect(input.ratios).toEqual([0, 80])
    expect(input.pool).toBe(2)
  })
})

describe('real-prompt payload', () => {
  it('sends every nonblank line verbatim as one request of one single-point load', () => {
    const payload = buildAIPerfPromptPayload(prompts(), connection)
    const aiperf = payload.aiperf as Record<string, unknown>
    expect(aiperf.input_prompts).toEqual(['first request', ' second request '])
    expect(aiperf.load).toEqual({
      mode: 'closed_loop', points: [{ concurrency: 1 }], request_count: 2, output_token_limit: 128, repetitions: 1,
    })
    expect(aiperf.cache_experiment).toBeUndefined()
    expect(aiperf.telemetry).toBeUndefined()
  })

  it('sends telemetry only when monitoring is explicitly enabled', () => {
    const draft = prompts()
    draft.monitoring = {
      enabled: true, required: false, sampleInterval: '1', maxGap: '',
      nodes: [{ id: 'one', nodeId: 'p', role: 'prefill', engine: 'vllm', url: 'http://p:9000/metrics',
        labels: '', bearerToken: '' }],
    }
    const aiperf = buildAIPerfPromptPayload(draft, connection).aiperf as Record<string, unknown>
    expect(aiperf.telemetry).toMatchObject({ required: false, sample_interval_seconds: 1, targets: [{ node_id: 'p' }] })
  })
})

describe('sanitizing and runtime binding', () => {
  it('clears credentials and credential-bearing URLs in every stored branch', () => {
    const draft = generated()
    draft.monitoring = {
      enabled: true, required: true, sampleInterval: '1', maxGap: '',
      nodes: [
        { id: 'one', nodeId: 'p', role: 'prefill', engine: 'vllm', url: 'http://user:pass@p:9000/metrics',
          labels: 'a=b', bearerToken: 'node-secret' },
        { id: 'two', nodeId: 'd', role: 'decode', engine: 'sglang', url: 'http://d:9000/metrics?token=x',
          labels: '', bearerToken: 'other-secret' },
      ],
    }
    const sanitized = sanitizedAIPerfTaskDraft(draft)
    expect(sanitized.monitoring.nodes.map((node) => node.bearerToken)).toEqual(['', ''])
    expect(sanitized.monitoring.nodes.map((node) => node.url)).toEqual(['', ''])
  })

  it('keeps the model key and node credentials in the runtime binding only', () => {
    const draft = generated()
    draft.monitoring = {
      enabled: true, required: true, sampleInterval: '1', maxGap: '',
      nodes: [{ id: 'one', nodeId: 'p', role: 'prefill', engine: 'vllm', url: 'http://p:9000/metrics',
        labels: 'model=alpha', bearerToken: 'node-secret' }],
    }
    const runtime = buildPlanRuntime(draft, connection)
    expect(runtime.api_key).toBe('model-secret')
    expect(runtime.telemetry?.targets).toEqual([{
      node_id: 'p', role: 'prefill', engine: 'vllm', url: 'http://p:9000/metrics',
      labels: { model: 'alpha' }, headers: { Authorization: 'Bearer node-secret' },
    }])
    expect(sanitizedAIPerfTaskDraft(draft)).not.toContain('model-secret')
  })

  it('omits telemetry entirely while monitoring is off', () => {
    expect(buildPlanRuntime(generated(), connection)).toEqual({ api_key: 'model-secret' })
  })
})
