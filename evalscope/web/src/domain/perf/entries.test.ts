import { describe, expect, it } from 'vitest'
import type { PerformanceResultEntry } from '@/api/types'

import { entryHasAction, experimentIdOf, isEntryOpenable, selectionSupport } from './entries'

function entry(overrides: Partial<PerformanceResultEntry>): PerformanceResultEntry {
  return {
    entry_id: 'run:perf/a',
    result_kind: 'run',
    root_path: './outputs',
    path: 'perf/a',
    capabilities: {},
    ...overrides,
  }
}

const RUN = entry({ actions: ['open', 'html', 'compare', 'copy_config', 'delete'], detail_path: '/perf-report?path=perf/a' })
const EXPERIMENT = entry({
  entry_id: 'experiment:task-1',
  result_kind: 'experiment',
  path: 'task-1/perf',
  task_id: 'task-1',
  actions: ['open', 'html', 'copy_config'],
  detail_path: '/perf-experiment/task-1',
})
const UNREADABLE = entry({ entry_id: 'run:perf/b', path: 'perf/b', actions: [], detail_path: null })

describe('entry actions', () => {
  it('only opens an entry that publishes both open and a detail path', () => {
    expect(isEntryOpenable(RUN)).toBe(true)
    expect(isEntryOpenable(UNREADABLE)).toBe(false)
    expect(isEntryOpenable(entry({ actions: ['open'] }))).toBe(false)
  })

  it('treats a missing action list as no actions rather than all actions', () => {
    expect(entryHasAction(entry({}), 'delete')).toBe(false)
    expect(entryHasAction(RUN, 'delete')).toBe(true)
  })
})

describe('experimentIdOf', () => {
  it('uses the service task id when the entry exposes one', () => {
    expect(experimentIdOf(EXPERIMENT)).toBe('task-1')
  })

  it('falls back to the archive directory for an external read-only experiment', () => {
    expect(experimentIdOf({ ...EXPERIMENT, task_id: null })).toBe('task-1')
    expect(experimentIdOf({ ...EXPERIMENT, task_id: null, path: 'mirror/task-2/perf' })).toBe('task-2')
  })

  it('never treats a native run as an experiment', () => {
    expect(experimentIdOf(RUN)).toBeNull()
  })
})

describe('selectionSupport', () => {
  it('compares only when every selected entry carries compare', () => {
    expect(selectionSupport([RUN]).canCompare).toBe(false)
    expect(selectionSupport([RUN, { ...RUN, entry_id: 'run:b' }]).canCompare).toBe(true)
    const mixed = selectionSupport([RUN, EXPERIMENT])
    expect(mixed.canCompare).toBe(false)
    expect(mixed.compareBlocked).toBe(true)
  })

  it('blocks delete when any selected entry cannot be deleted', () => {
    expect(selectionSupport([RUN, { ...RUN, entry_id: 'run:b' }]).canDelete).toBe(true)
    const mixed = selectionSupport([RUN, EXPERIMENT])
    expect(mixed.canDelete).toBe(false)
    expect(mixed.deleteBlocked).toBe(true)
    expect(selectionSupport([UNREADABLE]).deleteBlocked).toBe(true)
  })

  it('names the reason delete is unavailable', () => {
    const readOnlyRun = entry({
      entry_id: 'run:external',
      root_path: '/mirror',
      path: 'task/perf',
      actions: ['open', 'html', 'copy_config'],
      detail_path: '/perf-report?path=task/perf',
    })
    expect(selectionSupport([RUN]).deleteBlockedReason).toBeNull()
    expect(selectionSupport([EXPERIMENT]).deleteBlockedReason).toBe('experiment')
    expect(selectionSupport([readOnlyRun]).deleteBlockedReason).toBe('read_only')
    expect(selectionSupport([UNREADABLE]).deleteBlockedReason).toBe('mixed')
    expect(selectionSupport([RUN, EXPERIMENT]).deleteBlockedReason).toBe('mixed')
  })

  it('views HTML only for exactly one entry that publishes it', () => {
    expect(selectionSupport([EXPERIMENT]).canViewHtml).toBe(true)
    expect(selectionSupport([RUN, EXPERIMENT]).canViewHtml).toBe(false)
    expect(selectionSupport([UNREADABLE]).viewHtmlBlocked).toBe(true)
  })
})
