import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { getCacheNodeSeries } from '@/api/perf'
import type { NodeMetricsAnalysis } from '@/api/generated/contracts'
import NodeWindowPanel from './NodeWindowPanel'

vi.mock('@/api/perf', () => ({ getCacheNodeSeries: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('shows node-scoped cache and P/D statistics with coverage, gaps and unavailable values', async () => {
  vi.mocked(getCacheNodeSeries).mockResolvedValue({ series: { total: 3, max_points: 500, aggregation: 'raw', downsample: 'none', samples: [
    { timestamp_ns: 10, node_id: 'p', role: 'prefill', engine: 'vllm', endpoint: 'host', metric_name: 'vllm:num_requests_waiting', metric_type: 'gauge', labels: {}, observation: { value: 0 } },
    { timestamp_ns: 20, node_id: 'p', role: 'prefill', engine: 'vllm', endpoint: 'host', metric_name: 'vllm:num_requests_waiting', metric_type: 'gauge', labels: {}, observation: { value: 2 } },
    { timestamp_ns: 80, node_id: 'p', role: 'prefill', engine: 'vllm', endpoint: 'host', metric_name: 'vllm:num_requests_waiting', metric_type: 'gauge', labels: {}, observation: { value: 1 } },
  ] } })
  const analysis: NodeMetricsAnalysis = { availability: 'partial', max_gap_ns: 15, window_source: 'observed_request_bounds',
    window: { start_ns: 10, end_ns: 80 }, scrapes: [{ node_id: 'p', observed_successful_scrapes: 3 }],
    cache_ratios: [{ node_id: 'p', role: 'prefill', labels: {}, ratio: .8, hit_tokens: 8, lookup_tokens: 10, availability: 'available' }],
    series: [{ node_id: 'p', role: 'prefill', engine: 'vllm', labels: {}, window_source: 'observed_request_bounds',
      metric: { engine: 'vllm', upstream_version: '0.10.2', name: 'vllm:num_requests_waiting', key: 'waiting_requests', kind: 'gauge', unit: 'requests', source_unit: 'requests', source_url: 'https://example.invalid' },
      statistic: { availability: 'partial', value: 1, sample_peak: 2, sample_count: 3, coverage: .5, boundary: 'step', reason: 'scrape_gap' } },
    { node_id: 'd', role: 'decode', engine: 'sglang', labels: {}, window_source: 'observed_request_bounds',
      metric: { engine: 'sglang', upstream_version: '0.5.23', name: 'sglang:kv_transfer_latency_ms', key: 'kv_transfer_latency', kind: 'histogram', unit: 'seconds', source_unit: 'milliseconds', source_url: 'https://example.invalid' },
      histogram: { availability: 'missing', metric_key: 'kv_transfer_latency', unit: 'seconds', window_start_ns: 10, window_end_ns: 80, reason: 'window_not_bracketed' } }] }
  await act(async () => { render(<LocaleProvider><NodeWindowPanel analysis={analysis} taskId="task" subrunId="run" /></LocaleProvider>) })
  expect(screen.getByText(/lookup-token hit ratio: 0.8/)).toBeInTheDocument()
  expect(screen.getByText('3 / 0.5')).toBeInTheDocument()
  expect(screen.getByText(/scrape_gap/)).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Node metric time series' }).querySelectorAll('polyline')).toHaveLength(1)
  fireEvent.change(screen.getByLabelText('Node'), { target: { value: 'd' } })
  expect(screen.getByText(/window_not_bracketed/)).toBeInTheDocument()
  expect(screen.getByText(/P99 —/)).toBeInTheDocument()
})

it('requests exact labels and window and identifies uniformly sampled raw points', async () => {
  vi.mocked(getCacheNodeSeries).mockResolvedValue({ series: { total: 1200, max_points: 2, aggregation: 'raw', downsample: 'uniform_index', samples: [
    { timestamp_ns: 10, node_id: 'p', role: 'prefill', engine: 'vllm', endpoint: 'host', metric_name: 'vllm:num_requests_waiting', metric_type: 'gauge', labels: { model: 'm' }, observation: { value: 0 } },
    { timestamp_ns: 80, node_id: 'p', role: 'prefill', engine: 'vllm', endpoint: 'host', metric_name: 'vllm:num_requests_waiting', metric_type: 'gauge', labels: { model: 'm' }, observation: { value: 1 } },
  ] } })
  const analysis: NodeMetricsAnalysis = { availability: 'partial', max_gap_ns: 15, window_source: 'observed_request_bounds',
    window: { start_ns: 10, end_ns: 80 }, series: [{ node_id: 'p', role: 'prefill', engine: 'vllm', labels: { model: 'm' }, window_source: 'observed_request_bounds',
      metric: { engine: 'vllm', upstream_version: '0.10.2', name: 'vllm:num_requests_waiting', key: 'waiting_requests', kind: 'gauge', unit: 'requests', source_unit: 'requests', source_url: 'https://example.invalid' } }] }
  await act(async () => { render(<LocaleProvider><NodeWindowPanel analysis={analysis} taskId="task" subrunId="run" /></LocaleProvider>) })
  expect(getCacheNodeSeries).toHaveBeenCalledWith('task', 'run', expect.objectContaining({ labels: { model: 'm' }, start: 10, end: 80 }))
  expect(screen.getByText(/Uniform index selection of raw samples: 2 \/ 1200/)).toHaveTextContent('Peaks may be omitted')
  expect(screen.getByRole('img', { name: 'Node metric time series' }).querySelectorAll('polyline')).toHaveLength(0)
})
