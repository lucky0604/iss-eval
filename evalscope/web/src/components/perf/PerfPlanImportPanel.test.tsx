import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { DomainError } from '@/api/errors'
import type { PerfPlanPreviewResponse } from '@/api/generated/contracts'
import PerfPlanImportPanel from './PerfPlanImportPanel'
import { previewPerfPlan, submitPerfPlan } from '@/api/perfPlans'

vi.mock('@/api/perfPlans', () => ({
  previewPerfPlan: vi.fn(),
  exportPerfPlan: vi.fn(),
  submitPerfPlan: vi.fn(),
}))

const PLAN_TEXT = JSON.stringify({ kind: 'iss-eval.perf-plan', schema_version: '1.0' })

const preview = (hash = 'plan-hash-a'): PerfPlanPreviewResponse =>
  ({
    schema_version: '1.0',
    contract_version: '1.0',
    compiler_version: '1.1',
    workload_semantics: 'managed_token_prefix',
    intent: 'diagnostic',
    plan_hash: hash,
    normalized_hash: 'normalized',
    source_hash: 'source',
    semantic_fingerprint: 'semantic',
    normalized: { name: 'small plan' },
    runs: [],
    total_runs: 2,
    offset: 0,
    limit: 100,
    budgets: { formal_requests: 32, warmup_requests: 0, primer_requests: 1 },
    diagnostics: [],
    alignment: { overall: 'unknown', dimensions: [] },
    context: [],
    can_submit: true,
    requires_acknowledgement: [],
    acknowledged: [],
    engine_available: true,
    runtime: { runtime_status: 'available', runtime_version: '0.13.0' },
  }) as unknown as PerfPlanPreviewResponse

function renderPanel(onSubmitted = vi.fn()) {
  render(
    <LocaleProvider>
      <PerfPlanImportPanel onSubmitted={onSubmitted} />
    </LocaleProvider>,
  )
  return onSubmitted
}

async function load(text: string) {
  const input = screen.getByLabelText('Choose JSON file')
  const file = new File([text], 'plan.json', { type: 'application/json' })
  await act(async () => {
    fireEvent.change(input, { target: { files: [file] } })
  })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Preview imported plan' }))
  })
}

async function submit() {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Submit plan' }))
  })
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('submits the previewed plan hash and reports the accepted task', async () => {
  vi.mocked(previewPerfPlan).mockResolvedValue(preview())
  vi.mocked(submitPerfPlan).mockResolvedValue({
    task_id: 'perf_plan_task',
    state: 'queued',
    duplicate: false,
  } as never)
  const onSubmitted = renderPanel()
  await load(PLAN_TEXT)
  await submit()

  const [, taskId, options] = vi.mocked(submitPerfPlan).mock.calls[0]
  expect(taskId).toMatch(/^perf_plan_/)
  expect(options?.expectedPlanHash).toBe('plan-hash-a')
  expect(onSubmitted).toHaveBeenCalledWith('perf_plan_task')
  expect(screen.getByRole('status').textContent).toContain('queued')
})

it('retries an uncertain submission with the same task id', async () => {
  vi.mocked(previewPerfPlan).mockResolvedValue(preview())
  vi.mocked(submitPerfPlan).mockRejectedValueOnce(new DomainError('network', 'connection reset'))
  vi.mocked(submitPerfPlan).mockResolvedValueOnce({ task_id: 'perf_plan_task', state: 'queued', duplicate: false } as never)
  renderPanel()
  await load(PLAN_TEXT)
  await submit()
  expect(screen.getByRole('status').textContent).toContain('same task ID')

  await submit()
  const first = vi.mocked(submitPerfPlan).mock.calls[0][1]
  const second = vi.mocked(submitPerfPlan).mock.calls[1][1]
  expect(second).toBe(first)
})

it('starts a new task after a definite rejection and after success', async () => {
  vi.mocked(previewPerfPlan).mockResolvedValue(preview())
  vi.mocked(submitPerfPlan).mockRejectedValueOnce(new DomainError('http-4xx', 'Task ID already exists', 409))
  vi.mocked(submitPerfPlan).mockResolvedValueOnce({ task_id: 'perf_plan_one', state: 'queued', duplicate: false } as never)
  vi.mocked(submitPerfPlan).mockResolvedValueOnce({
    task_id: 'perf_plan_one',
    state: 'completed',
    duplicate: true,
  } as never)
  renderPanel()
  await load(PLAN_TEXT)

  await submit()
  await submit()
  const [rejected, accepted] = vi.mocked(submitPerfPlan).mock.calls.map((call) => call[1])
  expect(accepted).not.toBe(rejected)

  // A third click after acceptance is an explicit new submission, so it rotates again.
  await submit()
  const ids = vi.mocked(submitPerfPlan).mock.calls.map((call) => call[1])
  expect(new Set(ids).size).toBe(3)
  expect(screen.getByRole('status').textContent).toContain('completed')
})
