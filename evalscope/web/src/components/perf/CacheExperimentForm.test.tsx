import { useState } from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { DEFAULT_AIPERF, type AIPerfDraft } from '@/components/tasks/aiperfDraft'
import CacheExperimentForm from './CacheExperimentForm'
import { getPerfEngines, validatePerfExperiment, validatePerfSingle } from '@/api/perf'
import type { PerfPlanResponse } from '@/api/generated/contracts'

vi.mock('@/api/perf', () => ({ getPerfEngines: vi.fn(), validatePerfExperiment: vi.fn(), validatePerfSingle: vi.fn() }))
const plan = (requests = 30): PerfPlanResponse => ({ engine_available: true, warnings: [], plan: {
  plan_hash: 'hash', configuration_hash: 'config', tokenizer_id: 'tokenizer', template_id: 'template',
  subruns: [], profiling_requests: requests, primer_requests: 2, max_profiling_output_tokens: 300,
} })
const connection = { model: 'model', url: 'http://localhost:1234/v1', apiKey: 'model-secret', alias: '', api: 'openai' as const }

function Harness({ submit = async () => {}, initial = DEFAULT_AIPERF }: {
  submit?: (config: Record<string, unknown>) => Promise<void>, initial?: AIPerfDraft
}) {
  const [draft, setDraft] = useState<AIPerfDraft>(structuredClone(initial))
  return <LocaleProvider><CacheExperimentForm connection={connection} onSubmit={submit} disabled={false}
    draft={draft} onChange={setDraft} onValidateConnection={() => ({})} /></LocaleProvider>
}

