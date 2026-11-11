import { describe, expect, it } from 'vitest'
import type { ResultCapabilities } from '@/api/types'
import {
  availabilityLabelKey,
  capabilityState,
  engineLabelKey,
  isCapabilityAvailable,
  reasonLabelKey,
  resultTabLabelKey,
  resultTabs,
  testTypeLabelKey,
} from './capabilities'

const caps: ResultCapabilities = {
  overview: { availability: 'available' },
  sweep: { availability: 'pending', reason: 'task_not_finished' },
  requests: { availability: 'available' },
  node_metrics: { availability: 'not_configured' },
  cache_experiment: { availability: 'unsupported', reason: 'single_run' },
}

describe('capability state', () => {
  it('defaults a missing payload or surface to unknown', () => {
    expect(capabilityState(null, 'overview').availability).toBe('unknown')
    expect(capabilityState({}, 'requests').availability).toBe('unknown')
  })

  it('only available surfaces count as readable', () => {
    expect(isCapabilityAvailable(caps, 'overview')).toBe(true)
    expect(isCapabilityAvailable(caps, 'sweep')).toBe(false)
  })
})

describe('result tabs', () => {
  it('keeps product order and includes pending surfaces', () => {
    expect(resultTabs(caps)).toEqual(['overview', 'sweep', 'requests'])
  })

  it('shows a cache experiment tab only when that surface exists', () => {
    expect(resultTabs({ ...caps, cache_experiment: { availability: 'available' } })).toEqual([
      'overview',
      'cache_experiment',
      'sweep',
      'requests',
    ])
  })

  it('does not invent tabs for unavailable formats', () => {
    expect(resultTabs({ overview: { availability: 'available' } })).toEqual(['overview'])
    expect(resultTabs(null)).toEqual([])
  })
})

describe('label keys', () => {
  it('maps every availability, including unknown tokens', () => {
    expect(availabilityLabelKey({ availability: 'available' })).toBe('perf.result.availability.available')
    expect(availabilityLabelKey({ availability: 'not_configured' })).toBe('perf.result.availability.not_configured')
    expect(availabilityLabelKey({ availability: 'surprising' as never })).toBe('perf.result.availability.unknown')
    expect(availabilityLabelKey(null)).toBe('perf.result.availability.unknown')
  })

  it('known reason tokens get copy and unknown ones an explicit fallback', () => {
    expect(reasonLabelKey('archive_unreadable')).toBe('perf.result.reason.archive_unreadable')
    expect(reasonLabelKey('brand_new_reason')).toBe('perf.result.reason.unknown')
    expect(reasonLabelKey(undefined)).toBe('perf.result.reason.unknown')
  })

  it('test types and engines are secondary metadata with safe fallbacks', () => {
    expect(testTypeLabelKey('cache_experiment')).toBe('tasks.testType.cache_experiment')
    expect(testTypeLabelKey('other')).toBe('tasks.testType.unknown')
    expect(engineLabelKey('aiperf')).toBe('tasks.engine.aiperf')
    expect(engineLabelKey(null)).toBe('tasks.engine.unknown')
  })

  it('tab labels cover every capability', () => {
    expect(resultTabLabelKey('node_metrics')).toBe('perf.result.tabs.node_metrics')
    expect(resultTabLabelKey('configuration')).toBe('perf.result.tabs.configuration')
  })
})
