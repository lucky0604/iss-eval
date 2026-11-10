import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { ReportsProvider } from '@/contexts/ReportsContext'
import { getCacheExperiment, getCacheExperimentSummary, getCacheNodeMetrics, getCacheRequests, getCacheScenario, getCacheRepetitions } from '@/api/perf'
import CacheExperimentPage from './CacheExperimentPage'
import type {
  CacheExperimentResponse,
  CacheExperimentState,
  CacheExperimentSummaryResponse,
  CacheRequestsResponse,
  CacheScenarioResponse,
  CacheSubrunPlan,
} from '@/api/generated/contracts'

vi.mock('@/api/perf', () => ({
  getCacheExperiment: vi.fn(), getCacheExperimentSummary: vi.fn(), getCacheNodeMetrics: vi.fn(),
  getCacheRequests: vi.fn(), getCacheScenario: vi.fn(), getCacheRepetitions: vi.fn(),
  getCacheExperimentReportUrl: vi.fn(() => '/api/v1/perf/experiments/task/report'),
}))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('shows target, generated reuse and unknown server cache separately', async () => {
  vi.mocked(getCacheExperiment).mockResolvedValue({ task_id: 'task', plan: {
    plan_hash: 'hash', configuration_hash: 'config', tokenizer_id: 'tokenizer', template_id: 'template',
    primer_requests: 1, profiling_requests: 2, subruns: [{ subrun_id: 'r000-l000-n000', order: 0,
      target_ratio: 0.8, load_mode: 'closed_loop', load_point: { concurrency: 1 }, repetition: 0,
      seed: 42, namespace: 'ns', request_count: 2, primer_requests: 1 }],
  }, state: { schema_version: '1.0', plan_hash: 'hash', status: 'error', quality: 'invalid',
    completed_subruns: 0, total_subruns: 1, subruns: [{ subrun_id: 'r000-l000-n000', status: 'error',
      scenario: 'runs/r000-l000-n000/scenario.json' }] } })
  vi.mocked(getCacheExperimentSummary).mockResolvedValue({
    task_id: 'task', plan_hash: 'hash', configuration_hash: 'config', observed_at: '2026-11-09T00:00:00+00:00',
    revision: 1, execution_status: 'error', quality: 'invalid', total_planned_subruns: 1,
    counts_by_status: { error: 1 }, counts_by_quality: {}, readable_subruns: 0, missing_subruns: 0,
    unreadable_subruns: 1, coverage: { planned: 1, completed: 0, readable: 0, included: 0 }, groups: [{
      group_key: '["closed_loop",1,null,0.8,null,null]', load_mode: 'closed_loop', concurrency: 1, rate: null,
      target_ratio: 0.8, input_tokens: null, output_token_limit: null, total_subruns: 1, readable_subruns: 0,
      status_counts: { error: 1 }, planned_requests: 2, observed_requests: 0, successful_requests: 0,
    }], completeness: 'partial', completeness_reason: 'unreadable_subruns', availability: 'available',
  })
  vi.mocked(getCacheScenario).mockResolvedValue({ subrun_id: 'r000-l000-n000', status: 'error', target_ratio: 0.8,
    generated_ratio: 0.75, generated_count: 2, quality_reasons: ['profiling_failed'],
    cache: { availability: 'unavailable', ratio: null, observed_requests: 0, successful_requests: 2 } })
  vi.mocked(getCacheRepetitions).mockResolvedValue({ plan_hash: 'hash', total: 1, offset: 0, limit: 100, repetitions: [{ subrun_id: 'r000-l000-n000', repetition: 0, status: 'error' }] })
  vi.mocked(getCacheNodeMetrics).mockResolvedValue({ total: 0, offset: 0, limit: 500, samples: [] })
  vi.mocked(getCacheRequests).mockResolvedValue({ total: 1, offset: 0, limit: 50, requests: [{ index: 0,
    phase: 'profiling', status: 'success', cached_tokens: 0, cached_tokens_source: 'server_usage' }] })
  await act(async () => render(<LocaleProvider><ReportsProvider><MemoryRouter initialEntries={['/perf-experiment/task']}><Routes>
    <Route path="/perf-experiment/:taskId" element={<CacheExperimentPage />} />
  </Routes></MemoryRouter></ReportsProvider></LocaleProvider>))
  expect(screen.getByText('80%')).toBeInTheDocument()
  expect(screen.getByText('75%')).toBeInTheDocument()
  expect(screen.getByText('0/2')).toBeInTheDocument()
  // Execution status and data quality stay separate labels in the shared header.
  expect(screen.getByText('Failed')).toBeInTheDocument()
  expect(screen.getByText('Invalid')).toBeInTheDocument()
  expect(screen.getByText('Cache experiment')).toBeInTheDocument()
  expect(screen.getByText('Completed subruns')).toBeInTheDocument()
  expect(screen.getAllByText('0/1').length).toBeGreaterThan(0)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'r000-l000-n000' })) })
  expect(screen.getByText(/profiling_failed/)).toBeInTheDocument()
  expect(screen.getByText('Raw node samples (semantics unverified)')).toBeInTheDocument()
  expect(screen.getByText('Cached tokens (server)')).toBeInTheDocument()
})

