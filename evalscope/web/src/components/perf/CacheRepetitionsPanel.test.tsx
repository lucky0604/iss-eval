import { act, fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import type { CacheRepetitionItem, CacheSubrunPlan } from '@/api/generated/contracts'
import { getCacheRepetitions } from '@/api/perf'
import CacheRepetitionsPanel from './CacheRepetitionsPanel'

vi.mock('@/api/perf', () => ({ getCacheRepetitions: vi.fn() }))

it('loads all repetitions across pages, excluding invalid or incompatible metrics from the range', async () => {
  const base: CacheSubrunPlan = { subrun_id: 'a', order: 0, target_ratio: .8, load_mode: 'closed_loop', load_point: { concurrency: 1 }, repetition: 0, seed: 1, namespace: 'a', request_count: 10, primer_requests: 0 }
  const entries: CacheRepetitionItem[] = Array.from({ length: 61 }, (_, index) => ({ subrun_id: `r${index}`, repetition: index, status: 'completed', quality: 'complete', engine: 'aiperf', executor_version: '0.13.0', metric: { key: 'time_to_first_token', source: 'summary', scope: 'request', unit: 's', value: index, count: 10 } }))
  entries[59].quality = 'invalid'
  entries[60].metric!.source = 'other'
  vi.mocked(getCacheRepetitions).mockImplementation(async (_task, _subrun, _metric, offset) => ({ plan_hash: 'hash', total: 61, offset, limit: 50, repetitions: entries.slice(offset, offset + 50) }))
  render(<LocaleProvider><CacheRepetitionsPanel taskId="task" planHash="hash" rows={[base]} /></LocaleProvider>)
  expect(getCacheRepetitions).not.toHaveBeenCalled()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Load all repetitions' })) })
  expect(screen.getByText('All repetitions loaded: 61 / 61')).toBeInTheDocument()
  expect(screen.getByText(/Comparable repetitions loaded: 59 \/ 61/)).toHaveTextContent('0–58 s')
  expect(screen.getByText(/#1 · r0: 0 s \(n=10/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  expect(screen.getByText(/#60 · r59: 59 s/)).toHaveTextContent('excluded from range')
  expect(screen.getByText(/#61 · r60: 60 s/)).toHaveTextContent('excluded from range')
  expect(getCacheRepetitions).toHaveBeenCalledTimes(2)
})

it('does not present an interrupted or inconsistent scan as complete', async () => {
  const base: CacheSubrunPlan = { subrun_id: 'a', order: 0, target_ratio: 0, load_mode: 'closed_loop', load_point: { concurrency: 1 }, repetition: 0, seed: 1, namespace: 'a', request_count: 10, primer_requests: 0 }
  vi.mocked(getCacheRepetitions).mockResolvedValue({ plan_hash: 'wrong', total: 1, offset: 0, limit: 100, repetitions: [] })
  render(<LocaleProvider><CacheRepetitionsPanel taskId="task" planHash="hash" rows={[base]} /></LocaleProvider>)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Load all repetitions' })) })
  expect(screen.getByRole('alert')).toHaveTextContent('Inconsistent repetition page')
  expect(screen.queryByText(/All repetitions loaded/)).not.toBeInTheDocument()
  vi.mocked(getCacheRepetitions).mockResolvedValue({ plan_hash: 'hash', total: 1, offset: 0, limit: 100, repetitions: [{ subrun_id: 'a', repetition: 0, status: 'error' }] })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Load all repetitions' })) })
  expect(screen.getByText('All repetitions loaded: 1 / 1')).toBeInTheDocument()
})

it('aborts an in-flight page when the panel is removed', async () => {
  const base: CacheSubrunPlan = { subrun_id: 'a', order: 0, target_ratio: 0, load_mode: 'closed_loop', load_point: { concurrency: 1 }, repetition: 0, seed: 1, namespace: 'a', request_count: 10, primer_requests: 0 }
  let signal: AbortSignal | undefined
  vi.mocked(getCacheRepetitions).mockImplementation((_task, _subrun, _metric, _offset, requestSignal) => {
    signal = requestSignal
    return new Promise(() => {})
  })
  const view = render(<LocaleProvider><CacheRepetitionsPanel taskId="task" planHash="hash" rows={[base]} /></LocaleProvider>)
  fireEvent.click(screen.getByRole('button', { name: 'Load all repetitions' }))
  expect(signal?.aborted).toBe(false)
  view.unmount()
  expect(signal?.aborted).toBe(true)
})
