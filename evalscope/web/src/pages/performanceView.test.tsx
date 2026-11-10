// Component tests for the Performance_View surfaces.
//
// These cover the display behaviour that lives in the migrated Performance
// pages rather than in the pure `providerResolution` /
// `perfWorkload` domain logic (which have their own property tests):
//   - Provider and Protocol render as two independent, individually-labelled
//     fields and never collapse into one combined field;
//   - a single-run report uses single-run wording (`Run Summary` /
//     `Run Configuration`) and never the cross-run "best configuration" /
//     "Cross-Run" phrasing, while a multi-run report does the opposite;
//   - the run identity is the model alias, not the raw path or timestamp;
//   - the per-run workload context (concurrency, number of requests, request
//     rate) is surfaced and never shows `INF`.
//
// The suite runs under the global deterministic setup (fake timers, fixed
// system time, network disabled). The perf API module is mocked so the pages
// resolve deterministic fixtures instead of hitting the network; async data
// loads are flushed with `settle()`.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'

import { LocaleProvider } from '@/contexts/LocaleContext'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { ReportsProvider } from '@/contexts/ReportsContext'
import { useScan } from '@/contexts/ReportsContext'
import { DomainError } from '@/api/errors'
import { loadFixture } from '@/test/loadFixture'
import type {
  PerfDetailResponse,
  PerfEntryListResponse,
  PerfRequestsResponse,
  PerfRunsListResponse,
  PerformanceResultEntry,
} from '@/api/types'

import PerfReportDetailPage from './PerfReportDetailPage'
import PerfRunsTab from '@/components/perf/PerfRunsTab'
import PerfReportsPage from './PerfReportsPage'

// The perf API module is entirely mocked: the URL builders return stable
// strings and the data loaders are configured per test to resolve fixtures.
vi.mock('@/api/perf', () => ({
  getPerfDetail: vi.fn(),
  listPerfRunDetails: vi.fn(),
  getPerfRequests: vi.fn(),
  listPerfEntries: vi.fn(),
  deletePerfRun: vi.fn(),
  getPerfChartUrl: vi.fn(() => '/api/v1/perf/chart'),
  getPerfHistoryReportUrl: vi.fn(() => '/api/v1/perf/history/report'),
  getCacheExperimentReportUrl: vi.fn(() => '/api/v1/perf/experiments/task/report'),
}))

import * as perfApi from '@/api/perf'

const detailFixture = loadFixture<PerfDetailResponse>('perf-detail')
const singleRunFixture = loadFixture<PerfRunsListResponse>('perf-single-run')

const RUN_PATH = 'perf/test-model-a/20240101_000000'

/** An empty per-request page so the Runs tab has no rows to render. */
const EMPTY_REQUESTS: PerfRequestsResponse = {
  columns: [],
  rows: [],
  total: 0,
  page: 1,
  page_size: 50,
  has_db: true,
}

/** A representative unified performance entry for the list view. */
const ENTRY: PerformanceResultEntry = {
  entry_id: `run:${RUN_PATH}`,
  result_kind: 'run',
  root_path: './outputs',
  path: RUN_PATH,
  display_name: 'test-model-a',
  model: 'test-model-a',
  alias: null,
  endpoint: 'dashscope.aliyuncs.com',
  dataset: 'openqa',
  test_type: 'single',
  engine: 'evalscope',
  executor_version: null,
  execution_status: 'completed',
  quality: 'complete',
  timestamp: '2024-01-01T00:00:00',
  time_basis: 'legacy',
  num_runs: 1,
  total_requests: 100,
  best_rps: 5.43,
  best_latency: 1.842,
  success_rate: 100.0,
  metric_scope: 'run',
  capabilities: {},
  detail_path: `/perf-report?path=${encodeURIComponent(RUN_PATH)}&root_path=${encodeURIComponent('./outputs')}`,
  actions: ['open', 'html', 'compare', 'copy_config', 'delete'],
  unreadable_reason: null,
}

