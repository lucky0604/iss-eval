import { describe, expect, it } from 'vitest'
import {
  buildPlanDocument,
  buildPlanText,
  matrixRunCount,
  parseIntegerList,
  parseRatioList,
  parseRateList,
  percentileToRatio,
  type PlanMatrixInput,
} from './planDraft'

const input = (overrides: Partial<PlanMatrixInput> = {}): PlanMatrixInput => ({
  scene: 'performance',
  name: 'matrix',
  tokenizer: '/tokenizer',
  inputLengths: [1024, 8192],
  outputLimits: [128, 512],
  ratios: [0, 90],
  pool: 1,
  seed: 42,
  mode: 'closed_loop',
  points: [1, 4],
  requestCount: 10,
  repetitions: 1,
  order: 'seeded_shuffle',
  deploymentMode: 'aggregated',
  maxModelLen: 32768,
  warmup: { enabled: true, requestCount: 2, inputTokens: 1024, outputTokenLimit: 128, concurrency: 1 },
  ...overrides,
})

const connection = { model: 'served-model', url: 'http://127.0.0.1:8000/v1' }

describe('matrix list controls', () => {
  it.each([
    ['1024, 8192', [1024, 8192]],
    ['1024，8192', [1024, 8192]],
    ['1024,8192\n2048', [1024, 8192, 2048]],
    [' 1024 ; 8192 ', [1024, 8192]],
  ])('accepts English/Chinese separators and whitespace in %j', (text, expected) => {
    expect(parseIntegerList(text)).toEqual({ values: expected })
  })

  it.each([
    ['', 'empty'],
    ['   ', 'empty'],
    ['1024,', 'blankItem'],
    [',1024', 'blankItem'],
    ['1.5', 'notInteger'],
    ['0', 'notPositive'],
    ['-1', 'notPositive'],
    ['1,1', 'duplicate'],
  ])('rejects %j as %s', (text, error) => {
    expect(parseIntegerList(text)).toEqual({ error })
  })

  it('accepts decimal rates for an open loop but not booleans or zero', () => {
    expect(parseRateList('0.5, 3')).toEqual({ values: [0.5, 3] })
    expect(parseRateList('0')).toEqual({ error: 'notPositive' })
    expect(parseRateList('NaN')).toEqual({ error: 'notFinite' })
    expect(parseRateList('1, 1.0')).toEqual({ error: 'duplicate' })
  })

  it('keeps ratio zero and rejects a fully shared prompt at 100 %', () => {
    expect(parseRatioList('0, 80, 90')).toEqual({ values: [0, 80, 90] })
    expect(parseRatioList('100')).toEqual({ error: 'outOfRange' })
    expect(parseRatioList('-1')).toEqual({ error: 'outOfRange' })
    expect(parseRatioList('')).toEqual({ error: 'empty' })
  })

  it('converts percent to a document fraction without float noise', () => {
    expect(percentileToRatio(90)).toBe(0.9)
    expect(percentileToRatio(33)).toBe(0.33)
    expect(percentileToRatio(0)).toBe(0)
  })
})

describe('plan document built from the matrix', () => {
  it('measures the cache-off baseline for an ordinary performance run', () => {
    const document = buildPlanDocument(input({ ratios: [0, 90] }), connection)
    expect(document.workload).toMatchObject({ kind: 'token_prefix', input_lengths: [1024, 8192], target_ratios: [0] })
    expect(matrixRunCount(input({ ratios: [0, 90] }))).toBe(2 * 2 * 1 * 2 * 1)
  })

  it('sends every ratio, output limit and load point in a cache comparison', () => {
    const document = buildPlanDocument(input({ scene: 'cache_comparison', ratios: [0, 90] }), connection)
    expect(document.schema_version).toBe('1.1')
    expect(document.workload).toMatchObject({ target_ratios: [0, 0.9] })
    expect(document.load).toMatchObject({
      mode: 'closed_loop',
      points: [{ concurrency: 1 }, { concurrency: 4 }],
      output_token_limits: [128, 512],
      request_count: 10,
      repetitions: 1,
    })
    expect(matrixRunCount(input({ scene: 'cache_comparison', ratios: [0, 90] }))).toBe(2 * 2 * 2 * 2 * 1)
  })

  it('builds open-loop points as request rates, never concurrency', () => {
    const document = buildPlanDocument(input({ mode: 'open_loop', points: [0.5, 3] }), connection)
    expect(document.load).toMatchObject({ mode: 'open_loop', points: [{ rate: 0.5 }, { rate: 3 }] })
    for (const point of document.load.points) expect(point.concurrency).toBeUndefined()
  })

  it('declares the deployment shape and the deterministic execution order', () => {
    const document = buildPlanDocument(
      input({ deploymentMode: 'disaggregated', order: 'seeded_shuffle' }),
      connection,
    )
    expect(document.deployment).toEqual({
      mode: 'disaggregated', declared_max_model_len: 32768, evidence: 'declared',
    })
    expect(document.execution).toEqual({ order: 'seeded_shuffle', seed: 42 })
    expect(buildPlanDocument(input({ order: 'load_then_input' }), connection).execution).toEqual({ order: 'load_then_input' })
  })

  it('never puts credentials or telemetry targets in the portable document', () => {
    // The builder receives only the model and URL; a key held by the form is not
    // an input to the document and must reach the worker as runtime data instead.
    const text = buildPlanText(input(), connection)
    expect(text).not.toContain('api_key')
    expect(buildPlanDocument(input(), connection).telemetry).toEqual({ required: false, targets: [] })
  })

  it('keeps the warmup budget inside the document', () => {
    expect(buildPlanDocument(input(), connection).warmup).toEqual({
      enabled: true, request_count: 2, input_tokens: 1024, output_token_limit: 128, concurrency: 1, failure_policy: 'abort',
    })
  })
})
