// Component tests for the portable-plan report page.
//
// The page must show the *planned* matrix, not the first run's arguments: a
// partially executed plan has to render one row per planned run with its own
// input length, concurrency and repetition, and unfinished rows stay visible as
// `not_started`. Declared deployment facts and effective identity are kept
// apart so a declared fact is never presented as a measured one.

import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import PerfPlanPage from './PerfPlanPage'
import { LocaleProvider } from '@/contexts/LocaleContext'
import type { PerfPlanArchiveResponse } from '@/api/generated/contracts'
import { getPerfPlanArchiveAt } from '@/api/perfPlans'

vi.mock('@/api/perfPlans', () => ({
  getPerfPlanArchive: vi.fn(),
  getPerfPlanArchiveAt: vi.fn(),
  getPerfPlanRunRequests: vi.fn(),
  perfPlanReportUrl: (rootPath: string, path: string) =>
    `/api/v1/perf/plans/report?root_path=${encodeURIComponent(rootPath)}&path=${encodeURIComponent(path)}`,
}))

const run = (overrides: Record<string, unknown>) => ({
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
  concurrency: 1,
  formal_requests: 16,
  primer_requests: 1,
  output_token_limit: 32,
  repetition: 1,
  status: 'not_started',
  quality: null,
  reason: null,
  normalized: null,
  requests_available: false,
  cache_verification: null,
  started_ns: null,
  finished_ns: null,
  ...overrides,
})

const archive = {
  task_id: 'plan-task',
  model: 'served-model',
  workload_semantics: 'managed_token_prefix',
  contract_version: '1.0',
  compiler_version: '1.1',
  plan_hash: 'a'.repeat(64),
  normalized_hash: 'b'.repeat(64),
  source_hash: 'c'.repeat(64),
  semantic_fingerprint: 'd'.repeat(64),
  state: 'stopped',
  state_reason: null,
  status: 'stopped',
  quality: 'partial',
  quality_reasons: ['i001-r000-l001-n000:partial'],
  phase: null,
  current_run: null,
  completed_runs: 1,
  total_runs: 3,
  budgets: { formal_requests: 48, warmup_requests: 0, primer_requests: 3 },
  context: [],
  warmup: null,
  runs: [
    run({ status: 'completed', quality: 'partial' }),
    run({ run_id: 'i001-r000-l000-n000', order: 1, input_tokens: 2048, concurrency: 4, repetition: 2 }),
    run({ run_id: 'i001-r000-l001-n000', order: 2, input_tokens: 2048, concurrency: 8, repetition: 2 }),
  ],
  offset: 0,
  limit: 100,
  archive: {},
  alignment: null,
  effective: {
    schema_version: '1.0',
    identity_version: '1',
    plan_hash: 'a'.repeat(64),
    parent_plan_hash: null,
    contract_version: '1.0',
    compiler_version: '1.1',
    tokenizer: { status: 'resolved', content_hash: 'tok-hash', template_hash: 'tpl-hash', path: '/models/tokenizer' },
    aiperf_version: '0.13.0',
    executor: 'evalscope_service',
    deployment_evidence: 'declared',
  },
  report_available: true,
} as unknown as PerfPlanArchiveResponse

async function renderPage(entry: string) {
  await act(async () => {
    render(
      <LocaleProvider>
        <MemoryRouter initialEntries={[entry]}>
          <Routes>
            <Route path="/perf-plan" element={<PerfPlanPage />} />
            <Route path="/perf-plan/:taskId" element={<PerfPlanPage />} />
          </Routes>
        </MemoryRouter>
      </LocaleProvider>,
    )
    await Promise.resolve()
  })
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('renders every planned run with its own dimensions and unfinished state', async () => {
  vi.mocked(getPerfPlanArchiveAt).mockResolvedValue(archive)
  await renderPage('/perf-plan?root_path=%2Ftmp%2Foutputs&path=plan-task%2Fperf')

  expect(screen.getAllByText('partial').length).toBeGreaterThan(0)
  expect(screen.getByText('1/3')).toBeDefined()
  expect(screen.getAllByText('2048')).toHaveLength(2)
  expect(screen.getAllByText('not_started').length).toBe(2)
  expect(screen.getAllByText('partial').length).toBeGreaterThan(0)
  expect(screen.getByText(/i001-r000-l001-n000:partial/)).toBeDefined()
})

it('shows declared and observed reuse per run and keeps history explicit', async () => {
  const withCache = {
    ...archive,
    runs: [
      run({
        status: 'completed',
        quality: 'partial',
        cache_verification: {
          declared_ratio: 0.9,
          declared_source: 'target_prefix_ratio',
          observed_ratio: 0.6,
          delta: -0.3,
          tolerance: 0.05,
          required: true,
          status: 'mismatch',
        },
      }),
      run({
        run_id: 'i001-r000-l000-n000',
        order: 1,
        cache_verification: {
          declared_ratio: 0.9,
          declared_source: 'target_prefix_ratio',
          observed_ratio: 0,
          delta: -0.9,
          tolerance: 0.1,
          required: false,
          status: 'verified',
        },
      }),
      run({ run_id: 'i001-r000-l001-n000', order: 2, cache_verification: null }),
    ],
  } as unknown as PerfPlanArchiveResponse
  vi.mocked(getPerfPlanArchiveAt).mockResolvedValue(withCache)
  await renderPage('/perf-plan?root_path=%2Ftmp%2Foutputs&path=plan-task%2Fperf')

  expect(screen.getByText('mismatch')).toBeDefined()
  expect(screen.getByText(/90\.0% → 60\.0% \(-30\.0 pp\)/)).toBeDefined()
  expect(screen.getByText(/±5\.0 pp/)).toBeDefined()
  expect(screen.getByText(/observation required/)).toBeDefined()
  expect(screen.getByText(/observation optional/)).toBeDefined()
  expect(screen.getByText(/90\.0% → 0\.0%/)).toBeDefined()
  expect(screen.getByText('History: not recorded')).toBeDefined()
  expect(screen.getByText(/not that every replica is warm/)).toBeDefined()
})

it('separates requested, effective and declared facts', async () => {
  vi.mocked(getPerfPlanArchiveAt).mockResolvedValue(archive)
  await renderPage('/perf-plan?root_path=%2Ftmp%2Foutputs&path=plan-task%2Fperf')

  expect(screen.getByText('tok-hash')).toBeDefined()
  expect(screen.getByText('tpl-hash')).toBeDefined()
  expect(screen.getByText('0.13.0')).toBeDefined()
  expect(screen.getByText('declared')).toBeDefined()
  expect(screen.getByText(/Declared deployment facts are not measured facts/)).toBeDefined()
  const report = screen.getByRole('link', { name: 'Open the standalone HTML report' })
  expect(report.getAttribute('href')).toContain('/api/v1/perf/plans/report')
})
