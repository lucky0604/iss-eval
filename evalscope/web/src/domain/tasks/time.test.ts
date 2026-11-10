import { describe, expect, it } from 'vitest'
import { effectiveTaskTime, parseTaskTime, timeBasisLabelKey } from './time'

describe('parseTaskTime', () => {
  it('marks timestamps with an explicit offset as aware', () => {
    expect(parseTaskTime('2026-11-09T10:00:00+08:00')?.basis).toBe('aware')
    expect(parseTaskTime('2026-11-09T02:00:00Z')?.basis).toBe('aware')
  })

  it('marks legacy timestamps without an offset as legacy', () => {
    expect(parseTaskTime('2026-11-09 10:00:00')?.basis).toBe('legacy')
  })

  it('returns null for empty or unparseable values', () => {
    expect(parseTaskTime(null)).toBeNull()
    expect(parseTaskTime('  ')).toBeNull()
    expect(parseTaskTime('not a time')).toBeNull()
  })
})

describe('effectiveTaskTime', () => {
  it('prefers the backend effective time and trusts its basis', () => {
    const parsed = effectiveTaskTime({
      effective_time: '2026-11-09 10:00:00',
      time_basis: 'legacy',
      finished_at: '2026-11-09T09:00:00Z',
    })
    expect(parsed?.basis).toBe('legacy')
  })

  it('falls back to finished, started, enqueued in that order', () => {
    expect(effectiveTaskTime({ started_at: '2026-11-09T09:00:00Z', enqueued_at: '2026-11-09T08:00:00Z' })?.ms).toBe(
      Date.parse('2026-11-09T09:00:00Z'),
    )
    expect(effectiveTaskTime({ enqueued_at: '2026-11-09T08:00:00Z' })?.ms).toBe(Date.parse('2026-11-09T08:00:00Z'))
  })

  it('reports no time rather than a guessed instant', () => {
    expect(effectiveTaskTime({})).toBeNull()
    expect(effectiveTaskTime({ effective_time: '', started_at: 'bogus' })).toBeNull()
  })
})

describe('timeBasisLabelKey', () => {
  it('names every basis and defaults to unknown', () => {
    expect(timeBasisLabelKey('aware')).toBe('tasks.time.aware')
    expect(timeBasisLabelKey('legacy')).toBe('tasks.time.legacy')
    expect(timeBasisLabelKey(null)).toBe('tasks.time.unknown')
  })
})
