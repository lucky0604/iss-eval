import type { ComponentProps } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { makeRun } from '@/test/fixtures/reportRun'
import ReportCharts from './ReportCharts'

const { getPerfReportCharts } = vi.hoisted(() => ({ getPerfReportCharts: vi.fn() }))

vi.mock('@/api/perfReports', () => ({ getPerfReportCharts }))

function dimensions(isl: number, concurrency: number, osl = 128) {
  return { ...makeRun().dimensions, input_tokens: isl, output_token_limit: osl, concurrency }
}

function response(metric: string, statistic: string, duplicated = false) {
  return {
    metric,
    statistic,
    snapshot_id: 's1',
    series: [
      {
        facet: 'facet-a',
        facet_dimensions: dimensions(1024, 1, 128),
        load_mode: 'closed_loop',
        points: [
          { run_key: 'run-0', run_id: 'run-0', order: 0, dimensions: dimensions(1024, 1), value: 0.25, availability: 'available' },
          ...(duplicated
            ? [
                { run_key: 'run-1', run_id: 'run-1', order: 1, dimensions: dimensions(1024, 8), value: 0.4, availability: 'available' },
                { run_key: 'run-2', run_id: 'run-2', order: 2, dimensions: dimensions(1024, 16), value: null, availability: 'unavailable', reason: 'failed' },
              ]
            : []),
        ],
      },
      {
        facet: 'facet-b',
        facet_dimensions: dimensions(4096, 0, 256),
        load_mode: 'open_loop',
        points: [
          { run_key: 'run-9', run_id: 'run-9', order: 9, dimensions: { ...dimensions(4096, 0, 256), load_mode: 'open_loop', rate: 3.5, concurrency: null }, value: 0.8, availability: 'available' },
        ],
      },
    ],
  }
}

function renderCharts(overrides: Partial<ComponentProps<typeof ReportCharts>> = {}) {
  return render(
    <LocaleProvider>
      <ReportCharts location={{ taskId: 'task-1' }} available onOpenRun={vi.fn()} {...overrides} />
    </LocaleProvider>,
  )
}

// The global setup installs fake timers; async reads need real ones.
beforeAll(() => {
  vi.useRealTimers()
})

