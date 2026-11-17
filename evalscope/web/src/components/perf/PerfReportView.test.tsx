import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { makeRun } from '@/test/fixtures/reportRun'
import PerfReportView from './PerfReportView'

const {
  getPerfReportSummary,
  getPerfReportRuns,
  getPerfReportRequests,
  getPerfReportCharts,
  createPerfExport,
  getPerfExportStatus,
  getPerfExportCapabilities,
  cancelPerfExport,
  retryPerfExport,
} = vi.hoisted(() => ({
  getPerfReportSummary: vi.fn(),
  getPerfReportRuns: vi.fn(),
  getPerfReportRequests: vi.fn(),
  getPerfReportCharts: vi.fn(),
  createPerfExport: vi.fn(),
  getPerfExportStatus: vi.fn(),
  getPerfExportCapabilities: vi.fn(),
  cancelPerfExport: vi.fn(),
  retryPerfExport: vi.fn(),
}))

vi.mock('@/api/perfReports', () => ({
  getPerfReportSummary,
  getPerfReportRuns,
  getPerfReportRequests,
  getPerfReportCharts,
  createPerfExport,
  getPerfExportStatus,
  getPerfExportCapabilities,
  cancelPerfExport,
  retryPerfExport,
}))

const exportedFile = {
  name: 'pdf',
  filename: 'perf-report-snap-123-en.pdf',
  media_type: 'application/pdf',
  size: 4096,
  sha256: 'a'.repeat(64),
  url: '/api/v1/perf/reports/exports/e1/download?file=pdf',
}

function queuedExport() {
  return {
    created: true,
    export: {
      export_id: 'e1',
      status: 'running' as const,
      created_at: '2026-11-16T10:00:00+00:00',
      attempts: 1,
      spec: { kind: 'pdf' },
      artifacts: [],
      warnings: [],
    },
  }
}

function completedExport() {
  return {
    export: {
      ...queuedExport().export,
      status: 'completed' as const,
      run_count: 250,
      request_count: 250000,
      artifacts: [exportedFile],
    },
  }
}

const runs = Array.from({ length: 250 }, (_, index) =>
  makeRun({
    run_key: `run-${index}`,
    run_id: `run-${index}`,
    order: index,
    dimensions: { ...makeRun().dimensions, concurrency: index + 1, output_token_limit: index < 125 ? 128 : 256 },
  }),
)

const header = {
  snapshot_id: 'snap-123',
  created_at: '2026-11-16T10:00:00',
  source_kind: 'plan',
  source_identity: 'plan:abc',
  statistics_version: '1',
  model: 'Qwen3-8B',
  partial: false,
  quality: 'complete',
  counts: { planned_runs: 250, observed_requests: 250_000, successful_requests: 249_800 },
  capabilities: {
    requests: { availability: 'available' },
    token_usage: { availability: 'available' },
    cache: { availability: 'available' },
    client_metrics: { availability: 'available' },
    node_metrics: { availability: 'available' },
  },
  groups: [
    { group_key: 'g1', run_count: 125, planned_requests: 125_000, observed_requests: 125_000, run_keys: [], dimensions: { ...makeRun().dimensions } },
    { group_key: 'g2', run_count: 125, planned_requests: 125_000, observed_requests: 125_000, run_keys: [], dimensions: { ...makeRun().dimensions, output_token_limit: 256 } },
  ],
}

// The global setup installs fake timers; async reads need real ones.
beforeAll(() => {
  vi.useRealTimers()
})