function subrun(index: number): CacheSubrunPlan {
  return {
    subrun_id: `r${String(index).padStart(3, '0')}-l000-n000`, order: index, target_ratio: 0.8,
    load_mode: 'closed_loop', load_point: { concurrency: 1 }, repetition: 0, seed: 42,
    namespace: `ns-${index}`, request_count: 10, primer_requests: 1,
  }
}

function experimentOf(rows: CacheSubrunPlan[], status: CacheExperimentState['status'] = 'completed'): CacheExperimentResponse {
  return {
    task_id: 'task',
    plan: {
      plan_hash: 'hash', configuration_hash: 'config', tokenizer_id: 'tokenizer', template_id: 'template',
      primer_requests: rows.length, profiling_requests: rows.length * 10, subruns: rows,
    },
    state: {
      schema_version: '1.0', plan_hash: 'hash', status, completed_subruns: rows.length, total_subruns: rows.length,
      subruns: rows.map((row) => ({ subrun_id: row.subrun_id, status: 'completed',
        scenario: `runs/${row.subrun_id}/scenario.json` })),
    },
  }
}

function summaryOf(rows: CacheSubrunPlan[], overrides: Partial<CacheExperimentSummaryResponse> = {}): CacheExperimentSummaryResponse {
  return {
    task_id: 'task', plan_hash: 'hash', observed_at: '2026-11-09T00:00:00+00:00', revision: 1,
    execution_status: 'completed', quality: 'complete', total_planned_subruns: rows.length,
    counts_by_status: { completed: rows.length }, counts_by_quality: { complete: rows.length },
    readable_subruns: rows.length, missing_subruns: 0, unreadable_subruns: 0,
    coverage: { planned: rows.length, completed: rows.length, readable: rows.length, included: rows.length },
    groups: [{
      group_key: 'group', load_mode: 'closed_loop', concurrency: 1, rate: null, target_ratio: 0.8,
      input_tokens: null, output_token_limit: null, total_subruns: rows.length, readable_subruns: rows.length,
      status_counts: { completed: rows.length }, planned_requests: rows.length * 10,
      observed_requests: rows.length * 10, successful_requests: rows.length * 10,
    }],
    completeness: 'complete', availability: 'available', ...overrides,
  }
}

function scenarioOf(subrunId: string): CacheScenarioResponse {
  return { subrun_id: subrunId, status: 'completed', target_ratio: 0.8, generated_count: 10, quality_reasons: [] }
}

function requestsOf(requestId: string, index: number): CacheRequestsResponse {
  return { total: 1, offset: 0, limit: 50, requests: [{ index, phase: 'profiling', status: 'success', request_id: requestId }] }
}

function renderPage(entry = '/perf-experiment/task') {
  return render(<LocaleProvider><ReportsProvider><MemoryRouter initialEntries={[entry]}><Routes>
    <Route path="/perf-experiment/:taskId" element={<CacheExperimentPage />} />
  </Routes></MemoryRouter></ReportsProvider></LocaleProvider>)
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

// The suite runs on fake timers with a fixed clock, so every asynchronous
// assertion flushes the queued microtasks explicitly instead of waiting on
// wall-clock time.
async function flush() {
  await act(async () => { await vi.advanceTimersByTimeAsync(20) })
}

it('keeps the plan-wide summary stable while the matrix pages', async () => {
  const rows = Array.from({ length: 60 }, (_, index) => subrun(index))
  vi.mocked(getCacheExperiment).mockResolvedValue(experimentOf(rows))
  vi.mocked(getCacheExperimentSummary).mockResolvedValue(summaryOf(rows))
  vi.mocked(getCacheNodeMetrics).mockResolvedValue({ total: 0, offset: 0, limit: 100, samples: [] })
  vi.mocked(getCacheScenario).mockImplementation(async (_task, subrunId) => scenarioOf(subrunId))
  renderPage()
  await flush()
  await flush()

  expect(screen.getAllByText('60/60').length).toBeGreaterThan(0)
  expect(screen.getAllByText('600').length).toBe(3)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Next' })) })
  await flush()
  expect(screen.getByText(/51–60 \/ 60/)).toBeInTheDocument()
  expect(screen.getAllByText('600').length).toBe(3)
  expect(getCacheExperimentSummary).toHaveBeenCalledTimes(1)
})

it('restores the selected subrun from the URL and drops unknown identities', async () => {
  const rows = [subrun(0), subrun(1)]
  vi.mocked(getCacheExperiment).mockResolvedValue(experimentOf(rows))
  vi.mocked(getCacheExperimentSummary).mockResolvedValue(summaryOf(rows))
  vi.mocked(getCacheNodeMetrics).mockResolvedValue({ total: 0, offset: 0, limit: 100, samples: [] })
  vi.mocked(getCacheRequests).mockResolvedValue(requestsOf('req-a', 0))
  vi.mocked(getCacheScenario).mockImplementation(async (_task, subrunId) => scenarioOf(subrunId))
  renderPage('/perf-experiment/task?subrun=r001-l000-n000')
  await flush()
  await flush()
  expect(screen.getByText(/Subrun detail/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'r001-l000-n000' })).toHaveAttribute('aria-pressed', 'true')
  expect(getCacheRequests).toHaveBeenCalledWith('task', 'r001-l000-n000', 0, './outputs', expect.anything())

  cleanup()
  vi.clearAllMocks()
  vi.mocked(getCacheExperiment).mockResolvedValue(experimentOf(rows))
  vi.mocked(getCacheExperimentSummary).mockResolvedValue(summaryOf(rows))
  vi.mocked(getCacheScenario).mockImplementation(async (_task, subrunId) => scenarioOf(subrunId))
  renderPage('/perf-experiment/task?subrun=missing')
  await flush()
  await flush()
  expect(screen.queryByText(/Subrun detail/)).not.toBeInTheDocument()
  expect(vi.mocked(getCacheScenario).mock.calls.some((call) => call[1] === 'missing')).toBe(false)
})

