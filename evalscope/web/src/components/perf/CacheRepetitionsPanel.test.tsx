import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import type { CacheScenarioResponse, CacheSubrunPlan } from '@/api/generated/contracts'
import CacheRepetitionsPanel from './CacheRepetitionsPanel'

it('shows individual sample counts and excludes invalid or incompatible repetitions', () => {
  const base: CacheSubrunPlan = { subrun_id: 'a', order: 0, target_ratio: .8, load_mode: 'closed_loop', load_point: { concurrency: 1 }, repetition: 0, seed: 1, namespace: 'a', request_count: 10, primer_requests: 0 }
  const makeScenario = (id: string, value: number): CacheScenarioResponse => ({ subrun_id: id, status: 'completed', target_ratio: .8, generated_count: 10,
    result: { executor_version: '0.13.0', quality: 'complete', profiling_requests: 10, profiling_successes: 10, profiling_errors: 0,
      metrics: [{ key: 'time_to_first_token', source: 'summary', scope: 'request', unit: 's', value, count: 10 }] } })
  const b = makeScenario('b', 4)
  const c = makeScenario('c', 100)
  c.result!.quality = 'invalid'
  const d = makeScenario('d', 200)
  d.result!.metrics![0].source = 'other'
  render(<LocaleProvider><CacheRepetitionsPanel rows={[base, { ...base, subrun_id: 'b', repetition: 1 }, { ...base, subrun_id: 'c', repetition: 2 }, { ...base, subrun_id: 'd', repetition: 3 }]} scenarios={{ a: makeScenario('a', 2), b, c, d }} /></LocaleProvider>)
  expect(screen.getByText(/Comparable repetitions: 2 \/ 4/)).toHaveTextContent('2–4 s')
  expect(screen.getByText(/#1 · a: 2 s \(n=10/)).toBeInTheDocument()
  expect(screen.getByText(/#3 · c: —/)).toBeInTheDocument()
  expect(screen.getByText(/#4 · d: —/)).toBeInTheDocument()
})
