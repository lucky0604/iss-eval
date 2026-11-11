import { describe, expect, it } from 'vitest'
import {
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE,
  defaultTaskFilters,
  parseTaskFilters,
  taskFilterParams,
  taskFiltersKey,
  updateTaskParams,
} from './query'

function params(record: Record<string, string>): URLSearchParams {
  return new URLSearchParams(record)
}

describe('parseTaskFilters', () => {
  it('returns defaults for an empty query string', () => {
    const filters = parseTaskFilters(new URLSearchParams())
    expect(filters).toEqual(defaultTaskFilters())
    expect(filters.limit).toBe(DEFAULT_PAGE_SIZE)
    expect(filters.offset).toBe(0)
  })

  it('degrades hostile or stale values instead of throwing', () => {
    const filters = parseTaskFilters(
      params({
        view: 'nonsense',
        kind: 'sandbox',
        engine: 'tmux',
        sort: 'random',
        status: 'running,bogus,completed',
        limit: '9999',
        offset: '-5',
        from: 'yesterday',
      }),
    )
    expect(filters.view).toBe('all')
    expect(filters.kind).toBeUndefined()
    expect(filters.engine).toBeUndefined()
    expect(filters.sort).toBe('recent')
    expect(filters.statuses).toEqual(['running', 'completed'])
    expect(filters.limit).toBe(MAX_PAGE_SIZE)
    expect(filters.offset).toBe(0)
    expect(filters.from).toBe('')
  })

  it('deduplicates statuses and accepts explicit paging', () => {
    const filters = parseTaskFilters(params({ status: 'running,running,queued', offset: '50', limit: '10' }))
    expect(filters.statuses).toEqual(['running', 'queued'])
    expect(filters.offset).toBe(50)
    expect(filters.limit).toBe(10)
  })
})

describe('taskFilterParams', () => {
  it('omits defaults so old links stay short', () => {
    expect(taskFilterParams(defaultTaskFilters()).toString()).toBe('')
  })

  it('round-trips every non-default filter', () => {
    const filters = {
      view: 'attention' as const,
      kind: 'perf' as const,
      engine: 'aiperf' as const,
      statuses: ['error', 'unknown'],
      q: 'qwen',
      from: '2026-11-01T00:00:00Z',
      to: '2026-11-09T00:00:00Z',
      offset: 25,
      limit: 10,
      sort: 'queue' as const,
    }
    expect(parseTaskFilters(taskFilterParams(filters))).toEqual(filters)
    expect(taskFiltersKey(filters)).toBe(taskFilterParams(filters).toString())
  })
})

describe('updateTaskParams', () => {
  it('resets paging when a filter changes', () => {
    const current = params({ kind: 'perf', offset: '75' })
    expect(updateTaskParams(current, { kind: 'eval' }).get('offset')).toBeNull()
  })

  it('keeps the offset when paging', () => {
    const current = params({ kind: 'perf' })
    expect(updateTaskParams(current, { offset: 25 }).get('offset')).toBe('25')
  })

  it('preserves the creation tab parameter', () => {
    const current = params({ tab: 'perf' })
    expect(updateTaskParams(current, { view: 'active' }).get('tab')).toBe('perf')
  })
})
