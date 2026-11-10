import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { DomainError } from '@/api/errors'
import { getTaskDetail } from '@/api/tasks'
import type { LogResponse, TaskDetailResponse } from '@/api/generated/contracts'
import TaskDetailPage from './TaskDetailPage'

const taskApi = vi.hoisted(() => ({ log: vi.fn(), stop: vi.fn() }))

vi.mock('@/api/tasks', () => ({
  getTaskDetail: vi.fn(), getTask: vi.fn(), listTasks: vi.fn(), getTaskSummary: vi.fn(), listActiveTasks: vi.fn(),
}))
vi.mock('@/api/task', () => ({
  createTaskApi: () => ({
    log: taskApi.log, stop: taskApi.stop, reportUrl: () => '', submit: vi.fn(), progress: vi.fn(),
  }),
}))

afterEach(() => { cleanup(); vi.clearAllMocks() })

const OBSERVED = '2026-11-09T00:00:00+00:00'

function detailOf(overrides: Partial<TaskDetailResponse> = {}): TaskDetailResponse {
  return {
    kind: 'eval', status: 'running', status_group: 'executing', observed_at: OBSERVED, task_id: 'task-a', ...overrides,
  }
}

function logOf(overrides: Partial<LogResponse> = {}): LogResponse {
  return { head_line: 0, tail_line: 1, text: 'line one\n', total_lines: 2, ...overrides }
}

function NavigateTo({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate()
  return <button onClick={() => navigate(to)}>{label}</button>
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.search}</div>
}

