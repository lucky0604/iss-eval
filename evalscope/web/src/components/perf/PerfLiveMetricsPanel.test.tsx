import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { getLivePerfMetrics } from '@/api/perf'
import type { PerfLiveResponse } from '@/api/generated/contracts'
import PerfLiveMetricsPanel from './PerfLiveMetricsPanel'

vi.mock('@/api/perf', () => ({ getLivePerfMetrics: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

const sample: PerfLiveResponse = { task_id: 'task', current_subrun: 'r000-l000-n000', target_ratio: 0.8,
  load_mode: 'closed_loop', concurrency: 1, repetition: 0, expected_interval_seconds: 1,
  nodes: [{ node_id: 'p', role: 'prefill', engine: 'vllm', status: 'available',
    sampled_at_ns: Date.parse('2026-07-01T00:00:00Z') * 1_000_000,
    gauges: [{ name: 'vllm:num_requests_waiting', key: 'waiting_requests', labels: { model: 'a' }, value: 0, unit: 'requests' }] }] }

it('shows actual zero, scenario and stale data without inventing throughput', async () => {
  vi.mocked(getLivePerfMetrics).mockResolvedValueOnce(sample).mockRejectedValue(new Error('network'))
  await act(async () => { render(<LocaleProvider><PerfLiveMetricsPanel taskId="task" active /></LocaleProvider>) })
  expect(screen.getByText('0 requests')).toBeInTheDocument()
  expect(screen.getByText(/Target reuse: 80%/)).toBeInTheDocument()
  expect(screen.getByText('Sent / completed / measured throughput: —')).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(screen.getAllByText('Last sample is stale or unavailable').length).toBeGreaterThan(0)
  expect(screen.getByText('0 requests')).toBeInTheDocument()
})

it('aborts in-flight work when hidden and does not publish a late result', async () => {
  let resolve!: (value: PerfLiveResponse) => void
  vi.mocked(getLivePerfMetrics).mockImplementation(() => new Promise((done) => { resolve = done }))
  const view = render(<LocaleProvider><PerfLiveMetricsPanel taskId="task" active /></LocaleProvider>)
  const signal = vi.mocked(getLivePerfMetrics).mock.calls[0][1]!
  view.rerender(<LocaleProvider><PerfLiveMetricsPanel taskId="task" active={false} /></LocaleProvider>)
  expect(signal.aborted).toBe(true)
  await act(async () => { resolve(sample); await vi.advanceTimersByTimeAsync(15000) })
  expect(screen.queryByText('0 requests')).not.toBeInTheDocument()
  expect(getLivePerfMetrics).toHaveBeenCalledTimes(1)
})

it('graphs distinct observed queue samples and marks a stopped scrape stale', async () => {
  const second = structuredClone(sample)
  second.nodes![0].sampled_at_ns! += 5_000_000_000
  second.nodes![0].gauges![0]!.value = 2
  vi.mocked(getLivePerfMetrics).mockResolvedValueOnce(sample).mockResolvedValue(second)
  await act(async () => { render(<LocaleProvider><PerfLiveMetricsPanel taskId="task" active /></LocaleProvider>) })
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(screen.getByRole('img', { name: 'Waiting requests trend' })).toBeInTheDocument()
  expect(screen.getByText('2 requests')).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
  expect(screen.getByText(/Last sample is stale or unavailable/)).toBeInTheDocument()
})

it('marks a hanging request stale and retries without discarding the last sample', async () => {
  vi.mocked(getLivePerfMetrics).mockResolvedValueOnce(sample).mockImplementation(() => new Promise(() => {}))
  await act(async () => { render(<LocaleProvider><PerfLiveMetricsPanel taskId="task" active /></LocaleProvider>) })
  await act(async () => { await vi.advanceTimersByTimeAsync(13_000) })
  expect(screen.getAllByText(/Last sample is stale or unavailable/).length).toBeGreaterThan(0)
  expect(screen.getByText('0 requests')).toBeInTheDocument()
  expect(vi.mocked(getLivePerfMetrics).mock.calls[1][1]?.aborted).toBe(true)
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(getLivePerfMetrics).toHaveBeenCalledTimes(3)
})
