import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { getPerfRawArtifactUrl, getPerfRawArtifacts } from '@/api/perf'
import RawArtifactsPanel from './RawArtifactsPanel'

vi.mock('@/api/perf', () => ({
  getPerfRawArtifacts: vi.fn(),
  getPerfRawArtifactUrl: vi.fn((_root: string, _path: string, name: string) => `/raw/${name}`),
}))
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('lists raw artifacts with size and download links', async () => {
  vi.mocked(getPerfRawArtifacts).mockResolvedValue({
    files: [
      { name: 'profile_export.jsonl', size: 2048 },
      { name: 'gpu_telemetry_export.jsonl', size: 4 * 1024 * 1024 },
    ],
  })
  await act(async () => {
    render(<LocaleProvider><RawArtifactsPanel rootPath="/outputs" path="imports/a/perf" /></LocaleProvider>)
    await Promise.resolve()
    await Promise.resolve()
  })
  expect(getPerfRawArtifacts).toHaveBeenCalledWith('/outputs', 'imports/a/perf', expect.any(AbortSignal))
  expect(screen.getByText('profile_export.jsonl')).toBeInTheDocument()
  expect(screen.getByText('2.0 KiB')).toBeInTheDocument()
  expect(screen.getByText('4.0 MiB')).toBeInTheDocument()
  expect(screen.getAllByRole('link')[0]).toHaveAttribute('href', '/raw/profile_export.jsonl')
  expect(getPerfRawArtifactUrl).toHaveBeenCalledWith('/outputs', 'imports/a/perf', 'profile_export.jsonl')
})

it('shows the empty state when no raw files are archived', async () => {
  vi.mocked(getPerfRawArtifacts).mockResolvedValue({ files: [] })
  await act(async () => {
    render(<LocaleProvider><RawArtifactsPanel rootPath="/outputs" path="imports/a/perf" /></LocaleProvider>)
    await Promise.resolve()
    await Promise.resolve()
  })
  expect(screen.getByText(/No raw artifacts/)).toBeInTheDocument()
})
