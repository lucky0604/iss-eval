import { expect, it } from 'vitest'
import type { CacheSubrunPlan, MetricStatistics } from '@/api/generated/contracts'
import { comparableMetric, comparableRuns, relativeChange } from './cacheComparison'

const base: CacheSubrunPlan = { subrun_id: 'r000-l000-n000', order: 0, target_ratio: 0, load_mode: 'closed_loop',
  load_point: { concurrency: 2 }, repetition: 0, seed: 42, namespace: 'a', request_count: 10, primer_requests: 0, output_token_limit: 5 }
const metric: MetricStatistics = { key: 'time_to_first_token', source: 'aiperf_summary', scope: 'request', unit: 's', value: 1 }

it('compares only the chosen primary dimension while fixing load and repetition', () => {
  const ratio = { ...base, subrun_id: 'r001-l000-n000', target_ratio: .8 }
  expect(comparableRuns(base, ratio, 'ratio')).toBe(true)
  expect(comparableRuns(base, { ...ratio, repetition: 1 }, 'ratio')).toBe(false)
  expect(comparableRuns(base, { ...ratio, load_point: { concurrency: 4 } }, 'ratio')).toBe(false)
  expect(comparableRuns(base, { ...base, subrun_id: 'r000-l001-n000', load_point: { concurrency: 4 } }, 'load')).toBe(true)
})

it('rejects source, scope or unit mismatch and keeps zero-baseline change unknown', () => {
  expect(comparableMetric(metric, { ...metric, value: 2 })).toBe(true)
  expect(comparableMetric(metric, { ...metric, scope: 'node_window' })).toBe(false)
  expect(comparableMetric(metric, { ...metric, source: 'proxy' })).toBe(false)
  expect(comparableMetric(metric, { ...metric, unit: 'ms' })).toBe(false)
  expect(relativeChange(0, 3)).toBeNull()
  expect(relativeChange(2, 3)).toBe(.5)
})