describe('matrix charts', () => {
  beforeEach(() => {
    getPerfReportCharts.mockReset()
    getPerfReportCharts.mockImplementation((_location: unknown, query: { metric: string; statistic: string }) =>
      Promise.resolve(response(query.metric, query.statistic, query.metric === 'ttft')),
    )
  })

  it('requests all six metrics over the full run set, P95 by default for latencies', async () => {
    renderCharts()
    await waitFor(() => expect(getPerfReportCharts).toHaveBeenCalledTimes(6))
    const calls = getPerfReportCharts.mock.calls.map((call) => call[1] as { metric: string; statistic: string })
    expect(calls.map((call) => call.metric)).toEqual([
      'ttft',
      'tpot',
      'latency',
      'input_tps',
      'output_tps',
      'rpm',
    ])
    expect(calls.filter((call) => ['ttft', 'tpot', 'latency'].includes(call.metric)).every((call) => call.statistic === 'p95')).toBe(true)
    expect(calls.filter((call) => !['ttft', 'tpot', 'latency'].includes(call.metric)).every((call) => call.statistic === 'mean')).toBe(true)
  })

  it('plots every measured point and skips unavailable ones instead of zeroing them', async () => {
    renderCharts()
    const chart = await screen.findByRole('img', { name: 'TTFT · ms' })
    expect(chart.querySelectorAll('circle')).toHaveLength(3)
    expect(chart.querySelectorAll('circle')[2].closest('g')?.getAttribute('aria-label')).toBe('Open requests for run-9')
  })

  it('opens the requests of the clicked run', async () => {
    const onOpenRun = vi.fn()
    renderCharts({ onOpenRun })
    const chart = await screen.findByRole('img', { name: 'TTFT · ms' })
    fireEvent.click(chart.querySelectorAll('circle')[0])
    expect(onOpenRun).toHaveBeenCalledWith('run-0')
  })

  it('switches the statistic for latency charts and reloads from the server', async () => {
    renderCharts()
    await waitFor(() => expect(getPerfReportCharts).toHaveBeenCalledTimes(6))
    fireEvent.click(screen.getByRole('button', { name: 'P90' }))
    await waitFor(() => {
      // Only the latest reload matters; earlier calls used the P95 default.
      const calls = getPerfReportCharts.mock.calls
        .slice(-6)
        .map((call) => call[1] as { metric: string; statistic: string })
      expect(calls).toHaveLength(6)
      expect(calls.filter((call) => call.metric === 'ttft')[0].statistic).toBe('p90')
      expect(calls.filter((call) => call.metric === 'rpm')[0].statistic).toBe('mean')
    })
  })


  it('breaks the line at a missing measurement instead of joining across it', async () => {
    const base = { ...makeRun().dimensions, input_tokens: 1024, output_token_limit: 128 }
    getPerfReportCharts.mockImplementation((_location: unknown, query: { metric: string; statistic: string }) =>
      Promise.resolve(
        query.metric === 'ttft'
          ? {
              metric: 'ttft',
              statistic: query.statistic,
              snapshot_id: 's1',
              series: [
                {
                  facet: 'facet-a',
                  facet_dimensions: base,
                  load_mode: 'closed_loop',
                  points: [
                    { run_key: 'run-0', run_id: 'run-0', order: 0, dimensions: { ...base, concurrency: 1 }, value: 0.25, availability: 'available' },
                    { run_key: 'run-1', run_id: 'run-1', order: 1, dimensions: { ...base, concurrency: 4 }, value: null, availability: 'unavailable', reason: 'run_not_started' },
                    { run_key: 'run-2', run_id: 'run-2', order: 2, dimensions: { ...base, concurrency: 8 }, value: 0.4, availability: 'available' },
                  ],
                },
              ],
            }
          : response(query.metric, query.statistic),
      ),
    )
    renderCharts()
    const chart = await screen.findByRole('img', { name: 'TTFT · ms' })
    // Two separate paths, never one line that bridges the gap.
    expect(chart.querySelectorAll('polyline')).toHaveLength(2)
    expect(chart.querySelectorAll('circle')).toHaveLength(2)
  })

  it('offers one selectable facet per output limit instead of collapsing them', async () => {
    renderCharts()
    const select = await screen.findByRole('combobox')
    const labels = within(select)
      .getAllByRole('option')
      .map((option) => option.textContent ?? '')
    expect(labels.some((label) => label.includes('OSL 128'))).toBe(true)
    expect(labels.some((label) => label.includes('OSL 256'))).toBe(true)
    expect(labels.filter((label) => label.includes('OSL'))).toHaveLength(2)
  })

  it('keeps repetitions as separate series instead of joining them into one curve', async () => {
    const base = { ...makeRun().dimensions, input_tokens: 1024, output_token_limit: 128 }
    const repeated = (run: string, value: number, repetition: number) => ({
      run_key: run,
      run_id: run,
      order: repetition,
      dimensions: { ...base, concurrency: 1, repetition },
      value,
      availability: 'available',
    })
    getPerfReportCharts.mockImplementation((_location: unknown, query: { metric: string; statistic: string }) =>
      Promise.resolve(
        query.metric === 'ttft'
          ? {
              metric: 'ttft',
              statistic: query.statistic,
              snapshot_id: 's1',
              series: [
                { facet: 'facet-a', facet_dimensions: base, load_mode: 'closed_loop', repetition: 0, points: [repeated('run-0', 0.25, 0)] },
                { facet: 'facet-a', facet_dimensions: base, load_mode: 'closed_loop', repetition: 1, points: [repeated('run-1', 0.3, 1)] },
              ],
            }
          : response(query.metric, query.statistic),
      ),
    )
    renderCharts()
    const chart = await screen.findByRole('img', { name: 'TTFT · ms' })
    const figure = chart.closest('figure') as HTMLElement
    expect(within(figure).getByText(/repeat 1/)).toBeInTheDocument()
    expect(within(figure).getByText(/repeat 2/)).toBeInTheDocument()
    expect(chart.querySelectorAll('polyline')).toHaveLength(2)
  })

  it('never renders a statistics object inside the chart', async () => {
    renderCharts()
    await screen.findByRole('img', { name: 'TTFT · ms' })
    expect(document.body.textContent).not.toContain('availability')
    expect(document.body.textContent).not.toContain('{')
  })
})