/** One page of the unified entry list, with a single readable run. */
const ENTRY_PAGE: PerfEntryListResponse = {
  entries: [ENTRY],
  total: 1,
  known_total: 1,
  offset: 0,
  limit: 25,
  root_path: './outputs',
  observed_at: '2024-01-01T00:00:00Z',
  scanned: 1,
  skipped: 0,
  completeness: 'complete',
}

/**
 * Flush the chained promise resolutions and effect re-renders produced by the
 * pages' async data loads. Mocked promises settle on microtasks (no real
 * timers), so a handful of act flushes drains the load → state → effect chain.
 */
async function settle(): Promise<void> {
  for (let i = 0; i < 8; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

/** Text of the value span rendered next to a labelled identity/workload field. */
function labelledValue(label: string): string {
  const labelEl = screen.getByText(label)
  return labelEl.nextElementSibling?.textContent ?? ''
}

beforeEach(() => {
  vi.mocked(perfApi.getPerfDetail).mockResolvedValue(detailFixture)
  vi.mocked(perfApi.listPerfRunDetails).mockResolvedValue(singleRunFixture)
  vi.mocked(perfApi.getPerfRequests).mockResolvedValue(EMPTY_REQUESTS)
  vi.mocked(perfApi.listPerfEntries).mockResolvedValue(ENTRY_PAGE)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// ------------------------------------------------------------------ //
// PerfReportDetailPage                                                //
// ------------------------------------------------------------------ //

/** Render the detail page for the given run path within the required providers. */
async function renderDetail(detail: PerfDetailResponse = detailFixture) {
  vi.mocked(perfApi.getPerfDetail).mockResolvedValue(detail)
  const entry =
    `/perf-report?path=${encodeURIComponent(RUN_PATH)}&root_path=${encodeURIComponent('./outputs')}`
  render(
    <LocaleProvider>
      <ThemeProvider>
        <ReportsProvider>
          <MemoryRouter initialEntries={[entry]}>
            <PerfReportDetailPage />
          </MemoryRouter>
        </ReportsProvider>
      </ThemeProvider>
    </LocaleProvider>,
  )
  await settle()
}

describe('PerfReportDetailPage', () => {
  it('exposes node windows for AIPerf and keeps missing metrics explicit', async () => {
    await renderDetail({ ...detailFixture, num_runs: 1, engine: 'aiperf', quality_reasons: ['telemetry_missing'],
      node_analysis: { availability: 'missing', reason: 'no_samples', max_gap_ns: 1 } })
    fireEvent.click(screen.getByRole('tab', { name: 'P/D node metrics' }))
    expect(screen.getAllByText('telemetry_missing').length).toBeGreaterThan(0)
    expect(screen.getByText('no_samples')).toBeInTheDocument()
    expect(screen.getByText('No node windows available.')).toBeInTheDocument()
  })

  it('keeps native reports free of the AIPerf node tab', async () => {
    await renderDetail({ ...detailFixture, num_runs: 1 })
    expect(screen.queryByRole('tab', { name: 'P/D node metrics' })).not.toBeInTheDocument()
  })

  it('does not describe an embedding run as a decision service', async () => {
    await renderDetail({ ...detailFixture, is_embedding: true, api_type: 'openai_embedding' })
    expect(screen.queryByText(/Input-only decisions:/)).not.toBeInTheDocument()
    const chartTypes = vi.mocked(perfApi.getPerfChartUrl).mock.calls.map((call) => call[2])
    expect(chartTypes).not.toContain('percentile_token')
    expect(chartTypes).not.toContain('req_ttft_tpot')
  })

  it.each(['systemone', 'autotrust'])('hides generation timings for %s decisions', async (apiType) => {
    vi.mocked(perfApi.getPerfRequests).mockResolvedValue({
      ...EMPTY_REQUESTS,
      columns: ['#', 'Latency(s)', 'TTFT(ms)', 'TPOT(ms)', 'Success'],
      rows: [{ '#': 1, 'Latency(s)': 0.1, 'TTFT(ms)': 0, 'TPOT(ms)': 0, Success: 'OK' }],
      total: 1,
    })
    await renderDetail({ ...detailFixture, api_type: apiType, basic_info: { ...detailFixture.basic_info, Protocol: '' } })
    expect(labelledValue('Protocol')).toBe(apiType === 'systemone' ? 'System One' : 'AutoTrust /decide')
    expect(screen.getByText(/TTFT, TPOT and ITL are not applicable/)).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /TTFT|TPOT/ })).not.toBeInTheDocument()
    const chartTypes = vi.mocked(perfApi.getPerfChartUrl).mock.calls.map((call) => call[2])
    expect(chartTypes).not.toContain('percentile_token')
    expect(chartTypes).not.toContain('req_ttft_tpot')
    expect(chartTypes).toContain('req_latency')
  })

  it('renders Provider and Protocol as two independent labelled fields', async () => {
    await renderDetail()

    // Both labels are present as distinct fields ...
    expect(screen.getByText('Provider')).toBeInTheDocument()
    expect(screen.getByText('Protocol')).toBeInTheDocument()

    // ... each carrying its own, independent value (not merged into one field).
    expect(labelledValue('Provider')).toBe('Custom')
    expect(labelledValue('Protocol')).toBe('OpenAI-compatible')
    expect(labelledValue('Provider')).not.toBe(labelledValue('Protocol'))
  })

  it('detects DashScope from a sanitized archive endpoint host', async () => {
    await renderDetail({
      ...detailFixture,
      basic_info: { ...detailFixture.basic_info, Provider: '', 'API Host': 'dashscope.aliyuncs.com' },
    })

    expect(labelledValue('Provider')).toBe('DashScope')
    // The sanitized host is surfaced as the labelled Endpoint field.
    expect(labelledValue('Endpoint')).toBe('dashscope.aliyuncs.com')
  })

  it('prefers the run alias as the heading when set', async () => {
    await renderDetail({ ...detailFixture, alias: 'server-A' })
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('server-A')
  })

  it('uses the model alias as the primary identity, not the path/timestamp', async () => {
    await renderDetail()

    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading).toHaveTextContent('test-model-a')
    // The raw path and timestamp must not be the primary identity.
    expect(heading.textContent).not.toContain('20240101_000000')
    expect(heading.textContent).not.toContain('perf/')
  })

  it('uses single-run wording and avoids cross-run "best configuration" phrasing', async () => {
    await renderDetail() // detailFixture has num_runs === 1

    // A single-run report front-loads the Runs tab; switch to Overview to
    // inspect the summary/configuration section wording.
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }))

    expect(screen.getByText('Run Summary')).toBeInTheDocument()
    expect(screen.getByText('Run Configuration')).toBeInTheDocument()
    expect(screen.getByText(/Single-run benchmark/)).toBeInTheDocument()

    // The cross-run phrasing must not appear for a single-run report.
    expect(screen.queryByText('Cross-Run Summary')).not.toBeInTheDocument()
    expect(screen.queryByText('Best Configuration')).not.toBeInTheDocument()
  })

  it('replaces the unlimited-rate sentinel in the overview summary', async () => {
    await renderDetail({
      ...detailFixture,
      summary_columns: [
        { key: 'concurrency', label: 'Conc.', semantics: null },
        { key: 'request_rate', label: 'Rate', semantics: null },
        { key: 'request_throughput', label: 'RPS', semantics: detailFixture.summary_columns[2].semantics },
      ],
      summary_rows: [{
        values: { concurrency: 2, request_rate: -1, request_throughput: 1.9256 },
        sample_counts: { request_throughput: 100 },
      }],
    })
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }))

    expect(screen.getAllByText('closed-loop')).not.toHaveLength(0)
    expect(screen.queryByText(/^INF$/i)).not.toBeInTheDocument()
  })

  it('uses cross-run wording for a multi-run report', async () => {
    await renderDetail({ ...detailFixture, num_runs: 3 })

    // A multi-run report stays on the Overview tab by default.
    expect(screen.getByText('Cross-Run Summary')).toBeInTheDocument()
    expect(screen.getByText('Best Configuration')).toBeInTheDocument()

    // The single-run phrasing must not appear for a multi-run report.
    expect(screen.queryByText('Run Summary')).not.toBeInTheDocument()
    expect(screen.queryByText('Run Configuration')).not.toBeInTheDocument()
    expect(screen.queryByText(/Single-run benchmark/)).not.toBeInTheDocument()
  })
})

