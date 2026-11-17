import { useState } from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { DEFAULT_AIPERF, type AIPerfDraft } from '@/components/tasks/aiperfDraft'
import { buildPlanDocument, type PlanMatrixInput } from '@/domain/perf/planDraft'
import {
  exportPerfPlan,
  previewPerfPlan,
  submitPerfPlan,
} from '@/api/perfPlans'
import { getPerfEngines } from '@/api/perf'
import type { PerfPlanPreviewResponse, PlanRunSummary } from '@/api/generated/contracts'
import PlanMatrixForm from './PlanMatrixForm'

vi.mock('@/api/perf', () => ({ getPerfEngines: vi.fn() }))
vi.mock('@/api/perfPlans', () => ({
  exportPerfPlan: vi.fn(),
  previewPerfPlan: vi.fn(),
  submitPerfPlan: vi.fn(),
}))

const connection = {
  model: 'served-model', url: 'http://localhost:1234/v1', apiKey: 'model-secret', alias: '', api: 'openai' as const,
}
const draft = (overrides: Partial<AIPerfDraft> = {}): AIPerfDraft => ({
  ...DEFAULT_AIPERF, tokenizer: '/tokenizer', matrixMaxModelLen: '32768', ...overrides,
})

const matrixInput: PlanMatrixInput = {
  scene: 'performance', name: 'matrix', tokenizer: '/tokenizer', inputLengths: [1024, 8192],
  outputLimits: [128, 512], ratios: [0, 80, 90], pool: 1, seed: 42, mode: 'closed_loop', points: [1],
  requestCount: 10, repetitions: 1, order: 'seeded_shuffle', deploymentMode: 'aggregated', maxModelLen: 32768,
  warmup: { enabled: true, requestCount: 2, inputTokens: 1024, outputTokenLimit: 128, concurrency: 1 },
}

const run = (order: number): PlanRunSummary => ({
  order, run_id: `i1024-o128-r0-l1-n1-${order}`, input_tokens: 1024, output_token_limit: 128,
  target_ratio: 0, concurrency: 1, load_mode: 'closed_loop', primer_requests: 0, request_count: 10,
  repetition: 1, workload_kind: 'token_prefix', message_structure: 'user_only',
})

const preview = (overrides: Partial<PerfPlanPreviewResponse> = {}): PerfPlanPreviewResponse => ({
  alignment: { overall: 'unknown', dimensions: [] },
  budgets: { formal_requests: 1000, warmup_requests: 4, primer_requests: 6, total_requests: 1010,
    formal_output_tokens: 5000, warmup_output_tokens: 4 },
  can_submit: true, compiler_version: '1.3', context: [], contract_version: '1.1', diagnostics: [],
  engine_available: true, intent: 'benchmark', limit: 100, normalized: buildPlanDocument(matrixInput, connection),
  normalized_hash: 'normalized', offset: 0, output_token_limits: [128, 512], plan_hash: 'planhash',
  requires_acknowledgement: [], runs: Array.from({ length: 100 }, (_, index) => run(index + 1)),
  runtime: { tokenizer_status: 'resolved' }, semantic_fingerprint: 'fingerprint', source_hash: 'source',
  total_runs: 250, workload_semantics: 'managed_token_prefix', ...overrides,
})

function Harness({ initial = draft(), onPlanSubmitted }: {
  initial?: AIPerfDraft, onPlanSubmitted?: (taskId: string) => void
}) {
  const [value, setValue] = useState<AIPerfDraft>(structuredClone(initial))
  return (
    <LocaleProvider>
      <PlanMatrixForm connection={connection} onSubmit={async () => {}} disabled={false} draft={value}
        onChange={setValue} onValidateConnection={() => ({})} onPlanSubmitted={onPlanSubmitted} />
    </LocaleProvider>
  )
}

beforeEach(() => {
  vi.mocked(getPerfEngines).mockResolvedValue({ engines: [{ engine: 'aiperf', available: true }] })
  vi.mocked(previewPerfPlan).mockResolvedValue(preview())
  vi.mocked(submitPerfPlan).mockResolvedValue({ task_id: 'perf_matrix_1', state: 'queued', duplicate: false })
})
afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks() })

