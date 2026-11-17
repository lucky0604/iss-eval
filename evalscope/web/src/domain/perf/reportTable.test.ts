import { describe, expect, it } from 'vitest'
import {
  REPORT_COLUMNS,
  columnsForPreset,
  contiguousGroups,
  formatColumnValue,
  formatNumber,
  loadModeOf,
  loadSortKey,
  loadValue,
  nextSort,
  successRate,
} from './reportTable'
import { makeDistribution, makeRun } from '@/test/fixtures/reportRun'

function column(key: string) {
  const found = REPORT_COLUMNS.find((item) => item.key === key)
  if (!found) throw new Error(`No column ${key}`)
  return found
}

describe('report column model', () => {
  it('shows the eight required TTFT/TPOT columns in the core preset', () => {
    const columns = columnsForPreset('core', [makeRun()])
    const latency = columns.filter((item) => item.group === 'ttft' || item.group === 'tpot')
    expect(latency.map((item) => item.key)).toEqual([
      'ttft_mean',
      'ttft_p90',
      'ttft_p95',
      'ttft_p99',
      'tpot_mean',
      'tpot_p90',
      'tpot_p95',
      'tpot_p99',
    ])
    expect(latency.map((item) => item.labelKey)).toEqual([
      'perf.report.col.mean',
      'perf.report.col.p90',
      'perf.report.col.p95',
      'perf.report.col.p99',
      'perf.report.col.mean',
      'perf.report.col.p90',
      'perf.report.col.p95',
      'perf.report.col.p99',
    ])
  })

  it('binds every cell to its own field, never to a neighbour or an object', () => {
    const run = makeRun()
    expect(formatColumnValue(column('ttft_mean'), run)).toBe('120.00')
    expect(formatColumnValue(column('ttft_p90'), run)).toBe('200.00')
    expect(formatColumnValue(column('ttft_p95'), run)).toBe('250.00')
    expect(formatColumnValue(column('ttft_p99'), run)).toBe('300.00')
    expect(formatColumnValue(column('tpot_mean'), run)).toBe('20.00')
    expect(formatColumnValue(column('tpot_p95'), run)).toBe('35.00')
    for (const item of columnsForPreset('core', [run])) {
      expect(['string']).toContain(typeof formatColumnValue(item, run))
    }
  })

  it('renders a missing value as a dash and a measured zero as zero', () => {
    const missing = makeRun({ ttft: makeDistribution({ mean: 0.12, p95: null }) })
    expect(formatColumnValue(column('ttft_p95'), missing)).toBe('—')
    const zero = makeRun({ tpot: makeDistribution({ mean: 0 }) })
    expect(formatColumnValue(column('tpot_mean'), zero)).toBe('0.00')
    expect(formatNumber(null, 2)).toBe('—')
    expect(formatNumber(0, 2)).toBe('0.00')
  })

  it('only offers server-accepted sort keys', () => {
    expect(column('ttft_mean').sortKey).toBe('ttft_mean')
    expect(column('ttft_p90').sortKey).toBe('ttft_p90')
    expect(column('ttft_p95').sortKey).toBe('ttft_p95')
    expect(column('ttft_p99').sortKey).toBe('ttft_p99')
    expect(column('e2e_p95').sortKey).toBe('latency_p95')
    expect(column('ttft_p50').sortKey).toBeUndefined()
    expect(column('ttft_min').sortKey).toBeUndefined()
    expect(column('tpot_max').sortKey).toBeUndefined()
    expect(column('tpot_std').sortKey).toBeUndefined()
  })

  it('hides the repetition column when no run repeats', () => {
    const run = makeRun()
    expect(columnsForPreset('core', [run]).some((item) => item.key === 'repetition')).toBe(false)
    const repeated = makeRun({ dimensions: { ...run.dimensions, repetition: 2 } })
    expect(columnsForPreset('core', [repeated]).some((item) => item.key === 'repetition')).toBe(true)
  })

  it('distinguishes open-loop rate from closed-loop concurrency', () => {
    const closed = makeRun()
    expect(loadModeOf(closed)).toBe('closed')
    expect(loadValue(closed)).toBe(8)
    expect(loadSortKey([closed])).toBe('concurrency')
    const open = makeRun({ dimensions: { ...closed.dimensions, load_mode: 'open_loop', rate: 4.5, concurrency: null } })
    expect(loadValue(open)).toBe(4.5)
    expect(loadSortKey([open])).toBe('rate')
  })

  it('computes the success rate only when requests were planned', () => {
    expect(successRate(makeRun())).toBeCloseTo(0.99)
    expect(successRate(makeRun({ counts: { planned_requests: 0, total: 0 } }))).toBeNull()
  })

  it('keeps groups contiguous and marks a page that starts inside one', () => {
    const first = makeRun({ run_key: 'a', run_id: 'a' })
    const second = makeRun({ run_key: 'b', run_id: 'b' })
    const other = makeRun({ run_key: 'c', run_id: 'c', dimensions: { ...first.dimensions, output_token_limit: 256 } })
    const groups = contiguousGroups([first, second, other])
    expect(groups.map((group) => group.rows.length)).toEqual([2, 1])
    expect(groups.map((group) => group.continued)).toEqual([false, false])
    const repeated = contiguousGroups([first, other, second])
    expect(repeated.map((group) => group.continued)).toEqual([false, false, true])
  })

  it('toggles a metric sort and starts other columns descending', () => {
    expect(nextSort('order', 'asc', 'ttft_p95')).toEqual({ sort: 'ttft_p95', order: 'desc' })
    expect(nextSort('ttft_p95', 'desc', 'ttft_p95')).toEqual({ sort: 'ttft_p95', order: 'asc' })
    expect(nextSort('ttft_p95', 'desc', 'order')).toEqual({ sort: 'order', order: 'asc' })
  })
})
