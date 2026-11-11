import { describe, expect, it } from 'vitest'
import type { PerfPlanDocument, PerfPlanPreviewResponse } from '../../api/generated/contracts'
import {
  canSubmitPlan,
  checkPlanFileSize,
  diffPlanParameters,
  formatRatio,
  formatRatioDelta,
  formatRatioTolerance,
  formatReuseRatio,
  formatTokens,
  isPortablePlanText,
  matrixRows,
  missingAcknowledgements,
  planExportFileName,
  PLAN_LIMIT_BYTES,
} from './perfPlan'

function plan(overrides: Partial<PerfPlanDocument> = {}): PerfPlanDocument {
  return {
    kind: 'iss-eval.perf-plan',
    schema_version: '1.0',
    name: 'Small managed diagnostic',
    intent: 'diagnostic',
    connection: {
      model: 'served-model',
      url: 'http://127.0.0.1:8000/v1',
      tokenizer_path: '/models/tokenizer',
      protocol: 'openai_chat',
      streaming: true,
    },
    deployment: { mode: 'aggregated', declared_max_model_len: 8192, evidence: 'declared' },
    workload: { kind: 'token_prefix', input_lengths: [1024], target_ratios: [0.9], prefix_pool_size: 1, seed: 42 },
    load: { mode: 'closed_loop', points: [{ concurrency: 4 }], request_count: 16, output_token_limit: 32, repetitions: 1 },
    warmup: {
      enabled: false,
      request_count: 3,
      input_tokens: 16000,
      output_token_limit: 1000,
      concurrency: 1,
      failure_policy: 'abort',
    },
    priming: { policy: 'prime_all_prefixes', timeout_seconds: 30 },
    execution: { order: 'load_then_input' },
    telemetry: { required: false, targets: [] },
    ...overrides,
  } as PerfPlanDocument
}

function preview(overrides: Partial<PerfPlanPreviewResponse> = {}): PerfPlanPreviewResponse {
  return {
    schema_version: '1.0',
    contract_version: '1.0',
    compiler_version: '1.0',
    workload_semantics: 'managed_token_prefix',
    intent: 'diagnostic',
    plan_hash: 'a'.repeat(64),
    normalized_hash: 'b'.repeat(64),
    source_hash: 'c'.repeat(64),
    semantic_fingerprint: 'd'.repeat(64),
    normalized: plan(),
    runs: [
      {
        run_id: 'i000-r000-l000-n000',
        order: 0,
        workload_kind: 'token_prefix',
        message_structure: 'user_only',
        case_id: null,
        input_tokens: 1024,
        target_ratio: 0.9,
        declared_total_input_tokens: null,
        shared_system_tokens: null,
        unique_user_tokens: null,
        concurrency: 4,
        request_count: 16,
        output_token_limit: 32,
        repetition: 0,
        primer_requests: 1,
      },
    ],
    total_runs: 1,
    offset: 0,
    limit: 100,
    budgets: {
      formal_requests: 16,
      formal_output_tokens: 512,
      warmup_requests: 0,
      warmup_output_tokens: 0,
      primer_requests: 1,
      total_requests: 17,
    },
    diagnostics: [],
    alignment: { schema_version: '1.0', overall: 'unknown', dimensions: [] },
    context: [],
    can_submit: true,
    requires_acknowledgement: [],
    acknowledged: [],
    engine_available: true,
    runtime: { tokenizer_status: 'unknown', runtime_status: 'available' },
    ...overrides,
  } as PerfPlanPreviewResponse
}

describe('plan file intake', () => {
  it('rejects an empty file before upload', () => {
    expect(checkPlanFileSize(0)).toBe('tasks.aiperf.planImport.errorEmpty')
  })

  it('rejects a file over the byte limit', () => {
    expect(checkPlanFileSize(PLAN_LIMIT_BYTES + 1)).toBe('tasks.aiperf.planImport.errorTooLarge')
    expect(checkPlanFileSize(PLAN_LIMIT_BYTES)).toBeNull()
  })

  it('recognizes a portable plan without parsing it', () => {
    expect(isPortablePlanText('{"kind": "iss-eval.perf-plan"}')).toBe(true)
    expect(isPortablePlanText('{"version": 2, "connections": []}')).toBe(false)
  })
})

