import { describe, expect, it } from 'vitest'
import {
  countsAsActive,
  hasStatusLabel,
  isActiveStatus,
  isTerminalStatus,
  progressKind,
  progressPercent,
  statusGroupOf,
  statusLabelKey,
} from './status'

describe('status grouping', () => {
  it('maps every raw status to the backend status group', () => {
    expect(statusGroupOf('queued')).toBe('queued')
    expect(statusGroupOf('preparing')).toBe('executing')
    expect(statusGroupOf('running')).toBe('executing')
    expect(statusGroupOf('cancelling')).toBe('executing')
    expect(statusGroupOf('finalizing')).toBe('executing')
    expect(statusGroupOf('completed')).toBe('completed')
    expect(statusGroupOf('stopped')).toBe('stopped')
    expect(statusGroupOf('error')).toBe('failed')
    expect(statusGroupOf('unknown')).toBe('uncertain')
  })

  it('treats an unrecognized status as uncertain, never as running', () => {
    expect(statusGroupOf('vanished')).toBe('uncertain')
    expect(isActiveStatus('vanished')).toBe(false)
    expect(isTerminalStatus('vanished')).toBe(false)
    expect(countsAsActive({ status: 'vanished' as never })).toBe(false)
  })

  it('only queues and executing groups keep polling', () => {
    expect(countsAsActive({ status: 'running' })).toBe(true)
    expect(countsAsActive({ status: 'queued' })).toBe(true)
    expect(countsAsActive({ status: 'completed' })).toBe(false)
  })

  it('falls back to the uncertain label for statuses without copy', () => {
    expect(hasStatusLabel('running')).toBe(true)
    expect(hasStatusLabel('mystery')).toBe(false)
    expect(statusLabelKey('mystery')).toBe('tasks.state.unknown')
    expect(statusLabelKey('completed')).toBe('tasks.state.completed')
  })
})

describe('progress semantics', () => {
  it('a finished status is finished even at 100%', () => {
    expect(progressKind({ status: 'completed', percent: 100 })).toBe('finished')
    expect(progressKind({ status: 'error', percent: 100 })).toBe('finished')
  })

  it('a running task at 100% is not finished', () => {
    expect(progressKind({ status: 'running', percent: 100 })).toBe('determinate')
  })

  it('queued shows the queue position instead of a bar', () => {
    expect(progressKind({ status: 'queued', percent: 0 })).toBe('queued')
    expect(progressKind({ status: 'queued', percent: null })).toBe('queued')
  })

  it('missing or non-finite percentages stay indeterminate', () => {
    expect(progressKind({ status: 'running', percent: null })).toBe('indeterminate')
    expect(progressKind({ status: 'running', percent: Number.NaN })).toBe('indeterminate')
    expect(progressKind({ status: 'preparing' })).toBe('indeterminate')
  })

  it('clamps percentages and rejects non-numbers', () => {
    expect(progressPercent({ percent: 42.5 })).toBe(42.5)
    expect(progressPercent({ percent: -3 })).toBe(0)
    expect(progressPercent({ percent: 140 })).toBe(100)
    expect(progressPercent({ percent: null })).toBeNull()
    expect(progressPercent({ percent: Number.POSITIVE_INFINITY })).toBeNull()
    expect(progressPercent({ percent: '50' as unknown as number })).toBeNull()
  })
})