describe('Full matrix form', () => {
  it('groups the inputs and shows each real parameter name beside its Chinese title', async () => {
    localStorage.setItem('evalscope-locale', 'zh')
    await act(async () => { render(<Harness />) })
    expect(screen.getByRole('heading', { name: '基础设置' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '高级设置' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '可选监控' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '完整矩阵（推荐）' })).toBeInTheDocument()
    const labelOf = (name: RegExp) => {
      const input = screen.getByLabelText(name)
      return document.getElementById(input.getAttribute('aria-labelledby')!)!
    }
    expect(labelOf(/输入长度/).textContent).toContain('输入长度')
    expect(labelOf(/输入长度/).textContent).toContain('input_lengths')
    expect(labelOf(/输出长度上限/).textContent).toContain('output_token_limits')
    expect(labelOf(/模型上下文上限/).textContent).toContain('deployment.declared_max_model_len')
    expect(screen.getByLabelText(/并发数/)).toBeInTheDocument()
  })

  it('previews the exact plan text, pages it and submits the same plan with runtime credentials', async () => {
    const submitted = vi.fn()
    vi.mocked(previewPerfPlan).mockResolvedValue(preview({ requires_acknowledgement: ['cache_reuse'] }))
    await act(async () => { render(<Harness onPlanSubmitted={submitted} />) })
    expect(screen.getByText(/AIPerf 0.13.0: Available/)).toBeInTheDocument()

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Preview matrix' })) })
    const [content, options] = vi.mocked(previewPerfPlan).mock.calls[0]
    const document = JSON.parse(content)
    expect(document.schema_version).toBe('1.1')
    expect(document.workload).toMatchObject({ input_lengths: [1024, 8192], target_ratios: [0] })
    expect(document.load).toMatchObject({ points: [{ concurrency: 1 }], output_token_limits: [128, 512] })
    expect(options).toMatchObject({ offset: 0, limit: 100 })
    expect(screen.getByText('250')).toBeInTheDocument()
    expect(screen.getByText('Page 1 of 3')).toBeInTheDocument()

    const start = screen.getByRole('button', { name: 'Start matrix' })
    expect(start).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /cache_reuse/ }))
    expect(start).toBeEnabled()

    await act(async () => { fireEvent.click(start) })
    const [planText, taskId, submitOptions] = vi.mocked(submitPerfPlan).mock.calls[0]
    if (!submitOptions) throw new Error('submit options were not sent')
    expect(planText).toBe(content)
    expect(taskId).toMatch(/^perf_matrix_/)
    expect(submitOptions).toMatchObject({
      acknowledged: ['cache_reuse'],
      expectedPlanHash: 'planhash',
      runtime: { api_key: 'model-secret' },
    })
    expect(submitOptions.runtime).not.toHaveProperty('telemetry')
    expect(screen.getByText('Matrix accepted (queued).')).toBeInTheDocument()
    expect(submitted).toHaveBeenCalledWith('perf_matrix_1')
  })

  it('pages the compiled runs instead of truncating them', async () => {
    await act(async () => { render(<Harness />) })
    expect(screen.getByText(/AIPerf 0.13.0: Available/)).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Preview matrix' })) })
    expect(screen.getAllByRole('row')).toHaveLength(101)
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    vi.mocked(previewPerfPlan).mockResolvedValue(preview({ offset: 100, runs: Array.from({ length: 100 }, (_, index) => run(index + 101)) }))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Next page' })) })
    expect(vi.mocked(previewPerfPlan).mock.calls[1][1]).toMatchObject({ offset: 100, limit: 100 })
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Next page' })) })
    expect(vi.mocked(previewPerfPlan).mock.calls[2][1]).toMatchObject({ offset: 200 })
  })

  it('invalidates a preview when an axis changes and reports a bad list on its own field', async () => {
    await act(async () => { render(<Harness />) })
    expect(screen.getByText(/AIPerf 0.13.0: Available/)).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Preview matrix' })) })
    expect(screen.getByRole('button', { name: 'Start matrix' })).toBeEnabled()

    fireEvent.change(screen.getByLabelText(/Input lengths/), { target: { value: '1024, 4096' } })
    expect(screen.getByText('Inputs changed; preview the matrix again.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start matrix' })).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/Input lengths/), { target: { value: '1.5' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Preview matrix' })) })
    expect(vi.mocked(previewPerfPlan)).toHaveBeenCalledTimes(1)
    const input = screen.getByLabelText(/Input lengths/)
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(within(input.closest('div')!).getByText('Use positive whole numbers only.')).toBeInTheDocument()
  })

  it('reveals monitoring inputs only when enabled and locks the pool without a reuse ratio', async () => {
    await act(async () => { render(<Harness />) })
    expect(screen.getByText(/AIPerf 0.13.0: Available/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Metrics sampling interval/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add node' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Target prefix reuse/)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/Shared prefix count/)).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Scenario/), { target: { value: 'cache_comparison' } })
    expect(screen.getByLabelText(/Shared prefix count/)).toBeEnabled()
    expect(screen.getByLabelText(/Target prefix reuse/)).toHaveValue('0, 80, 90')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Collect node metrics' }))
    expect(screen.getByLabelText(/Metrics sampling interval/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add node' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Require node metrics' })).toBeInTheDocument()
  })

  it('clears the load points when the pacing unit changes', async () => {
    await act(async () => { render(<Harness />) })
    expect(screen.getByText(/AIPerf 0.13.0: Available/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Concurrency points/)).toHaveValue('1')
    fireEvent.change(screen.getByLabelText(/Load mode/), { target: { value: 'open_loop' } })
    // A concurrency must never be resubmitted as a request rate.
    expect(screen.getByLabelText(/Request rates per second/)).toHaveValue('')
  })

  it('exports the plan text without any runtime credential', async () => {
    vi.mocked(exportPerfPlan).mockResolvedValue({
      can_submit: true, content: '{"ok":true}', normalized_hash: 'normalized', plan_hash: 'planhash',
      semantic_fingerprint: 'fingerprint',
    })
    await act(async () => { render(<Harness />) })
    expect(screen.getByText(/AIPerf 0.13.0: Available/)).toBeInTheDocument()
    const createObjectURL = vi.fn(() => 'blob:plan')
    const revokeObjectURL = vi.fn()
    Object.assign(URL, { createObjectURL, revokeObjectURL })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Export plan' })) })
    expect(vi.mocked(exportPerfPlan).mock.calls[0][0]).not.toContain('model-secret')
    expect(vi.mocked(exportPerfPlan).mock.calls[0][0]).toContain('"schema_version": "1.1"')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:plan')
  })
})
