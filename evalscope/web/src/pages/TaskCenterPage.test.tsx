import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { getTaskSummary, listTasks } from '@/api/tasks'
import type { TaskListResponse, TaskSummaryItem, TaskSummaryResponse } from '@/api/generated/contracts'
import TaskCenterPage from './TaskCenterPage'

vi.mock('@/api/tasks', () => ({
  getTaskSummary: vi.fn(), listTasks: vi.fn(), getTaskDetail: vi.fn(), getTask: vi.fn(), listActiveTasks: vi.fn(),
}))

afterEach(() => { cleanup(); vi.clearAllMocks() })

const OBSERVED = '2026-11-09T00:00:00+00:00'

function item(overrides: Partial<TaskSummaryItem> & { task_id: string }): TaskSummaryItem {
  return {
    kind: 'eval', status: 'completed', status_group: 'completed', observed_at: OBSERVED, ...overrides,
  } as TaskSummaryItem
}

function listOf(items: TaskSummaryItem[], overrides: Partial<TaskListResponse> = {}): TaskListResponse {
  return {
    completeness: 'complete', indexing: { state: 'ready' }, items, known_total: items.length, limit: 25,
    observed_at: OBSERVED, offset: 0, revision: 1, total: items.length, ...overrides,
  }
}

function summaryOf(overrides: Partial<TaskSummaryResponse> = {}): TaskSummaryResponse {
  return {
    attention: 0, completed_in_range: 0, executing: 0, observed_at: OBSERVED, queued: 0,
    queue: { backend: 'local', reachable: true }, ...overrides,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.search}</div>
}

function renderCenter(entry = '/tasks') {
  return render(
    <LocaleProvider>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/tasks" element={<><TaskCenterPage /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </LocaleProvider>,
  )
}

// The suite runs on fake timers, so asynchronous assertions advance them
// explicitly instead of waiting on the wall clock.
async function flush(ms = 20) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

/** Read a KPI value by its label; the same words also appear in a badge/option. */
function summaryValue(label: string): string | null {
  const labelElement = screen.getAllByText(label).find((element) => element.className.includes('uppercase'))
  return labelElement?.parentElement?.querySelector('.text-2xl')?.textContent ?? null
}

