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

it('requires compatible credential-free experiment configuration across tasks', async () => {
  const { comparableConfigurations } = await import('./cacheComparison')
  const config = {
    task_id: 'one', model: 'model', url: 'http://server/v1', tokenizer_id: 'tok', template_id: 'template',
    tokenizer_path: '/first', stream: true, deployment_mode: 'pd' as const,
    telemetry: { required: false, sample_interval_seconds: 2, max_scrape_gap_seconds: null, targets: [] },
    cache: { target_ratios: [0, .8] as [number, ...number[]], total_input_tokens: 100, prefix_pool_size: 1 },
    load: { mode: 'closed_loop' as const, points: [{ concurrency: 1 }] as [{ concurrency: number }], repetitions: 1, request_count: 10, output_token_limit: 4 },
  }
  const other = { ...config, task_id: 'two', tokenizer_path: '/second', cache: { ...config.cache, target_ratios: [.9] as [number] }, load: { ...config.load, repetitions: 2, points: [{ concurrency: 2 }] as [{ concurrency: number }] } }
  expect(comparableConfigurations(config, other)).toBe(true)
  expect(comparableConfigurations(config, { ...other, model: 'other' })).toBe(false)
  expect(comparableConfigurations(config, { ...other, cache: { ...other.cache, total_input_tokens: 200 } })).toBe(false)
  expect(comparableConfigurations(config, { ...other, telemetry: { ...other.telemetry, required: true } })).toBe(false)
  expect(comparableConfigurations(config, null)).toBe(false)
  expect(comparableRuns(base, { ...base, target_ratio: .8 }, 'ratio', false)).toBe(true)
})
