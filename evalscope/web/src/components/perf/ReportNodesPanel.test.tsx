import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { makeRun } from '@/test/fixtures/reportRun'
import type { ReportRun, ReportRunNodes } from '@/api/generated/contracts'
import ReportNodesPanel from './ReportNodesPanel'

function nodesRun(overrides: Partial<ReportRunNodes> = {}): ReportRun {
  return makeRun({
    run_id: 'isl1024-osl128-c8-r0',
    run_key: 'run-nodes',
    nodes: {
      availability: 'available',
      window_source: 'aiperf_phase',
      timed_requests: 198,
      total_requests: 200,
      max_gap_ns: 1_500_000_000,
      metrics: [
        {
          node_id: 'prefill-0',
          role: 'prefill',
          engine: 'vllm',
          labels: { instance: 'vllm-0' },
          metric_name: 'gpu_cache_usage_perc',
          metric_unit: 'ratio',
          window_source: 'aiperf_phase',
          statistic: {
            availability: 'available',
            value: 0.42,
            sample_peak: 0.87,
            sample_count: 12,
            coverage: 1,
            boundary: 'step',
          },
        },
      ],
      cache_ratios: [
        {
          node_id: 'prefill-0',
          role: 'prefill',
          availability: 'available',
          hit_tokens: 900,
          lookup_tokens: 1000,
          ratio: 0.9,
          semantics: 'prefix_lookup_tokens',
        },
      ],
      ...overrides,
    },
  })
}

function renderPanel(runs: ReportRun[], available = true) {
  return render(
    <LocaleProvider>
      <ReportNodesPanel runs={runs} available={available} />
    </LocaleProvider>,
  )
}

beforeEach(() => {
  localStorage.setItem('evalscope-locale', 'en')
})

describe('node metrics panel', () => {
  it('shows every persisted node value as its own numeric cell', () => {
    renderPanel([nodesRun()])
    // The same node appears in the metric table and the separate cache table.
    expect(screen.getAllByText('prefill-0').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('gpu_cache_usage_perc')).toBeInTheDocument()
    expect(screen.getByText('0.4200')).toBeInTheDocument()
    expect(screen.getByText('0.8700')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('instance=vllm-0')).toBeInTheDocument()
    expect(screen.getByText(/window aiperf_phase · timed 198\/200/)).toBeInTheDocument()
    // The node row is a number, never a serialized statistics object.
    expect(document.body.textContent).not.toContain('"value"')
  })

  it('states an unavailable node observation instead of hiding it', () => {
    renderPanel([makeRun()])
    expect(screen.getByText(/no run has a persisted node observation/i)).toBeInTheDocument()
    renderPanel([], false)
    expect(screen.getAllByText(/no readable node observation/i).length).toBeGreaterThan(0)
  })

  it('keeps node prefix cache ratios separate from response cache usage', () => {
    renderPanel([nodesRun()])
    expect(screen.getByText(/prefix cache ratios/i)).toBeInTheDocument()
    expect(screen.getByText('prefix_lookup_tokens')).toBeInTheDocument()
    expect(screen.getByText('900')).toBeInTheDocument()
    expect(screen.getByText('90.00')).toBeInTheDocument()
  })
})