function renderDetail(entry = '/tasks/task-a', extra?: React.ReactNode) {
  return render(
    <LocaleProvider>
      <MemoryRouter initialEntries={[entry]}>
        {extra}
        <Routes>
          <Route path="/tasks/:taskId" element={<><TaskDetailPage /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </LocaleProvider>,
  )
}

async function flush(ms = 20) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

describe('TaskDetailPage', () => {
  it('shows identity, execution status, quality and result surfaces in one context', async () => {
    vi.mocked(getTaskDetail).mockResolvedValue(detailOf({
      kind: 'perf', test_type: 'cache_experiment', engine: 'aiperf', alias: 'exp-run', model: 'qwen',
      percent: 40, quality: 'partial', config_completeness: 'full',
      config_snapshot: { kind: 'perf', snapshot_type: 'perf_cache_experiment', model: 'qwen', target_ratios: [0.8] },
      capabilities: {
        overview: { availability: 'available' },
        requests: { availability: 'not_configured', reason: 'no_requests' },
        node_metrics: { availability: 'unavailable', reason: 'no_node_metrics' },
      },
      result_availability: { availability: 'available' },
      report_url: '/api/v1/perf/report?task_id=task-a', html_report_url: '/api/v1/perf/report?task_id=task-a&format=html',
    }))
    renderDetail()
    await flush()

    expect(screen.getByRole('heading', { level: 1, name: 'exp-run' })).toBeInTheDocument()
    const header = screen.getByRole('heading', { level: 1, name: 'exp-run' }).parentElement!
    expect(within(header).getByText('Performance')).toBeInTheDocument()
    expect(within(header).getByText('Cache experiment')).toBeInTheDocument()
    expect(within(header).getByText('AIPerf')).toBeInTheDocument()
    expect(within(header).getByText('Running')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '40')
    // Result quality is its own field, not folded into the execution status.
    expect(screen.getByText('Result quality')).toBeInTheDocument()
    expect(screen.getByText('Partial')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')

    expect(screen.getByRole('link', { name: 'Open report' })).toHaveAttribute('href', '/api/v1/perf/report?task_id=task-a')
    expect(screen.getByRole('link', { name: 'Open experiment' })).toHaveAttribute('href', '/perf-experiment/task-a')

    // Configured-but-missing data is explained per surface instead of a dash.
    const requests = screen.getByText('Requests').closest('li')!
    expect(within(requests).getByText('Not configured')).toBeInTheDocument()
    expect(within(requests).getByText('No per-request data was recorded.')).toBeInTheDocument()

    // Configuration lives on its own URL-addressable tab; logs stay untouched
    // until the logs tab is opened.
    await act(async () => { fireEvent.click(screen.getByRole('tab', { name: 'Configuration' })) })
    expect(screen.getByTestId('location')).toHaveTextContent('tab=config')
    expect(screen.getByText('Full configuration')).toBeInTheDocument()
    expect(screen.getByRole('tabpanel')).toHaveTextContent('perf_cache_experiment')
    expect(taskApi.log).not.toHaveBeenCalled()
  })

  it('renders a missing task once and stops polling it', async () => {
    vi.mocked(getTaskDetail).mockRejectedValue(new DomainError('http-4xx', 'missing', 404))
    renderDetail('/tasks/missing')
    await flush()

    expect(screen.getByText('This task does not exist in the output directory.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'All tasks' })).toHaveAttribute('href', '/tasks')

    await flush(15000)
    expect(getTaskDetail).toHaveBeenCalledTimes(1)
  })

  it('says cancel, not stop, for a queued task and waits for the service terminal state', async () => {
    vi.mocked(getTaskDetail).mockResolvedValue(detailOf({ status: 'queued', status_group: 'queued', queue_position: 2 }))
    taskApi.stop.mockResolvedValue({ status: 'cancelling', task_id: 'task-a' })
    renderDetail()
    await flush()

    expect(screen.getByText('Queue #2')).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Cancel queued task' })) })

    const dialog = screen.getByRole('alertdialog')
    expect(within(dialog).getByText('Cancel this queued task?')).toBeInTheDocument()
    expect(within(dialog).getByText('The task has not started yet; cancelling removes it from the queue.')).toBeInTheDocument()
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel queued task' })) })
    await flush()

    // An acknowledgement that does not confirm a terminal state stays queued.
    expect(taskApi.stop).toHaveBeenCalledWith('task-a')
    expect(screen.getByText('Queued')).toBeInTheDocument()
    expect(screen.queryByText('Stopped')).not.toBeInTheDocument()
  })

  it('appends log pages incrementally and stops once a finished task is caught up', async () => {
    vi.mocked(getTaskDetail)
      .mockResolvedValueOnce(detailOf({ logs_available: true, status: 'running', status_group: 'executing' }))
      .mockResolvedValue(detailOf({ logs_available: true, status: 'completed', status_group: 'completed' }))
    taskApi.log
      .mockResolvedValueOnce(logOf({ text: 'line one\n', tail_line: 1, total_lines: 2 }))
      .mockResolvedValue(logOf({ text: 'line two\n', tail_line: 2, total_lines: 2 }))
    renderDetail()
    await flush()

    await act(async () => { fireEvent.click(screen.getByRole('tab', { name: 'Logs' })) })
    expect(taskApi.log).not.toHaveBeenCalled()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Load logs' })) })
    await flush()
    expect(screen.getByText(/line one/)).toBeInTheDocument()

    // The next page starts at the previous tail instead of re-reading the head.
    await flush(3000)
    expect(taskApi.log).toHaveBeenLastCalledWith('task-a', 1, 500, expect.anything())
    expect(screen.getByText(/line two/)).toBeInTheDocument()

    // The detail poll (5s) observes completion before the next log tick (6s):
    // that tick notices the log is caught up and stops the loop.
    await flush(2000)
    await flush(1000)
    const settled = taskApi.log.mock.calls.length
    await flush(9000)
    expect(taskApi.log).toHaveBeenCalledTimes(settled)
  })

  it('reads logs only while the logs tab is showing', async () => {
    vi.mocked(getTaskDetail).mockResolvedValue(detailOf({ logs_available: true }))
    taskApi.log.mockResolvedValue(logOf({ text: 'tick\n', tail_line: 1, total_lines: 5 }))
    renderDetail()
    await flush()

    await act(async () => { fireEvent.click(screen.getByRole('tab', { name: 'Logs' })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Load logs' })) })
    await flush()
    expect(taskApi.log).toHaveBeenCalledTimes(1)

    await act(async () => { fireEvent.click(screen.getByRole('tab', { name: 'Overview' })) })
    await flush(6000)
    expect(taskApi.log).toHaveBeenCalledTimes(1)

    await act(async () => { fireEvent.click(screen.getByRole('tab', { name: 'Logs' })) })
    await flush()
    expect(taskApi.log).toHaveBeenCalledTimes(2)
  })

  it('addresses subrun progress by URL and links to the experiment', async () => {
    vi.mocked(getTaskDetail).mockResolvedValue(detailOf({
      kind: 'perf', test_type: 'cache_experiment', total_subruns: 8, completed_subruns: 2,
    }))
    renderDetail('/tasks/task-a?tab=subruns')
    await flush()

    expect(screen.getByRole('tab', { name: 'Subruns' })).toHaveAttribute('aria-selected', 'true')
    const bar = screen.getByRole('progressbar')
    expect(bar).toHaveAttribute('aria-valuenow', '2')
    expect(bar).toHaveAttribute('aria-valuemax', '8')
    expect(screen.getByRole('link', { name: 'Open experiment' })).toHaveAttribute('href', '/perf-experiment/task-a')
  })

  it('does not paint a late response from the previous task onto the new one', async () => {    let resolveA!: (value: TaskDetailResponse) => void
    const slowA = new Promise<TaskDetailResponse>((resolve) => { resolveA = resolve })
    vi.mocked(getTaskDetail).mockImplementation(async (taskId) =>
      taskId === 'task-a' ? slowA : detailOf({ task_id: 'task-b', alias: 'task-b-run' }))
    renderDetail('/tasks/task-a', <NavigateTo to="/tasks/task-b" label="switch" />)
    await flush()

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'switch' })) })
    await flush()
    expect(screen.getByRole('heading', { level: 1, name: 'task-b-run' })).toBeInTheDocument()

    await act(async () => { resolveA(detailOf({ task_id: 'task-a', alias: 'task-a-run' })) })
    await flush()
    expect(screen.queryByText('task-a-run')).not.toBeInTheDocument()
  })
})
