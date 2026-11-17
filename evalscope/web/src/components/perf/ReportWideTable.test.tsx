import type { ComponentProps } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { columnsForPreset, type ReportPreset } from '@/domain/perf/reportTable'
import { makeDistribution, makeRun } from '@/test/fixtures/reportRun'
import ReportWideTable from './ReportWideTable'

function renderTable(overrides: Partial<ComponentProps<typeof ReportWideTable>> = {}) {
  const runs = overrides.runs ?? [makeRun()]
  const props: ComponentProps<typeof ReportWideTable> = {
    runs,
    total: overrides.total ?? runs.length,
    offset: 0,
    limit: 100,
    sort: 'order',
    order: 'asc',
    preset: 'core',
    onPreset: vi.fn(),
    onSort: vi.fn(),
    onPage: vi.fn(),
    ...overrides,
  }
  return { ...render(<LocaleProvider><ReportWideTable {...props} /></LocaleProvider>), props }
}

function dataRows(): HTMLTableRowElement[] {
  return [...document.querySelectorAll('tbody tr')].filter(
    (row) => row.querySelector('td') !== null && row.getAttribute('aria-expanded') !== null,
  ) as HTMLTableRowElement[]
}

function bodyCell(rowIndex: number, columnKey: string, preset: ReportPreset = 'core'): HTMLElement {
  const keys = columnsForPreset(preset, [makeRun()]).map((item) => item.key)
  const cells = dataRows()[rowIndex].querySelectorAll('td')
  return cells[keys.indexOf(columnKey)] as HTMLElement
}

describe('numeric wide table', () => {
  it('renders the eight default latency columns bound to their own percentiles', () => {
    renderTable()
    const keys = columnsForPreset('core', [makeRun()]).map((item) => item.key)
    expect(keys.filter((key) => key.startsWith('ttft_') || key.startsWith('tpot_'))).toHaveLength(8)
    expect(bodyCell(0, 'ttft_mean').textContent).toBe('120.00')
    expect(bodyCell(0, 'ttft_p90').textContent).toBe('200.00')
    expect(bodyCell(0, 'ttft_p95').textContent).toBe('250.00')
    expect(bodyCell(0, 'ttft_p99').textContent).toBe('300.00')
    expect(bodyCell(0, 'tpot_mean').textContent).toBe('20.00')
    expect(bodyCell(0, 'tpot_p95').textContent).toBe('35.00')
    // No cell ever renders a statistics object.
    expect(document.body.textContent).not.toContain('percentile_method')
    expect(document.body.textContent).not.toContain('{')
  })

  it('keeps a null value distinct from a measured zero', () => {
    const missing = makeRun({ run_key: 'missing', run_id: 'missing', ttft: makeDistribution({ p95: null }) })
    const zero = makeRun({ run_key: 'zero', run_id: 'zero', tpot: makeDistribution({ mean: 0 }) })
    renderTable({ runs: [missing, zero] })
    expect(bodyCell(0, 'ttft_p95').textContent).toBe('—')
    expect(bodyCell(1, 'tpot_mean').textContent).toBe('0.00')
  })

  it('asks the server to sort the whole run set when a metric header is clicked', () => {
    const onSort = vi.fn()
    renderTable({ onSort })
    const buttons = screen.getAllByRole('button', { name: /P95/ })
    fireEvent.click(buttons[0])
    expect(onSort).toHaveBeenCalledWith('ttft_p95', 'desc')
  })

  it('renders group section headers in the default order so a page keeps its grouping', () => {
    const first = makeRun({ run_key: 'a', run_id: 'a' })
    const second = makeRun({ run_key: 'b', run_id: 'b' })
    renderTable({ runs: [first, second] })
    expect(screen.getByText('generation · 0% prefix · OSL 128 · closed-loop')).toBeInTheDocument()
  })

  it('renders flat rows with the tier caption when sorted by a metric', () => {
    renderTable({ sort: 'ttft_p95', order: 'desc' })
    expect(screen.getByText('OSL 128 · 0% prefix · closed-loop')).toBeInTheDocument()
    expect(screen.queryByText('generation · 0% prefix · OSL 128 · closed-loop')).toBeNull()
  })

  it('expands a run into its sample population and source by mouse and keyboard', () => {
    renderTable()
    const row = dataRows()[0]
    fireEvent.click(row)
    expect(screen.getByText('Valid samples').parentElement?.textContent).toContain('100 / 100 / 100')
    expect(screen.getByText('Population').parentElement?.textContent).toContain('successful_profiling_requests')
    fireEvent.keyDown(dataRows()[0], { key: 'Enter' })
    expect(screen.queryByText('Valid samples')).toBeNull()
  })

  it('pages through all rows without hiding the total', () => {
    const onPage = vi.fn()
    renderTable({ total: 250, onPage })
    expect(screen.getByText(/250\/250 runs/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '2' }))
    expect(onPage).toHaveBeenCalledWith(100)
  })

  it('shows failure status as text, not as a zero measurement', () => {
    const failed = makeRun({
      lifecycle: 'error',
      availability: 'unavailable',
      ttft: makeDistribution({ availability: 'unavailable', count: 0, p95: null }),
    })
    renderTable({ runs: [failed] })
    expect(screen.getByText('Error')).toBeInTheDocument()
    expect(bodyCell(0, 'ttft_p95').textContent).toBe('—')
  })
})
