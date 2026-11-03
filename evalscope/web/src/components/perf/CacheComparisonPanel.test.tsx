import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import type { CacheScenarioResponse, CacheSubrunPlan } from '@/api/generated/contracts'
import CacheComparisonPanel from './CacheComparisonPanel'

const base: CacheSubrunPlan = { subrun_id: 'a', order: 0, target_ratio: 0, load_mode: 'closed_loop',
  load_point: { concurrency: 1 }, repetition: 0, seed: 42, namespace: 'a', request_count: 10, primer_requests: 0, output_token_limit: 4 }
const scenario: CacheScenarioResponse = { subrun_id: 'a', status: 'completed', target_ratio: 0, generated_count: 10,
  result: { executor_version: '0.13.0', quality: 'partial', profiling_requests: 10, profiling_successes: 10, profiling_errors: 0,
    metrics: [{ key: 'ttft', source: 'summary', scope: 'request', unit: 's', value: 2, count: 10 }] } }

it('requires fixed dimensions and compatible metric provenance before showing a delta', () => {
  const candidate = { plan: { ...base, subrun_id: 'b', target_ratio: .8 }, scenario: structuredClone(scenario) }
  candidate.scenario.result!.metrics![0].value = 1
  const { rerender } = render(<LocaleProvider><CacheComparisonPanel baseline={{ plan: base, scenario }} candidate={candidate} /></LocaleProvider>)
  expect(screen.getByText('-50%')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Changed dimension'), { target: { value: 'load' } })
  expect(screen.getByText(/Choose two valid runs/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Changed dimension'), { target: { value: 'ratio' } })
  candidate.scenario.result!.metrics![0].source = 'different'
  rerender(<LocaleProvider><CacheComparisonPanel baseline={{ plan: base, scenario }} candidate={candidate} /></LocaleProvider>)
  expect(screen.queryByText('-50%')).not.toBeInTheDocument()
})