// ------------------------------------------------------------------ //
// PerfRunsTab                                                         //
// ------------------------------------------------------------------ //

/** Render the per-run tab in isolation with the required locale provider. */
async function renderRunsTab() {
  render(
    <LocaleProvider>
      <PerfRunsTab rootPath="./outputs" path={RUN_PATH} mode="generation" />
    </LocaleProvider>,
  )
  await settle()
}

describe('PerfRunsTab', () => {
  it('surfaces the workload parameters for the selected run', async () => {
    await renderRunsTab()

    // The single-run fixture is parallel=10, number=100, rate=null.
    expect(labelledValue('Concurrency')).toBe('10')
    expect(labelledValue('Number of requests')).toBe('100')
    // A null rate under a concurrency limit is a closed-loop workload ...
    expect(labelledValue('Request rate')).toBe('closed-loop')
    // ... and never the raw `INF` sentinel.
    expect(screen.queryByText(/INF/i)).not.toBeInTheDocument()
  })
})

// ------------------------------------------------------------------ //
// PerfReportsPage                                                     //
// ------------------------------------------------------------------ //

/** Render the perf-run list page within the required providers. */
async function renderReports() {
  render(
    <LocaleProvider>
      <ReportsProvider>
        <MemoryRouter initialEntries={['/performance']}>
          <PerfReportsPage />
          <LocationProbe />
        </MemoryRouter>
      </ReportsProvider>
    </LocaleProvider>,
  )
  await settle()
}

