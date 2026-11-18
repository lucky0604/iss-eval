import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { importPerfArtifact } from '@/api/perf'
import PerfArtifactImportPanel from './PerfArtifactImportPanel'

vi.mock('@/api/perf', () => ({ importPerfArtifact: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve() })

it('submits the server artifact path and reports the imported archive', async () => {
  const onImported = vi.fn()
  vi.mocked(importPerfArtifact).mockResolvedValue({
    task_id: 'import_1', subrun_id: 'single', root_path: '/outputs', path: 'imports/import_1/perf',
    quality: 'partial', quality_reasons: ['profiling_window_unverified'], profiling_requests: 192, metric_count: 10,
  })
  render(<LocaleProvider><PerfArtifactImportPanel onImported={onImported} /></LocaleProvider>)

  fireEvent.change(screen.getByLabelText(/Artifact directory/), { target: { value: '/tmp/aiperf_run' } })
  fireEvent.change(screen.getByLabelText(/Model name/), { target: { value: 'GLM-5.3-Flash-NVFP4' } })
  fireEvent.change(screen.getByLabelText(/Concurrency/), { target: { value: '64' } })
  fireEvent.change(screen.getByLabelText(/Maximum output tokens/), { target: { value: '1024' } })
  fireEvent.click(screen.getByRole('button', { name: 'Import artifacts' }))
  await flush()

  expect(importPerfArtifact).toHaveBeenCalledWith({
    raw_dir: '/tmp/aiperf_run', model: 'GLM-5.3-Flash-NVFP4', load_mode: 'closed_loop',
    concurrency: 64, output_token_limit: 1024,
  })
  expect(onImported).toHaveBeenCalled()
  expect(screen.getByText(/Imported partial · 192 requests/)).toBeInTheDocument()
  expect(screen.getByText('imports/import_1/perf')).toBeInTheDocument()
})

it('shows the backend error without claiming success', async () => {
  const onImported = vi.fn()
  vi.mocked(importPerfArtifact).mockRejectedValue(new Error('raw_dir must be an existing directory'))
  render(<LocaleProvider><PerfArtifactImportPanel onImported={onImported} /></LocaleProvider>)

  fireEvent.change(screen.getByLabelText(/Artifact directory/), { target: { value: '/missing' } })
  fireEvent.change(screen.getByLabelText(/Model name/), { target: { value: 'm' } })
  fireEvent.change(screen.getByLabelText(/Maximum output tokens/), { target: { value: '2' } })
  fireEvent.click(screen.getByRole('button', { name: 'Import artifacts' }))
  await flush()

  expect(screen.getByText(/raw_dir must be an existing directory/)).toBeInTheDocument()
  expect(onImported).not.toHaveBeenCalled()
})