describe('numeric report view', () => {
  beforeEach(() => {
    getPerfReportSummary.mockReset().mockResolvedValue({ report: header, total_runs: 250 })
    getPerfReportRuns.mockReset().mockImplementation(
      (_location: unknown, query: { offset?: number; limit?: number }) => {
        const offset = query.offset ?? 0
        const limit = query.limit ?? 100
        return Promise.resolve({
          snapshot_id: 'snap-123',
          rows: runs.slice(offset, offset + limit),
          total: runs.length,
          offset,
          limit,
          sort: 'comparison',
          order: 'asc',
        })
      },
    )
    getPerfReportRequests.mockReset().mockResolvedValue({
      snapshot_id: 'snap-123',
      run_key: 'run-0',
      run_id: 'run-0',
      total: 0,
      offset: 0,
      limit: 50,
      requests: [],
      sort: 'index',
      order: 'asc',
    })
    getPerfReportCharts.mockReset().mockImplementation((_location: unknown, query: { metric: string; statistic: string }) =>
      Promise.resolve({ snapshot_id: 'snap-123', series: [], metric: query.metric, statistic: query.statistic }),
    )
    createPerfExport.mockReset().mockResolvedValue(queuedExport())
    getPerfExportStatus.mockReset().mockResolvedValue(completedExport())
    getPerfExportCapabilities.mockReset().mockResolvedValue({
      pdf_available: true,
      pdf_renderer: 'chrome',
      pdf_message: '',
      kinds: ['html', 'pdf', 'csv', 'requests', 'bundle'],
    })
    cancelPerfExport.mockReset()
    retryPerfExport.mockReset()
  })

  it('reads the default table in comparison order, pinned to the header snapshot', async () => {
    render(<LocaleProvider><PerfReportView location={{ taskId: 'task-1' }} /></LocaleProvider>)
    await screen.findByText('snap-123')
    expect(getPerfReportSummary).toHaveBeenCalledWith({ taskId: 'task-1' }, expect.anything())
    await waitFor(() =>
      expect(getPerfReportRuns).toHaveBeenCalledWith(
        { taskId: 'task-1', rootPath: undefined, path: undefined, snapshotId: 'snap-123' },
        expect.objectContaining({ offset: 0, sort: 'comparison', order: 'asc' }),
        expect.anything(),
      ),
    )
  })

  it('offers the node tab only when the archive has node observations', async () => {
    render(<LocaleProvider><PerfReportView location={{ taskId: 'task-1' }} /></LocaleProvider>)
    await screen.findByText('snap-123')
    expect(screen.getByRole('tab', { name: 'Nodes' })).toBeInTheDocument()
  })

  it('hides the node tab when node metrics are unavailable', async () => {
    getPerfReportSummary.mockResolvedValue({
      report: {
        ...header,
        capabilities: { ...header.capabilities, node_metrics: { availability: 'unavailable', reason: 'no_persisted_node_analysis' } },
      },
      total_runs: 250,
    })
    render(<LocaleProvider><PerfReportView location={{ taskId: 'task-1' }} /></LocaleProvider>)
    await screen.findByText('snap-123')
    expect(screen.queryByRole('tab', { name: 'Nodes' })).toBeNull()
  })

  it('describes the snapshot without shrinking its totals to the current page', async () => {
    render(<LocaleProvider><PerfReportView location={{ taskId: 'task-1' }} /></LocaleProvider>)
    expect(await screen.findByText('snap-123')).toBeInTheDocument()
    expect(screen.getByText('250 planned runs · 250000 observed requests · 249800 successful')).toBeInTheDocument()
    expect(await screen.findByText(/250\/250 runs/)).toBeInTheDocument()
  })

  it('exports the whole snapshot through its own record, following the selected group', async () => {
    render(<LocaleProvider><PerfReportView location={{ taskId: 'task-1' }} /></LocaleProvider>)
    await screen.findByText('snap-123')
    expect(screen.getByText('Every tier and every request in this snapshot')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'PDF' }))
    await waitFor(() =>
      expect(createPerfExport).toHaveBeenCalledWith(
        { kind: 'pdf', location: { taskId: 'task-1' }, group: undefined, preset: 'core', locale: 'en' },
        expect.anything(),
      ),
    )
    const link = await screen.findByRole('link', { name: exportedFile.filename })
    expect(link).toHaveAttribute('href', exportedFile.url)
    expect(link).toHaveAttribute('download', exportedFile.filename)
    expect(await screen.findByText(/Export ready: 250 tiers, 250000 requests/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/Group/), { target: { value: 'g2' } })
    await waitFor(() => expect(screen.getByText(/Current group filter/)).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'PDF' }))
    await waitFor(() =>
      expect(createPerfExport).toHaveBeenLastCalledWith(
        { kind: 'pdf', location: { taskId: 'task-1' }, group: 'g2', preset: 'core', locale: 'en' },
        expect.anything(),
      ),
    )
  })

  it('exports in the locale of the page that asked for it', async () => {
    localStorage.setItem('evalscope-locale', 'zh')
    try {
      render(<LocaleProvider><PerfReportView location={{ taskId: 'task-1' }} /></LocaleProvider>)
      await screen.findByText('snap-123')
      fireEvent.click(screen.getByRole('button', { name: 'PDF' }))
      await waitFor(() =>
        expect(createPerfExport).toHaveBeenCalledWith(
          expect.objectContaining({ kind: 'pdf', locale: 'zh' }),
          expect.anything(),
        ),
      )
    } finally {
      localStorage.setItem('evalscope-locale', 'en')
    }
  })

  it('disables PDF with the renderer reason when the host cannot render it', async () => {
    getPerfExportCapabilities.mockResolvedValue({
      pdf_available: false,
      pdf_renderer: null,
      pdf_message: 'install playwright or set EVALSCOPE_CHROME',
      kinds: ['html', 'csv', 'requests', 'bundle'],
    })
    render(<LocaleProvider><PerfReportView location={{ taskId: 'task-1' }} /></LocaleProvider>)
    await screen.findByText('snap-123')
    const pdf = screen.getByRole('button', { name: 'PDF' })
    await waitFor(() => expect(pdf).toBeDisabled())
    expect(pdf).toHaveAttribute('title', expect.stringContaining('install playwright'))
    expect(screen.getByRole('button', { name: 'HTML' })).toBeEnabled()
  })

  it('shows a failed export with its server reason and offers a retry', async () => {
    getPerfExportStatus.mockResolvedValue({
      export: { ...queuedExport().export, status: 'failed', error: 'renderer exploded' },
    })
    render(<LocaleProvider><PerfReportView location={{ taskId: 'task-1' }} /></LocaleProvider>)
    await screen.findByText('snap-123')
    fireEvent.click(screen.getByRole('button', { name: 'HTML' }))
    expect(await screen.findByText('Export failed: renderer exploded')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(createPerfExport).toHaveBeenCalledTimes(2))
  })

  it('loads every run, not only the first page, for the request run selector', async () => {
    render(<LocaleProvider><PerfReportView location={{ taskId: 'task-1' }} /></LocaleProvider>)
    await screen.findByText('snap-123')
    fireEvent.click(screen.getByRole('tab', { name: 'Requests' }))
    await waitFor(() => {
      const selector = screen.getByLabelText('Run') as HTMLSelectElement
      expect(selector.options).toHaveLength(250)
    })
    expect(getPerfReportRuns).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ offset: 0, limit: 500 }),
      expect.anything(),
    )
  })
})