function LocationProbe() {
  const location = useLocation()
  return <span data-testid="location-probe">{location.pathname}{location.search}</span>
}

function RescanHarness() {
  const { triggerScan } = useScan()
  return <button onClick={() => triggerScan()}>Rescan fixture</button>
}

describe('PerfReportsPage', () => {
  it('lists a run by its model alias, not the raw path', async () => {
    await renderReports()

    // The entry identity is the display name/model, not the archive path.
    expect(screen.getByText('test-model-a')).toBeInTheDocument()
    expect(screen.queryByText(RUN_PATH)).not.toBeInTheDocument()
    expect(screen.getByText('dashscope.aliyuncs.com')).toBeInTheDocument()
    expect(screen.getByText('5.43')).toBeInTheDocument()
    expect(screen.getByText('1.842')).toBeInTheDocument()
    expect(screen.getByText('Success')).toBeInTheDocument()
  })

  it('sends the search text to the unified entry API after debounce', async () => {
    await renderReports()
    const search = screen.getByPlaceholderText('Search run name / model / endpoint / dataset...')

    fireEvent.change(search, { target: { value: 'DashScope' } })
    await act(async () => { vi.advanceTimersByTime(300) })
    await settle()

    expect(perfApi.listPerfEntries).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: 'DashScope', offset: 0 }),
    )
  })

  it('filters one unified list by test type and engine', async () => {
    await renderReports()

    fireEvent.change(screen.getByLabelText('Test type'), { target: { value: 'cache_experiment' } })
    await settle()
    expect(perfApi.listPerfEntries).toHaveBeenLastCalledWith(
      expect.objectContaining({ testType: 'cache_experiment' }),
    )

    fireEvent.change(screen.getByLabelText('Engine'), { target: { value: 'aiperf' } })
    await settle()
    expect(perfApi.listPerfEntries).toHaveBeenLastCalledWith(expect.objectContaining({ engine: 'aiperf' }))
    // Still one surface: no second AIPerf-only section is rendered.
    expect(screen.queryByRole('heading', { name: /AIPerf/i })).not.toBeInTheDocument()
  })

  it('shows alias and endpoint host so same-model runs stay distinguishable', async () => {
    vi.mocked(perfApi.listPerfEntries).mockResolvedValue({
      ...ENTRY_PAGE,
      total: 2,
      known_total: 2,
      entries: [
        { ...ENTRY, entry_id: 'run:a', path: 'perf/run-a', display_name: 'server-A', alias: 'server-A', endpoint: '10.0.0.2:8000' },
        { ...ENTRY, entry_id: 'run:b', path: 'perf/run-b', display_name: 'server-B', alias: 'server-B', endpoint: '10.0.0.3:8000', timestamp: '2024-01-02T00:00:00' },
      ],
    })
    await renderReports()

    expect(screen.getByText('server-A')).toBeInTheDocument()
    expect(screen.getByText('server-B')).toBeInTheDocument()
    expect(screen.getByText('10.0.0.2:8000')).toBeInTheDocument()
  })

  it('shows the shared selection tray and opens the selected HTML report', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    await renderReports()

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select performance run: test-model-a' }))

    expect(screen.getByText('1 selected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Compare' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'View HTML' }))

    expect(perfApi.getPerfHistoryReportUrl).toHaveBeenCalledWith('./outputs', RUN_PATH)
    expect(openSpy).toHaveBeenCalledWith('/api/v1/perf/history/report', '_blank')
    openSpy.mockRestore()
  })

  it('enables comparison after selecting two performance runs', async () => {
    vi.mocked(perfApi.listPerfEntries).mockResolvedValue({
      ...ENTRY_PAGE,
      total: 2,
      known_total: 2,
      entries: [
        ENTRY,
        { ...ENTRY, entry_id: 'run:2', path: `${RUN_PATH}-2`, timestamp: '2024-01-02T00:00:00' },
      ],
    })
    await renderReports()

    const checkboxes = screen.getAllByRole('checkbox', { name: 'Select performance run: test-model-a' })
    fireEvent.click(checkboxes[0])
    fireEvent.click(checkboxes[1])

    expect(screen.getByText('2 selected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'View HTML' })).toBeDisabled()
    const compareButton = screen.getByRole('button', { name: 'Compare' })
    expect(compareButton).toBeEnabled()
    fireEvent.click(compareButton)
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/perf-compare?paths=')
  })

  it('keeps an unreadable archive visible with its reason and no actions', async () => {
    vi.mocked(perfApi.listPerfEntries).mockResolvedValue({
      ...ENTRY_PAGE,
      completeness: 'partial',
      skipped: 1,
      entries: [{ ...ENTRY, actions: [], detail_path: null, unreadable_reason: 'missing benchmark_summary.json' }],
    })
    await renderReports()

    expect(screen.getByRole('status')).toHaveTextContent('Some result archives could not be read')
    expect(screen.getByText('Unreadable archive')).toBeInTheDocument()
    expect(screen.getByTitle('missing benchmark_summary.json')).toHaveAttribute('aria-disabled', 'true')
  })

  it('keeps the existing run visible when a refreshed response fails schema validation', async () => {
    render(
      <LocaleProvider>
        <ReportsProvider>
          <MemoryRouter initialEntries={['/performance']}>
            <RescanHarness />
            <PerfReportsPage />
          </MemoryRouter>
        </ReportsProvider>
      </LocaleProvider>,
    )
    await settle()
    expect(screen.getByText('test-model-a')).toBeInTheDocument()

    vi.mocked(perfApi.listPerfEntries).mockRejectedValueOnce(
      new DomainError('validation', 'Performance response did not match its schema'),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Rescan fixture' }))
    await settle()

    expect(screen.getByText('test-model-a')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Performance response did not match its schema')
  })
})

it('renders an unmeasured summary metric as missing instead of zero', async () => {
  await renderDetail({
    ...detailFixture,
    summary_columns: [{ key: 'p99_tpot', label: 'P99 TPOT', semantics: null }],
    summary_rows: [{ values: { p99_tpot: null }, sample_counts: { p99_tpot: 0 } }],
  })
  fireEvent.click(screen.getByRole('tab', { name: 'Overview' }))
  expect(screen.getByRole('cell', { name: '—' })).toBeInTheDocument()
})