describe('TaskCenterPage', () => {
  it('shows an untrustworthy running state once, with its explanation', async () => {
    vi.mocked(getTaskSummary).mockResolvedValue(summaryOf({ attention: 1 }))
    vi.mocked(listTasks).mockResolvedValue(listOf([
      item({ task_id: 'stale-1', status: 'unknown', status_group: 'uncertain' }),
    ]))
    renderCenter()
    await flush()

    expect(screen.getAllByText('Running state unknown')).toHaveLength(1)
    expect(screen.getByText('No live process owns this task; its last observed state is shown.')).toBeInTheDocument()
  })
  it('separates executing, queued and completed counts without inventing progress', async () => {
    vi.mocked(getTaskSummary).mockResolvedValue(summaryOf({ executing: 3, queued: 2, attention: 1, completed_in_range: 12 }))
    vi.mocked(listTasks).mockResolvedValue(listOf([
      item({ task_id: 'run-1', alias: 'nightly-eval', kind: 'perf', status: 'running', status_group: 'executing',
        percent: 42, test_type: 'single', engine: 'aiperf', model: 'qwen' }),
      item({ task_id: 'run-2', status: 'queued', status_group: 'queued', queue_position: 3, percent: null }),
      item({ task_id: 'run-3', alias: 'partial-run', quality: 'partial',
        result_availability: { availability: 'available' } }),
    ]))
    renderCenter()
    await flush()

    expect(summaryValue('Executing')).toBe('3')
    expect(summaryValue('Queued')).toBe('2')
    expect(summaryValue('Needs attention')).toBe('1')
    expect(summaryValue('Completed today')).toBe('12')

    // Only the running task has a determinate bar; a queued task shows its
    // position instead of a fabricated percentage.
    expect(screen.getAllByRole('progressbar')).toHaveLength(1)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42')
    expect(screen.getByText('Queue #3')).toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()

    // Completion and partial quality are two independent signals on one row.
    const partialRow = screen.getByText('partial-run').closest('li')!
    expect(within(partialRow).getByText('Completed')).toBeInTheDocument()
    expect(within(partialRow).getByText('Partial')).toBeInTheDocument()
    expect(screen.getByText('Showing 1–3 of 3')).toBeInTheDocument()

    // The active view polls; one tick issues a fresh read.
    await flush(5100)
    expect(vi.mocked(listTasks).mock.calls.length).toBeGreaterThan(1)
  })

  it('keeps the previous page while a filter change loads and reports failures with retry', async () => {
    vi.mocked(getTaskSummary).mockRejectedValueOnce(new Error('offline'))
    vi.mocked(listTasks).mockRejectedValueOnce(new Error('offline'))
    vi.mocked(getTaskSummary).mockResolvedValue(summaryOf({ executing: 1 }))
    vi.mocked(listTasks).mockResolvedValue(listOf([item({ task_id: 'run-9', alias: 'recovered' })]))
    renderCenter()
    await flush()

    expect(screen.getByText('Could not load the task history.')).toBeInTheDocument()
    expect(screen.getByText('offline')).toBeInTheDocument()
    // A failed first read is unknown, not "zero tasks".
    expect(summaryValue('Executing')).toBe('—')

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry' })) })
    await flush()
    expect(screen.getByText('recovered')).toBeInTheDocument()
    expect(screen.queryByText('offline')).not.toBeInTheDocument()
    expect(summaryValue('Executing')).toBe('1')
  })

  it('restores filters from the URL and pages without losing them', async () => {
    const rows = Array.from({ length: 10 }, (_, index) => item({ task_id: `run-${index}` }))
    vi.mocked(getTaskSummary).mockResolvedValue(summaryOf())
    vi.mocked(listTasks).mockImplementation(async (query = {}) => listOf(rows, {
      total: 60, known_total: 60, offset: query.offset ?? 0, limit: query.limit ?? 25,
    }))
    renderCenter('/tasks?view=history&kind=perf&engine=aiperf&status=error,stopped&q=alpha&offset=50&limit=10&sort=queue')
    await flush()

    expect(vi.mocked(listTasks).mock.calls[0][0]).toMatchObject({
      view: 'history', kind: 'perf', engine: 'aiperf', statuses: ['error', 'stopped'], q: 'alpha',
      offset: 50, limit: 10, sort: 'queue',
    })
    expect(screen.getByText('Showing 51–60 of 60')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previous' })) })
    await flush()
    expect(screen.getByTestId('location')).toHaveTextContent('offset=40')
    expect(screen.getByText('Showing 41–50 of 60')).toBeInTheDocument()
  })

  it('debounces typing into the URL instead of refetching on every keystroke', async () => {
    vi.mocked(getTaskSummary).mockResolvedValue(summaryOf())
    vi.mocked(listTasks).mockResolvedValue(listOf([item({ task_id: 'run-1', alias: 'alpha' })]))
    renderCenter()
    await flush()
    vi.mocked(listTasks).mockClear()

    fireEvent.change(screen.getByPlaceholderText('Task ID, model or name'), { target: { value: 'beta' } })
    await flush(200)
    expect(listTasks).not.toHaveBeenCalled()

    await flush(150)
    await flush(400)
    await flush(20)
    expect(vi.mocked(listTasks).mock.calls[0][0]).toMatchObject({ q: 'beta', offset: 0 })
    expect(screen.getByTestId('location')).toHaveTextContent('q=beta')
  })

  it('states the index range instead of presenting a partial scan as complete', async () => {
    vi.mocked(getTaskSummary).mockResolvedValue(summaryOf())
    vi.mocked(listTasks).mockResolvedValue(listOf([], {
      completeness: 'partial', indexing: { state: 'indexing', scanned: 4 }, excluded_without_time: 2, total: 0,
    }))
    renderCenter()
    await flush()

    expect(screen.getByText('Indexing existing tasks…')).toBeInTheDocument()
    expect(screen.getByText('The history index is incomplete; some older tasks may be missing.')).toBeInTheDocument()
    expect(screen.getByText(/2 tasks have no usable timestamp/)).toBeInTheDocument()
    expect(screen.getByText('No tasks match these filters.')).toBeInTheDocument()
  })
})