it('polls a running experiment and stops once it is terminal', async () => {
  const rows = [subrun(0)]
  vi.mocked(getCacheExperiment).mockResolvedValue(experimentOf(rows, 'running'))
  vi.mocked(getCacheExperimentSummary).mockResolvedValue(summaryOf(rows))
  vi.mocked(getCacheScenario).mockResolvedValue(scenarioOf('r000-l000-n000'))
  renderPage()
  await flush()
  expect(getCacheExperiment).toHaveBeenCalledTimes(1)

  await act(async () => { await vi.advanceTimersByTimeAsync(5100) })
  expect(getCacheExperiment).toHaveBeenCalledTimes(2)

  vi.mocked(getCacheExperiment).mockResolvedValue(experimentOf(rows, 'completed'))
  await act(async () => { await vi.advanceTimersByTimeAsync(5100) })
  const settled = vi.mocked(getCacheExperiment).mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(5100) })
  expect(getCacheExperiment).toHaveBeenCalledTimes(settled)
})

it('ignores a late response from the previously selected subrun', async () => {
  const rows = [subrun(0), subrun(1)]
  vi.mocked(getCacheExperiment).mockResolvedValue(experimentOf(rows))
  vi.mocked(getCacheExperimentSummary).mockResolvedValue(summaryOf(rows))
  vi.mocked(getCacheScenario).mockImplementation(async (_task, subrunId) => scenarioOf(subrunId))
  const slow = deferred<CacheRequestsResponse>()
  vi.mocked(getCacheRequests).mockImplementation((_task, subrunId) =>
    subrunId === 'r000-l000-n000' ? slow.promise : Promise.resolve(requestsOf('req-b', 1)))
  renderPage()
  await flush()
  await flush()
  const first = screen.getByRole('button', { name: 'r000-l000-n000' })
  const second = screen.getByRole('button', { name: 'r001-l000-n000' })
  expect(first).toBeEnabled()
  expect(second).toBeEnabled()
  await act(async () => { fireEvent.click(first) })
  await flush()
  expect(getCacheRequests).toHaveBeenCalledWith('task', 'r000-l000-n000', 0, './outputs', expect.anything())
  await act(async () => { fireEvent.click(second) })
  await flush()
  expect(screen.getByText(/req-b/)).toBeInTheDocument()

  await act(async () => { slow.resolve(requestsOf('req-a', 0)) })
  await flush()
  expect(screen.queryByText(/req-a/)).not.toBeInTheDocument()
})

it('retries a failed requests panel without reloading the experiment', async () => {
  const rows = [subrun(0)]
  vi.mocked(getCacheExperiment).mockResolvedValue(experimentOf(rows))
  vi.mocked(getCacheExperimentSummary).mockResolvedValue(summaryOf(rows))
  vi.mocked(getCacheScenario).mockResolvedValue(scenarioOf('r000-l000-n000'))
  vi.mocked(getCacheNodeMetrics).mockResolvedValue({ total: 0, offset: 0, limit: 100, samples: [] })
  vi.mocked(getCacheRequests).mockRejectedValueOnce(new Error('requests unavailable'))
    .mockResolvedValue(requestsOf('req-ok', 0))
  renderPage()
  await flush()
  await flush()
  const row = screen.getByRole('button', { name: 'r000-l000-n000' })
  expect(row).toBeEnabled()
  await act(async () => { fireEvent.click(row) })
  await flush()
  expect(screen.getByText('requests unavailable')).toBeInTheDocument()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry' })) })
  await flush()
  expect(screen.getByText(/req-ok/)).toBeInTheDocument()
  expect(getCacheExperiment).toHaveBeenCalledTimes(1)
})
