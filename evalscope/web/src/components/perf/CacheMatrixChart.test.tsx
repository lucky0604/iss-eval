import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import type { CacheScenarioResponse, CacheSubrunPlan } from '@/api/generated/contracts'
import CacheMatrixChart from './CacheMatrixChart'

const base: CacheSubrunPlan = { subrun_id: 'a', order: 0, target_ratio: 0, load_mode: 'closed_loop',
  load_point: { concurrency: 1 }, repetition: 0, seed: 42, namespace: 'a', request_count: 2, primer_requests: 0 }
const result: CacheScenarioResponse = { subrun_id: 'a', status: 'completed', target_ratio: 0, generated_count: 2,
  result: { executor_version: '0.13.0', quality: 'partial', profiling_requests: 2, profiling_successes: 2, profiling_errors: 0,
    metrics: [{ key: 'time_to_first_token', source: 'aiperf', scope: 'request', unit: 's', value: 0, count: 2 }] } }

it('shows only observed points for the selected load and repetition, without joining gaps', () => {
  const other = { ...base, subrun_id: 'b', target_ratio: .9 }
  const different = { ...base, subrun_id: 'c', target_ratio: .8, load_point: { concurrency: 2 } }
  const scenarios = { a: result, b: { ...result, subrun_id: 'b', target_ratio: .9 },
    c: { ...result, subrun_id: 'c', target_ratio: .8 } }
  render(<LocaleProvider><CacheMatrixChart rows={[base, other, different]} scenarios={scenarios} /></LocaleProvider>)
  const chart = screen.getByRole('img', { name: 'Target ratio versus metric scatter plot' })
  expect(chart.querySelectorAll('circle')).toHaveLength(2)
  expect(chart.querySelectorAll('polyline')).toHaveLength(0)
  fireEvent.change(screen.getByLabelText('Load / repetition'), { target: { value: 'closed_loop:2:0' } })
  expect(chart.querySelectorAll('circle')).toHaveLength(1)
})
