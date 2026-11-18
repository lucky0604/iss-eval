import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { importPerfArtifact } from '@/api/perf'
import PerfArtifactImportPanel from './PerfArtifactImportPanel'

vi.mock('@/api/perf', () => ({ importPerfArtifact: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve() })

const importedRun = (taskId: string, sourceDir: string, concurrency: number) => ({
  task_id: taskId, subrun_id: 'single', root_path: '/outputs', path: `imports/${taskId}/perf`,
  source_dir: sourceDir, model: 'DeepSeek-V4.1-Flash-NVFP4', load_mode: 'closed_loop' as const,
  concurrency, rate: null, quality: 'partial' as const, quality_reasons: ['profiling_window_unverified'],
  profiling_requests: 48, metric_count: 10,
  inferred_fields: ['model', 'load_mode', 'concurrency', 'output_token_limit'],
})

it('imports a batch directory without typing the run identity', async () => {
  const onImported = vi.fn()
  vi.mocked(importPerfArtifact).mockResolvedValue({
    root_path: '/outputs',
    imported: [
      importedRun('import_1', 'exports/conc_16_inputlen_8192_osl_1024', 16),
      importedRun('import_2', 'exports/conc_32_inputlen_8192_osl_1024', 32),
    ],
    failed: [],
  })
  render(<LocaleProvider><PerfArtifactImportPanel onImported={onImported} /></LocaleProvider>)

  fireEvent.change(screen.getByLabelText(/Artifact directory/), {
    target: { value: '/data/jobs/ds_v4.1/aiperf_results_20261113_030736' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Import artifacts' }))
  await flush()

  expect(importPerfArtifact).toHaveBeenCalledWith({ raw_dir: '/data/jobs/ds_v4.1/aiperf_results_20261113_030736' })
  expect(onImported).toHaveBeenCalled()
  expect(screen.getByText(/Imported 2 archive\(s\), failed 0/)).toBeInTheDocument()
  expect(screen.getByText('imports/import_1/perf')).toBeInTheDocument()
  expect(screen.getAllByText(/inferred: model, load_mode, concurrency, output_token_limit/)).toHaveLength(2)
})

it('sends explicit values only when the operator confirms them', async () => {
  const onImported = vi.fn()
  vi.mocked(importPerfArtifact).mockResolvedValue({ root_path: '/outputs', imported: [], failed: [] })
  render(<LocaleProvider><PerfArtifactImportPanel onImported={onImported} /></LocaleProvider>)

  fireEvent.change(screen.getByLabelText(/Artifact directory/), { target: { value: '/tmp/aiperf_run' } })
  fireEvent.change(screen.getByLabelText(/Model name/), { target: { value: 'GLM-5.3-Flash-NVFP4' } })
  fireEvent.change(screen.getByLabelText(/Load mode/), { target: { value: 'closed_loop' } })
  fireEvent.change(screen.getByLabelText(/Concurrency/), { target: { value: '64' } })
  fireEvent.change(screen.getByLabelText(/Maximum output tokens/), { target: { value: '1024' } })
  fireEvent.click(screen.getByRole('button', { name: 'Import artifacts' }))
  await flush()

  expect(importPerfArtifact).toHaveBeenCalledWith({
    raw_dir: '/tmp/aiperf_run', model: 'GLM-5.3-Flash-NVFP4', load_mode: 'closed_loop',
    concurrency: 64, output_token_limit: 1024,
  })
})

it('lists the runs a batch could not archive', async () => {
  const onImported = vi.fn()
  vi.mocked(importPerfArtifact).mockResolvedValue({
    root_path: '/outputs',
    imported: [importedRun('import_1', 'exports/conc_16', 16)],
    failed: [{ source_dir: 'exports/conc_32', error: 'load_mode is not recorded in the artifacts' }],
  })
  render(<LocaleProvider><PerfArtifactImportPanel onImported={onImported} /></LocaleProvider>)

  fireEvent.change(screen.getByLabelText(/Artifact directory/), { target: { value: '/tmp/batch' } })
  fireEvent.click(screen.getByRole('button', { name: 'Import artifacts' }))
  await flush()

  expect(screen.getByText(/Imported 1 archive\(s\), failed 1/)).toBeInTheDocument()
  expect(screen.getByText(/exports\/conc_32: load_mode is not recorded/)).toBeInTheDocument()
})

it('shows the backend error without claiming success', async () => {
  const onImported = vi.fn()
  vi.mocked(importPerfArtifact).mockRejectedValue(
    new Error("model conflicts with the artifacts: request='other' artifacts='mock-model'"),
  )
  render(<LocaleProvider><PerfArtifactImportPanel onImported={onImported} /></LocaleProvider>)

  fireEvent.change(screen.getByLabelText(/Artifact directory/), { target: { value: '/tmp/aiperf_run' } })
  fireEvent.change(screen.getByLabelText(/Model name/), { target: { value: 'other' } })
  fireEvent.click(screen.getByRole('button', { name: 'Import artifacts' }))
  await flush()

  expect(screen.getByText(/conflicts with the artifacts/)).toBeInTheDocument()
  expect(onImported).not.toHaveBeenCalled()
})
