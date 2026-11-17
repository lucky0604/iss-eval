import type { ComponentProps } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { makeRun } from '@/test/fixtures/reportRun'
import ReportRequestsPanel from './ReportRequestsPanel'

const { getPerfReportRequests, createPerfExport, getPerfExportStatus, cancelPerfExport, retryPerfExport } = vi.hoisted(() => ({
  getPerfReportRequests: vi.fn(),
  createPerfExport: vi.fn(),
  getPerfExportStatus: vi.fn(),
  cancelPerfExport: vi.fn(),
  retryPerfExport: vi.fn(),
}))

vi.mock('@/api/perfReports', () => ({
  getPerfReportRequests,
  createPerfExport,
  getPerfExportStatus,
  cancelPerfExport,
  retryPerfExport,
}))

const requestsCsv = {
  name: 'requests_csv',
  filename: 'requests-snap-123.csv',
  media_type: 'text/csv; charset=utf-8',
  size: 2048,
  sha256: 'b'.repeat(64),
  url: '/api/v1/perf/reports/exports/e2/download?file=requests_csv',
}

const row = {
  index: 17,
  phase: 'profiling',
  status: 'success' as const,
  prompt_tokens: 1024,
  output_tokens: 128,
  cached_tokens: 0,
  ttft_seconds: 0.25,
  tpot_seconds: 0.035,
  latency_seconds: 2.4,
  tpot_method: 'itl_mean',
  status_reason: null,
}

function renderPanel(overrides: Partial<ComponentProps<typeof ReportRequestsPanel>> = {}) {
  const props = {
    location: { taskId: 'task-1' },
    runs: [makeRun()],
    available: true,
    ...overrides,
  }
  return render(<LocaleProvider><ReportRequestsPanel {...props} /></LocaleProvider>)
}

// The global setup installs fake timers; async reads need real ones.
beforeAll(() => {
  vi.useRealTimers()
})

describe('request details panel', () => {
  beforeEach(() => {
    getPerfReportRequests.mockReset()
    getPerfReportRequests.mockResolvedValue({
      snapshot_id: 's1',
      run_key: 'run-0',
      run_id: 'isl1024-osl128-c8',
      total: 1201,
      offset: 0,
      limit: 50,
      requests: [row],
      sort: 'index',
      order: 'asc',
    })
    createPerfExport.mockReset().mockResolvedValue({
      created: true,
      export: {
        export_id: 'e2',
        status: 'running',
        created_at: '2026-11-16T10:00:00+00:00',
        attempts: 1,
        spec: { kind: 'requests' },
        artifacts: [],
        warnings: [],
      },
    })
    getPerfExportStatus.mockReset().mockResolvedValue({
      export: {
        export_id: 'e2',
        status: 'completed',
        created_at: '2026-11-16T10:00:00+00:00',
        attempts: 1,
        spec: { kind: 'requests' },
        run_count: 1,
        request_count: 1201,
        artifacts: [requestsCsv],
        warnings: [],
      },
    })
    cancelPerfExport.mockReset()
    retryPerfExport.mockReset()
  })

  it('pages over the whole run rather than the first page only', async () => {
    renderPanel()
    await waitFor(() => expect(screen.getByText('17')).toBeInTheDocument())
    expect(screen.getByText('1201 requests match')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '2' }))
    await waitFor(() =>
      expect(getPerfReportRequests).toHaveBeenLastCalledWith(
        expect.anything(),
        expect.objectContaining({ offset: 50, limit: 50 }),
        expect.anything(),
      ),
    )
  })

  it('filters by status on the server and resets to the first page', async () => {
    renderPanel()
    await waitFor(() => expect(screen.getByText('17')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '2' }))
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'failed' } })
    await waitFor(() =>
      expect(getPerfReportRequests).toHaveBeenLastCalledWith(
        expect.anything(),
        expect.objectContaining({ offset: 0, status: 'failed' }),
        expect.anything(),
      ),
    )
  })

  it('sorts by end-to-end latency descending from a header click', async () => {
    renderPanel()
    await waitFor(() => expect(screen.getByText('17')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /E2E \(s\)/ }))
    await waitFor(() =>
      expect(getPerfReportRequests).toHaveBeenLastCalledWith(
        expect.anything(),
        expect.objectContaining({ sort: 'latency', order: 'desc' }),
        expect.anything(),
      ),
    )
  })

  it('searches by request id and downloads every matching request', async () => {
    renderPanel()
    await waitFor(() => expect(screen.getByText('17')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Request ID / reason'), { target: { value: 'req-42' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() =>
      expect(getPerfReportRequests).toHaveBeenLastCalledWith(
        expect.anything(),
        expect.objectContaining({ search: 'req-42', offset: 0 }),
        expect.anything(),
      ),
    )
    expect(screen.getByRole('button', { name: 'Download matching requests' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Download matching requests' }))
    await waitFor(() =>
      expect(createPerfExport).toHaveBeenCalledWith(
        {
          kind: 'requests',
          location: { taskId: 'task-1' },
          runKey: 'run-0',
          status: undefined,
          search: 'req-42',
          phase: 'profiling',
          locale: 'en',
        },
        expect.anything(),
      ),
    )
    const link = await screen.findByRole('link', { name: requestsCsv.filename })
    expect(link).toHaveAttribute('href', requestsCsv.url)
    expect(await screen.findByText(/1 tiers, 1201 requests/)).toBeInTheDocument()
  })

  it('downloads matching requests in the page locale', async () => {
    localStorage.setItem('evalscope-locale', 'zh')
    try {
      renderPanel()
      await waitFor(() => expect(screen.getByText('17')).toBeInTheDocument())
      fireEvent.click(screen.getByRole('button', { name: '下载全部匹配请求' }))
      await waitFor(() =>
        expect(createPerfExport).toHaveBeenCalledWith(
          expect.objectContaining({ kind: 'requests', locale: 'zh' }),
          expect.anything(),
        ),
      )
    } finally {
      localStorage.setItem('evalscope-locale', 'en')
    }
  })

  it('renders request latencies in fixed units and never as an object', async () => {
    renderPanel()
    await waitFor(() => expect(screen.getByText('17')).toBeInTheDocument())
    expect(screen.getByText('250.00')).toBeInTheDocument()
    expect(screen.getByText('35.00')).toBeInTheDocument()
    expect(screen.getByText('2.400')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('{')
  })
})