beforeEach(() => {
  vi.mocked(getPerfEngines).mockResolvedValue({ engines: [{ engine: 'aiperf', available: true }] })
  vi.mocked(validatePerfExperiment).mockResolvedValue(plan())
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('previews and submits actual ratios, loads, labels and independent node credentials', async () => {
  const submit = vi.fn().mockResolvedValue(undefined)
  await act(async () => { render(<Harness submit={submit} />) })
  expect(screen.getByText(/AIPerf 0.13.0: Available/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Tokenizer path or ModelScope model ID'), { target: { value: '/tokenizer' } })
  fireEvent.change(screen.getByLabelText('Load points (comma-separated)'), { target: { value: '1, 4' } })
  fireEvent.change(screen.getByLabelText('Deployment'), { target: { value: 'pd' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add node' }))
  fireEvent.click(screen.getByRole('button', { name: 'Add node' }))
  const first = screen.getByRole('group', { name: 'Node 1' })
  const second = screen.getByRole('group', { name: 'Node 2' })
  fireEvent.change(within(first).getByLabelText('Node ID'), { target: { value: 'p' } })
  fireEvent.change(within(first).getByLabelText('Prometheus URL'), { target: { value: 'http://p:9000/metrics' } })
  fireEvent.change(within(first).getByLabelText('Label filter (key=value, comma-separated)'), { target: { value: 'model=alpha' } })
  fireEvent.change(within(first).getByLabelText('Metrics bearer token (not saved)'), { target: { value: 'p-secret' } })
  fireEvent.change(within(second).getByLabelText('Node ID'), { target: { value: 'd' } })
  fireEvent.change(within(second).getByLabelText('Node role'), { target: { value: 'decode' } })
  fireEvent.change(within(second).getByLabelText('Node engine'), { target: { value: 'sglang' } })
  fireEvent.change(within(second).getByLabelText('Prometheus URL'), { target: { value: 'http://d:9000/metrics' } })
  fireEvent.change(within(second).getByLabelText('Metrics bearer token (not saved)'), { target: { value: 'd-secret' } })
  fireEvent.click(screen.getByLabelText('Require node metrics'))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Preview plan' })) })
  const sent = vi.mocked(validatePerfExperiment).mock.calls[0][0]
  expect(sent).toMatchObject({ engine: 'aiperf', aiperf: {
    api_key: 'model-secret', tokenizer_path: '/tokenizer', deployment_mode: 'pd',
    cache_experiment: { target_ratios: [0, 0.8, 0.9], seed: 42 },
    load: { points: [{ concurrency: 1 }, { concurrency: 4 }] },
    telemetry: { required: true, targets: [
      { node_id: 'p', role: 'prefill', engine: 'vllm', url: 'http://p:9000/metrics', labels: { model: 'alpha' }, headers: { Authorization: 'Bearer p-secret' } },
      { node_id: 'd', role: 'decode', engine: 'sglang', url: 'http://d:9000/metrics', headers: { Authorization: 'Bearer d-secret' } },
    ] },
  } })
  fireEvent.click(screen.getByRole('button', { name: 'Start experiment' }))
  expect(submit).toHaveBeenCalledWith(sent)
  fireEvent.change(screen.getByLabelText('Target prefix reuse (%, comma-separated)'), { target: { value: '90' } })
  expect(screen.getByRole('button', { name: 'Start experiment' })).toBeDisabled()
  fireEvent.change(within(first).getByLabelText('Prometheus URL'), { target: { value: 'http://other:9000/metrics' } })
  expect(within(first).getByLabelText('Metrics bearer token (not saved)')).toHaveValue('')
})

it('focuses and describes the first invalid field, including dynamic nodes', async () => {
  let focusFrame: FrameRequestCallback | undefined
  const frame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    focusFrame = callback
    return 1
  })
  await act(async () => { render(<Harness />) })
  expect(screen.getByText(/AIPerf 0.13.0: Available/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Preview plan' }))
  const tokenizer = screen.getByLabelText('Tokenizer path or ModelScope model ID')
  act(() => { focusFrame?.(0) })
  expect(tokenizer).toHaveFocus()
  expect(tokenizer).toHaveAttribute('aria-invalid', 'true')
  expect(tokenizer).toHaveAttribute('aria-describedby')
  fireEvent.change(tokenizer, { target: { value: '/tokenizer' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add node' }))
  fireEvent.click(screen.getByRole('button', { name: 'Preview plan' }))
  const nodeId = screen.getByLabelText('Node ID')
  act(() => { focusFrame?.(0) })
  expect(nodeId).toHaveFocus()
  expect(nodeId).toHaveAttribute('aria-invalid', 'true')
  expect(validatePerfExperiment).not.toHaveBeenCalled()
  frame.mockRestore()
})

it('ignores an out-of-order preview and invalidates it when the draft changes', async () => {
  let resolveFirst!: (value: PerfPlanResponse) => void
  let resolveSecond!: (value: PerfPlanResponse) => void
  vi.mocked(validatePerfExperiment)
    .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
    .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve }))
  await act(async () => { render(<Harness initial={{ ...DEFAULT_AIPERF, tokenizer: '/tokenizer' }} />) })
  expect(screen.getByText(/AIPerf 0.13.0: Available/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Preview plan' }))
  fireEvent.change(screen.getByLabelText('Requests per run'), { target: { value: '20' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview plan' }))
  await act(async () => resolveSecond(plan(60)))
  expect(screen.getByText(/60 profiling requests/)).toBeInTheDocument()
  await act(async () => resolveFirst(plan(30)))
  expect(screen.getByText(/60 profiling requests/)).toBeInTheDocument()
  expect(screen.queryByText(/30 profiling requests/)).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Random seed'), { target: { value: '43' } })
  expect(screen.getByRole('button', { name: 'Start experiment' })).toBeDisabled()
})

it('preflights and submits one inline-prompt run with exact user text', async () => {
  const submit = vi.fn().mockResolvedValue(undefined)
  vi.mocked(validatePerfSingle).mockResolvedValue({ engine_available: true, profiling_requests: 2 })
  await act(async () => { render(<Harness submit={submit} initial={{ ...DEFAULT_AIPERF, tokenizer: '/tokenizer' }} />) })
  fireEvent.change(screen.getByLabelText('Workload'), { target: { value: 'single' } })
  fireEvent.change(screen.getByLabelText('Prompts (one per line; stored in this browser)'), { target: { value: 'first\n second ' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Preview plan' })) })
  expect(screen.getByText(/2 profiling requests/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Start single run' }))
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({ engine: 'aiperf', aiperf: expect.objectContaining({
    input_prompts: ['first', ' second '], load: expect.objectContaining({ request_count: 2, repetitions: 1 }),
  }) }))
  const request = vi.mocked(validatePerfSingle).mock.calls[0][0]
  expect((request.aiperf as Record<string, unknown>).cache_experiment).toBeUndefined()
})
