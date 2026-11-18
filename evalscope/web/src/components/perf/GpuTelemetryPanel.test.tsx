import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { getPerfNodeSeries } from '@/api/perf'
import type { NodeMetricsAnalysis, NodeMetricWindow } from '@/api/generated/contracts'
import GpuTelemetryPanel from './GpuTelemetryPanel'

vi.mock('@/api/perf', () => ({ getPerfNodeSeries: vi.fn(), getCacheNodeSeries: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

function metric(key: string, name: string, unit: string): NodeMetricWindow['metric'] {
  return {
    engine: 'dcgm',
    upstream_version: 'dcgm-exporter',
    name,
    key,
    kind: 'gauge',
    unit,
    source_unit: unit,
    source_url: 'https://example.invalid',
  }
}

const analysis: NodeMetricsAnalysis = {
  availability: 'partial',
  max_gap_ns: 15,
  window_source: 'aiperf_phase',
  window: { start_ns: 10, end_ns: 80 },
  series: [
    {
      node_id: 'head01-04', role: 'gpu', engine: 'dcgm', labels: { gpu: '0', hostname: 'head01-04' },
      window_source: 'aiperf_phase', metric: metric('gpu_utilization', 'dcgm:DCGM_FI_DEV_GPU_UTIL', 'percent'),
      statistic: { availability: 'available', value: 88, sample_peak: 91, sample_count: 2 },
    },
    {
      node_id: 'head01-04', role: 'gpu', engine: 'dcgm', labels: { gpu: '0', hostname: 'head01-04' },
      window_source: 'aiperf_phase', metric: metric('gpu_memory_used', 'dcgm:DCGM_FI_DEV_FB_USED', 'MiB'),
      statistic: { availability: 'available', value: 50688, sample_peak: 50688, sample_count: 2 },
    },
    {
      node_id: 'head01-04', role: 'gpu', engine: 'dcgm', labels: { gpu: '0', hostname: 'head01-04' },
      window_source: 'aiperf_phase', metric: metric('gpu_power_usage', 'dcgm:DCGM_FI_DEV_POWER_USAGE', 'W'),
      statistic: { availability: 'available', value: 300.5, sample_peak: 320, sample_count: 2 },
    },
    {
      node_id: 'head01-04', role: 'gpu', engine: 'dcgm', labels: { gpu: '0', hostname: 'head01-04' },
      window_source: 'aiperf_phase', metric: metric('gpu_temperature', 'dcgm:DCGM_FI_DEV_GPU_TEMP', 'celsius'),
      statistic: { availability: 'available', value: 62, sample_peak: 64, sample_count: 2 },
    },
    {
      node_id: 'head01-04', role: 'gpu', engine: 'dcgm', labels: { gpu: '0', hostname: 'head01-04' },
      window_source: 'aiperf_phase', metric: metric('gpu_xid_errors', 'dcgm:DCGM_FI_DEV_XID_ERRORS', 'count'),
      statistic: { availability: 'available', value: 0, sample_peak: 0, sample_count: 2 },
    },
  ],
}

it('renders GPU charts and secondary GPU metrics without per-request attribution', async () => {
  vi.mocked(getPerfNodeSeries).mockResolvedValue({
    series: {
      total: 2, max_points: 500, aggregation: 'raw', downsample: 'none',
      samples: [
        { timestamp_ns: 10, node_id: 'head01-04', role: 'gpu', engine: 'dcgm', endpoint: 'prometheus', metric_name: 'dcgm:DCGM_FI_DEV_GPU_UTIL', metric_type: 'gauge', labels: { gpu: '0', hostname: 'head01-04' }, observation: { value: 88 } },
        { timestamp_ns: 80, node_id: 'head01-04', role: 'gpu', engine: 'dcgm', endpoint: 'prometheus', metric_name: 'dcgm:DCGM_FI_DEV_GPU_UTIL', metric_type: 'gauge', labels: { gpu: '0', hostname: 'head01-04' }, observation: { value: 91 } },
      ],
    },
  })

  await act(async () => {
    render(<LocaleProvider><GpuTelemetryPanel analysis={analysis} archive={{ rootPath: '/outputs', path: 'task/perf' }} /></LocaleProvider>)
  })

  expect(screen.getByLabelText('GPU telemetry')).toBeInTheDocument()
  expect(screen.getByLabelText('GPU node')).toHaveValue('head01-04')
  expect(screen.getByText(/not attributed to individual requests/)).toBeInTheDocument()
  expect(screen.getByText(/gpu_xid_errors/)).toBeInTheDocument()
  expect(screen.getAllByRole('img', { name: 'Node metric time series' })).toHaveLength(4)
})

it('renders nothing when no GPU series exists', async () => {
  await act(async () => {
    render(<LocaleProvider><GpuTelemetryPanel analysis={{ ...analysis, series: [] }} archive={{ rootPath: '/outputs', path: 'task/perf' }} /></LocaleProvider>)
  })
  expect(screen.queryByLabelText('GPU telemetry')).not.toBeInTheDocument()
})
