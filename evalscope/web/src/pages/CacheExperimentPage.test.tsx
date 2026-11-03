import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { getCacheExperiment, getCacheNodeMetrics, getCacheRequests, getCacheScenario, getCacheRepetitions } from '@/api/perf'
import CacheExperimentPage from './CacheExperimentPage'

vi.mock('@/api/perf', () => ({ getCacheExperiment: vi.fn(), getCacheNodeMetrics: vi.fn(), getCacheRequests: vi.fn(), getCacheScenario: vi.fn(), getCacheRepetitions: vi.fn() }))
afterEach(cleanup)

it('shows target, generated reuse and unknown server cache separately', async () => {
  vi.mocked(getCacheExperiment).mockResolvedValue({ task_id: 'task', plan: {
    plan_hash: 'hash', configuration_hash: 'config', tokenizer_id: 'tokenizer', template_id: 'template',
    primer_requests: 1, profiling_requests: 2, subruns: [{ subrun_id: 'r000-l000-n000', order: 0,
      target_ratio: 0.8, load_mode: 'closed_loop', load_point: { concurrency: 1 }, repetition: 0,
      seed: 42, namespace: 'ns', request_count: 2, primer_requests: 1 }],
  }, state: { schema_version: '1.0', plan_hash: 'hash', status: 'error', quality: 'invalid',
    completed_subruns: 0, total_subruns: 1, subruns: [{ subrun_id: 'r000-l000-n000', status: 'error',
      scenario: 'runs/r000-l000-n000/scenario.json' }] } })
  vi.mocked(getCacheScenario).mockResolvedValue({ subrun_id: 'r000-l000-n000', status: 'error', target_ratio: 0.8,
    generated_ratio: 0.75, generated_count: 2, quality_reasons: ['profiling_failed'],
    cache: { availability: 'unavailable', ratio: null, observed_requests: 0, successful_requests: 2 } })
  vi.mocked(getCacheRepetitions).mockResolvedValue({ plan_hash: 'hash', total: 1, offset: 0, limit: 100, repetitions: [{ subrun_id: 'r000-l000-n000', repetition: 0, status: 'error' }] })
  vi.mocked(getCacheNodeMetrics).mockResolvedValue({ total: 0, offset: 0, limit: 500, samples: [] })
  vi.mocked(getCacheRequests).mockResolvedValue({ total: 1, offset: 0, limit: 50, requests: [{ index: 0,
    phase: 'profiling', status: 'success', cached_tokens: 0, cached_tokens_source: 'server_usage' }] })
  await act(async () => render(<LocaleProvider><MemoryRouter initialEntries={['/perf-experiment/task']}><Routes>
    <Route path="/perf-experiment/:taskId" element={<CacheExperimentPage />} />
  </Routes></MemoryRouter></LocaleProvider>))
  expect(screen.getByText('80%')).toBeInTheDocument()
  expect(screen.getByText('75%')).toBeInTheDocument()
  expect(screen.getByText('0/2')).toBeInTheDocument()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'r000-l000-n000' })) })
  expect(screen.getByText(/profiling_failed/)).toBeInTheDocument()
  expect(screen.getByText('Raw node samples (semantics unverified)')).toBeInTheDocument()
  expect(screen.getByText('Cached tokens (server)')).toBeInTheDocument()
})
