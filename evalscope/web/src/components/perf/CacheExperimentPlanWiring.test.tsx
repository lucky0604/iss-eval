// S01: an accepted portable plan must reach the task tracker of the page that
// hosts the import panel. Before this wiring existed the panel created a task
// and the page kept showing an empty status area.

import { useState } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { DEFAULT_AIPERF, type AIPerfDraft } from '@/components/tasks/aiperfDraft'
import type { PerfPlanPreviewResponse } from '@/api/generated/contracts'
import CacheExperimentForm from './CacheExperimentForm'
import { previewPerfPlan, submitPerfPlan } from '@/api/perfPlans'
import { getPerfEngines } from '@/api/perf'

vi.mock('@/api/perf', () => ({
  getPerfEngines: vi.fn(),
  validatePerfExperiment: vi.fn(),
  validatePerfSingle: vi.fn(),
}))
vi.mock('@/api/perfPlans', () => ({
  previewPerfPlan: vi.fn(),
  exportPerfPlan: vi.fn(),
  submitPerfPlan: vi.fn(),
}))

const connection = {
  model: 'model',
  url: 'http://localhost:1234/v1',
  apiKey: 'secret',
  alias: '',
  api: 'openai' as const,
}

const preview = {
  plan_hash: 'plan-hash',
  normalized_hash: 'normalized',
  source_hash: 'source',
  semantic_fingerprint: 'semantic',
  contract_version: '1.0',
  compiler_version: '1.1',
  workload_semantics: 'managed_token_prefix',
  intent: 'diagnostic',
  normalized: { name: 'small plan' },
  runs: [],
  total_runs: 1,
  offset: 0,
  limit: 100,
  budgets: { formal_requests: 8, warmup_requests: 0, primer_requests: 1 },
  diagnostics: [],
  alignment: { overall: 'unknown', dimensions: [] },
  context: [],
  can_submit: true,
  requires_acknowledgement: [],
  acknowledged: [],
  engine_available: true,
  runtime: { runtime_status: 'available', runtime_version: '0.13.0' },
} as unknown as PerfPlanPreviewResponse

function Harness({ onPlanSubmitted }: { onPlanSubmitted: (taskId: string) => void }) {
  const [draft, setDraft] = useState<AIPerfDraft>(structuredClone(DEFAULT_AIPERF))
  return (
    <LocaleProvider>
      <CacheExperimentForm
        connection={connection}
        onSubmit={async () => {}}
        disabled={false}
        draft={draft}
        onChange={setDraft}
        onValidateConnection={() => ({})}
        onPlanSubmitted={onPlanSubmitted}
      />
    </LocaleProvider>
  )
}

beforeEach(() => {
  vi.mocked(getPerfEngines).mockResolvedValue({ engines: [{ engine: 'aiperf', available: true }] })
  vi.mocked(previewPerfPlan).mockResolvedValue(preview)
  vi.mocked(submitPerfPlan).mockResolvedValue({
    task_id: 'perf_plan_wired',
    state: 'queued',
    duplicate: false,
  } as never)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('forwards an accepted plan task id to the host page', async () => {
  const accepted = vi.fn()
  await act(async () => {
    render(<Harness onPlanSubmitted={accepted} />)
  })
  const file = new File([JSON.stringify({ schema_version: '1.0' })], 'plan.json', { type: 'application/json' })
  await act(async () => {
    fireEvent.change(screen.getByLabelText('Choose JSON file'), { target: { files: [file] } })
  })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Preview imported plan' }))
  })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Submit plan' }))
  })
  expect(accepted).toHaveBeenCalledWith('perf_plan_wired')
})