describe('parameter diff', () => {
  it('reports changed leaves with their paths', () => {
    const after = plan({
      connection: { ...plan().connection, model: 'other-model' },
      load: { ...plan().load, request_count: 8 },
    })
    const diff = diffPlanParameters(plan(), after)
    expect(diff.map((item) => item.path)).toEqual(['connection.model', 'load.request_count'])
    expect(diff[0]).toEqual({ path: 'connection.model', before: 'served-model', after: 'other-model' })
  })

  it('reports dropped blocks as absent rather than as no change', () => {
    const after = plan({ warmup: undefined as unknown as PerfPlanDocument['warmup'] })
    const paths = diffPlanParameters(plan(), after).map((item) => item.path)
    expect(paths.some((path) => path.startsWith('warmup.'))).toBe(true)
  })

  it('returns no entries for identical plans', () => {
    expect(diffPlanParameters(plan(), plan())).toEqual([])
  })
})

describe('preview projections', () => {
  it('formats ratios and token counts without inventing precision', () => {
    expect(formatRatio(0.9)).toBe('90%')
    expect(formatRatio(null)).toBe('—')
    expect(formatTokens(235800)).toBe('235,800')
    expect(formatTokens(undefined)).toBe('—')
  })

  it('keeps an observed 0 % distinguishable from a missing observation', () => {
    expect(formatReuseRatio(0)).toBe('0.0%')
    expect(formatReuseRatio(null)).toBe('—')
    expect(formatRatioDelta(0)).toBe('0.0 pp')
    expect(formatRatioDelta(-0.3)).toBe('-30.0 pp')
    expect(formatRatioDelta(null)).toBe('—')
    expect(formatRatioTolerance(0.05)).toBe('±5.0 pp')
    expect(formatRatioTolerance(undefined)).toBe('±—')
  })

  it('renders the managed matrix with the user-only structure', () => {
    const [row] = matrixRows(preview())
    expect(row).toMatchObject({ runId: 'i000-r000-l000-n000', structure: 'user_only', input: '1,024', ratio: '90%' })
  })

  it('renders the synthetic split as shared plus unique', () => {
    const base = preview()
    const rows = matrixRows({
      ...base,
      runs: [
        {
          ...base.runs[0],
          workload_kind: 'aiperf_synthetic',
          message_structure: 'system_and_user',
          input_tokens: null,
          shared_system_tokens: 900,
          unique_user_tokens: 100,
          primer_requests: 0,
        },
      ],
    } as PerfPlanPreviewResponse)
    expect(rows[0].input).toBe('900 + 100')
  })
})

describe('submission gating', () => {
  it('lists acknowledgement codes the operator has not confirmed', () => {
    expect(missingAcknowledgements(['a', 'b'], ['b'])).toEqual(['a'])
  })

  it('requires a preview first', () => {
    expect(canSubmitPlan(null, [])).toEqual({ allowed: false, reason: 'noPreview' })
  })

  it('blocks when the engine is unavailable', () => {
    expect(canSubmitPlan(preview({ engine_available: false }), [])).toEqual({
      allowed: false,
      reason: 'engineUnavailable',
    })
  })

  it('blocks when the server refused the plan', () => {
    expect(canSubmitPlan(preview({ can_submit: false }), [])).toEqual({ allowed: false, reason: 'blocked' })
  })

  it('blocks until every warning is acknowledged', () => {
    const state = canSubmitPlan(preview({ requires_acknowledgement: ['request_count_below_concurrency'] }), [])
    expect(state).toEqual({ allowed: false, reason: 'missingAcknowledgement' })
  })

  it('allows submission once the state is complete', () => {
    const state = canSubmitPlan(preview({ requires_acknowledgement: ['request_count_below_concurrency'] }), [
      'request_count_below_concurrency',
    ])
    expect(state).toEqual({ allowed: true, reason: 'ready' })
  })
})

describe('export naming', () => {
  it('slugifies the plan name', () => {
    expect(planExportFileName('Small managed diagnostic')).toBe('small-managed-diagnostic.perf-plan.json')
  })

  it('falls back when the plan has no name', () => {
    expect(planExportFileName(null)).toBe('perf-plan.perf-plan.json')
    expect(planExportFileName('///')).toBe('perf-plan.perf-plan.json')
  })
})
