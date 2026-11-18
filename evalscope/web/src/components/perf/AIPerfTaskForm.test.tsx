import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import type { PerfPlanPreviewResponse, PerfSinglePlanResponse } from '@/api/generated/contracts'
import type { FieldErrors, ModelConnection } from '@/components/tasks/taskConfig'
import {
  defaultAIPerfDraft,
  type AIPerfNodeDraft,
  type AIPerfTaskDraft,
} from '@/components/tasks/aiperfDraft'
import AIPerfTaskForm from './AIPerfTaskForm'
import TaskRunnerShell, { type SubmitResult } from '@/components/tasks/TaskRunnerShell'
import { getPerfEngines, probePerfTelemetry, validatePerfSingle } from '@/api/perf'
import { previewPerfPlan, submitPerfPlan } from '@/api/perfPlans'

vi.mock('@/api/perf', () => ({
  getPerfEngines: vi.fn(),
  validatePerfSingle: vi.fn(),
  probePerfTelemetry: vi.fn(),
}))

vi.mock('@/api/perfPlans', () => ({
  previewPerfPlan: vi.fn(),
  exportPerfPlan: vi.fn(),
  submitPerfPlan: vi.fn(),
}))

const connection: ModelConnection = {
  model: 'served-model', url: 'http://localhost:1234/v1', apiKey: 'sk-model', alias: '', api: 'openai',
}

const node: AIPerfNodeDraft = {
  id: 'n1', nodeId: 'prefill', role: 'prefill', engine: 'vllm',
  url: 'http://10.0.0.1:9000/metrics', labels: '', bearerToken: '',
}

function readyDraft(patch: Partial<AIPerfTaskDraft> = {}): AIPerfTaskDraft {
  return {
    ...defaultAIPerfDraft(),
    tokenizer: 'tok',
    generated: { ...defaultAIPerfDraft().generated, maxModelLen: '32768' },
    ...patch,
  }
}

function monitoring(patch: Partial<AIPerfTaskDraft['monitoring']> = {}): AIPerfTaskDraft['monitoring'] {
  return { enabled: true, required: false, sampleInterval: '1', maxGap: '', nodes: [node], ...patch }
}

function planPreview(patch: Partial<PerfPlanPreviewResponse> = {}): PerfPlanPreviewResponse {
  return {
    schema_version: '1.1',
    contract_version: '1.1',
    compiler_version: '1.1',
    workload_semantics: 'managed_token_prefix',
    intent: 'benchmark',
    plan_hash: 'hash-1',
    normalized_hash: 'normalized',
    source_hash: 'source',
    semantic_fingerprint: 'semantic',
    normalized: { name: 'matrix' },
    runs: [],
    total_runs: 4,
    offset: 0,
    limit: 100,
    budgets: { formal_requests: 40, warmup_requests: 6, primer_requests: 0 },
    diagnostics: [],
    alignment: { overall: 'unknown', dimensions: [] },
    context: [],
    can_submit: true,
    requires_acknowledgement: [],
    acknowledged: [],
    engine_available: true,
    runtime: { runtime_status: 'available', runtime_version: '0.13.0' },
    ...patch,
  } as unknown as PerfPlanPreviewResponse
}

function singlePreview(patch: Partial<PerfSinglePlanResponse> = {}): PerfSinglePlanResponse {
  return { engine_available: true, profiling_requests: 2, warnings: [], ...patch } as PerfSinglePlanResponse
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function renderForm(
  initial: AIPerfTaskDraft,
  overrides: {
    onSubmit?: (config: Record<string, unknown>) => Promise<SubmitResult | void>
    onPlanSubmitted?: (taskId: string) => void
    onValidateConnection?: () => FieldErrors
    onAttemptTask?: (taskId: string) => void
    onSubmittingChange?: (submitting: boolean) => void
    connection?: ModelConnection
  } = {},
) {
  const onSubmit = vi.fn(overrides.onSubmit ?? (async () => {}))
  const onPlanSubmitted = vi.fn(overrides.onPlanSubmitted ?? (() => {}))
  const onAttemptTask = vi.fn(overrides.onAttemptTask ?? (() => {}))
  function Harness() {
    const [draft, setDraft] = useState(initial)
    return (
      <LocaleProvider>
        <AIPerfTaskForm connection={overrides.connection ?? connection} draft={draft} onChange={setDraft}
          onSubmit={onSubmit} disabled={false} onValidateConnection={overrides.onValidateConnection ?? (() => ({}))}
          onPlanSubmitted={onPlanSubmitted} onAttemptTask={onAttemptTask}
          onSubmittingChange={overrides.onSubmittingChange} />
      </LocaleProvider>
    )
  }
  const view = render(<Harness />)
  return { onSubmit, onPlanSubmitted, onAttemptTask, view }
}

const previewButton = () => screen.getByRole('button', { name: 'Preview test' })
const startButton = () => screen.getByRole('button', { name: 'Start test' })

/** Flush pending promises/effects; the suite runs on fake timers, so no polling. */
async function flush() {
  await act(async () => {
    await Promise.resolve()
  })
}

async function click(element: HTMLElement) {
  await act(async () => {
    fireEvent.click(element)
  })
}

async function renderReady(initial: AIPerfTaskDraft, overrides = {}) {
  const handlers = renderForm(initial, overrides)
  await flush()
  return handlers
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(getPerfEngines).mockResolvedValue(
    { engines: [{ engine: 'aiperf', available: true, reason: null }] } as never,
  )
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    callback(0)
    return 1
  })
})

afterEach(() => {
  cleanup()
  // resetAllMocks also drops queued `...Once` implementations, which would
  // otherwise leak into the next test.
  vi.resetAllMocks()
  vi.restoreAllMocks()
})

describe('input source', () => {
  it('waits for engine availability before allowing a preview', async () => {
    const engines = deferred<never>()
    vi.mocked(getPerfEngines).mockReturnValue(engines.promise)
    renderForm(readyDraft())
    expect(previewButton()).toBeDisabled()
    await act(async () => {
      engines.resolve({ engines: [{ engine: 'aiperf', available: true, reason: null }] } as never)
    })
    await flush()
    expect(previewButton()).toBeEnabled()
  })

  it('swaps the active branch while both keep their edits', async () => {
    await renderReady(readyDraft())
    fireEvent.change(screen.getByRole('combobox', { name: /Input source/ }), { target: { value: 'prompts' } })
    expect(screen.queryByLabelText(/declared_max_model_len/)).toBeNull()
    fireEvent.change(screen.getByLabelText(/aiperf\.input_prompts/), {
      target: { value: 'kept prompt' },
    })

    fireEvent.change(screen.getByRole('combobox', { name: /Input source/ }), { target: { value: 'generated' } })
    fireEvent.change(screen.getByLabelText(/workload\.input_lengths/), { target: { value: '4096' } })
    expect(screen.getByLabelText(/workload\.input_lengths/)).toHaveValue('4096')

    fireEvent.change(screen.getByRole('combobox', { name: /Input source/ }), { target: { value: 'prompts' } })
    expect(screen.getByLabelText(/aiperf\.input_prompts/)).toHaveValue('kept prompt')
  })

  it('uses the runtime prefix for generated metrics and the payload prefix for real prompts', async () => {
    await renderReady(readyDraft({ monitoring: monitoring() }))
    expect(screen.getByLabelText(/runtime\.telemetry\.targets\[\]\.node_id/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/aiperf\.telemetry\.targets\[\]\.node_id/)).toBeNull()
    fireEvent.change(screen.getByRole('combobox', { name: /Input source/ }), { target: { value: 'prompts' } })
    expect(screen.getByLabelText(/aiperf\.telemetry\.targets\[\]\.node_id/)).toBeInTheDocument()
  })
})

describe('generated validation and preview', () => {
  it('does not call the service and focuses the first invalid field', async () => {
    await renderReady(readyDraft({ tokenizer: '', generated: { ...readyDraft().generated, maxModelLen: '' } }))
    await click(previewButton())
    expect(previewPerfPlan).not.toHaveBeenCalled()
    const tokenizer = screen.getByLabelText(/tokenizer_path/)
    expect(tokenizer).toHaveAttribute('aria-invalid', 'true')
    expect(tokenizer).toHaveFocus()
  })

  it('opens the advanced section when the first invalid field is hidden there', async () => {
    const draft = readyDraft()
    draft.generated.seed = ''
    await renderReady(draft)
    await click(previewButton())
    expect(previewPerfPlan).not.toHaveBeenCalled()
    expect((screen.getByText('Advanced settings').closest('details') as HTMLDetailsElement).open).toBe(true)
    expect(document.getElementById('aiperf-seed')).toHaveFocus()
  })

  it('previews schema 1.1 and submits the same hash with the runtime binding after acknowledgement', async () => {
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview({ requires_acknowledgement: ['context_window_exceeded'] }))
    vi.mocked(submitPerfPlan).mockResolvedValue({ task_id: 'perf_task_1', state: 'queued', duplicate: false } as never)
    const { onPlanSubmitted } = await renderReady(readyDraft())
    await click(previewButton())

    const [content] = vi.mocked(previewPerfPlan).mock.calls[0]
    const document = JSON.parse(content)
    expect(document).toMatchObject({
      kind: 'iss-eval.perf-plan',
      schema_version: '1.1',
      connection: { model: 'served-model', tokenizer_path: 'tok', url: 'http://localhost:1234/v1' },
      deployment: { declared_max_model_len: 32768, mode: 'aggregated' },
      workload: { input_lengths: [1024, 8192], target_ratios: [0], prefix_pool_size: 1 },
    })
    expect(startButton()).toBeDisabled()
    const acknowledgement = screen.getByRole('checkbox', { name: 'context_window_exceeded' })
    fireEvent.click(acknowledgement)
    expect(startButton()).toBeEnabled()

    await click(startButton())
    const [submitted, taskId, options] = vi.mocked(submitPerfPlan).mock.calls[0]
    expect(submitted).toBe(content)
    expect(taskId).toMatch(/^perf_matrix_/)
    expect(options?.expectedPlanHash).toBe('hash-1')
    expect(options?.acknowledged).toEqual(['context_window_exceeded'])
    expect(options?.runtime).toEqual({ api_key: 'sk-model' })
    expect(onPlanSubmitted).toHaveBeenCalledWith('perf_task_1')
    expect(startButton()).toBeDisabled()
  })

  it('keeps the task id when the submission outcome is unknown', async () => {
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview())
    vi.mocked(submitPerfPlan)
      .mockRejectedValueOnce(new Error('connection reset'))
      .mockResolvedValueOnce({ task_id: 'perf_task_2', state: 'queued', duplicate: false } as never)
    const { onPlanSubmitted } = await renderReady(readyDraft())
    await click(previewButton())

    await click(startButton())
    expect(screen.getByText(/did not confirm the submission/)).toBeInTheDocument()
    await click(startButton())
    const first = vi.mocked(submitPerfPlan).mock.calls[0]
    const second = vi.mocked(submitPerfPlan).mock.calls[1]
    expect(second[1]).toBe(first[1])
    expect(onPlanSubmitted).toHaveBeenCalledWith('perf_task_2')
  })

  it('invalidates a successful preview when an active prefix value becomes invalid', async () => {
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview())
    const draft = readyDraft()
    draft.generated.comparePrefix = true
    draft.generated.ratios = '80'
    await renderReady(draft)
    await click(previewButton())
    expect(startButton()).toBeEnabled()

    fireEvent.change(document.getElementById('aiperf-pool')!, { target: { value: '' } })
    await click(startButton())
    expect(submitPerfPlan).not.toHaveBeenCalled()
    expect(document.getElementById('aiperf-pool')).toHaveAttribute('aria-invalid', 'true')
  })

  it('does not start a second invoke while a submission is still in flight', async () => {
    const pending = deferred<never>()
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview())
    vi.mocked(submitPerfPlan).mockReturnValue(pending.promise)
    await renderReady(readyDraft())
    await click(previewButton())
    await click(startButton())
    expect(submitPerfPlan).toHaveBeenCalledTimes(1)

    fireEvent.change(document.getElementById('aiperf-inputLengths')!, { target: { value: '2048' } })
    await click(previewButton())
    await click(startButton())
    expect(previewPerfPlan).toHaveBeenCalledTimes(1)
    expect(submitPerfPlan).toHaveBeenCalledTimes(1)
  })

  it('requires an explicit new attempt before an unconfirmed plan can be replaced', async () => {
    vi.mocked(previewPerfPlan)
      .mockResolvedValueOnce(planPreview({ plan_hash: 'hash-1' }))
      .mockResolvedValueOnce(planPreview({ plan_hash: 'hash-2' }))
    vi.mocked(submitPerfPlan)
      .mockRejectedValueOnce(new Error('connection reset'))
      .mockResolvedValueOnce({ task_id: 'perf_task_3', state: 'queued', duplicate: false } as never)
    const { onAttemptTask } = await renderReady(readyDraft())
    await click(previewButton())
    await click(startButton())
    expect(onAttemptTask).toHaveBeenCalledTimes(1)
    const originalId = vi.mocked(submitPerfPlan).mock.calls[0][1]

    fireEvent.change(document.getElementById('aiperf-inputLengths')!, { target: { value: '2048' } })
    await click(previewButton())
    await click(startButton())
    expect(submitPerfPlan).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/still unconfirmed/)).toBeInTheDocument()

    await click(screen.getByRole('button', { name: 'New attempt' }))
    await click(startButton())
    expect(submitPerfPlan).toHaveBeenCalledTimes(2)
    expect(vi.mocked(submitPerfPlan).mock.calls[1][1]).not.toBe(originalId)
  })

  it('blocks an AIPerf-unsupported connection before any invoke', async () => {
    const onValidateConnection = vi.fn(() => ({ 'connection-api': 'tasks.aiperf.openaiRequired' }))
    await renderReady(readyDraft(), {
      connection: { ...connection, api: 'local' },
      onValidateConnection,
    })
    await click(previewButton())
    expect(previewPerfPlan).not.toHaveBeenCalled()
    expect(onValidateConnection).toHaveBeenCalled()
    await click(startButton())
    expect(submitPerfPlan).not.toHaveBeenCalled()
  })

  it('keeps the active load input outside advanced settings and only the mode inside', async () => {
    await renderReady(readyDraft())
    const advanced = screen.getByText('Advanced settings').closest('details')
    const closed = document.getElementById('aiperf-closedLoads')!
    expect(closed).toBeInTheDocument()
    expect(closed.closest('details')).toBeNull()

    const mode = document.getElementById('aiperf-mode')!
    expect(mode.closest('details')).toBe(advanced)
    fireEvent.change(mode, { target: { value: 'open_loop' } })
    const open = document.getElementById('aiperf-openRates')!
    expect(open.closest('details')).toBeNull()
  })

  it('keeps an accepted matrix locked after the draft is edited', async () => {
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview())
    vi.mocked(submitPerfPlan).mockResolvedValue({ task_id: 'perf_task_4', state: 'queued', duplicate: false } as never)
    await renderReady(readyDraft())
    await click(previewButton())
    await click(startButton())
    expect(startButton()).toBeDisabled()
    expect(screen.getAllByText(/The submission was accepted/).length).toBeGreaterThan(0)

    fireEvent.change(document.getElementById('aiperf-inputLengths')!, { target: { value: '2048' } })
    expect(startButton()).toBeDisabled()
    expect(screen.getAllByText(/The submission was accepted/).length).toBeGreaterThan(0)
  })
})

describe('real prompts', () => {
  const text = 'first request\n\n second request '

  function promptsDraft(): AIPerfTaskDraft {
    return readyDraft({
      inputSource: 'prompts',
      prompts: { text, outputLimit: '128', mode: 'closed_loop', concurrency: '1', rate: '' },
    })
  }

  it('previews through the single-run contract and submits the verbatim payload', async () => {
    vi.mocked(validatePerfSingle).mockResolvedValue(singlePreview())
    const { onSubmit } = await renderReady(promptsDraft())
    await click(previewButton())

    expect(previewPerfPlan).not.toHaveBeenCalled()
    const [payload, taskId] = vi.mocked(validatePerfSingle).mock.calls[0]
    expect(taskId).toMatch(/^perf_single_preview_/)
    const aiperf = (payload as { aiperf: Record<string, unknown> }).aiperf
    expect(aiperf.input_prompts).toEqual(['first request', ' second request '])
    expect(aiperf.cache_experiment).toBeUndefined()
    expect(aiperf.load).toEqual({
      mode: 'closed_loop', points: [{ concurrency: 1 }], request_count: 2, output_token_limit: 128, repetitions: 1,
    })
    expect(screen.getByText(/1 tier, 2 formal requests/)).toBeInTheDocument()

    await click(startButton())
    expect(onSubmit).toHaveBeenCalledWith(payload)
  })

  it('drops a preview as soon as an effective value is edited', async () => {
    vi.mocked(validatePerfSingle).mockResolvedValue(singlePreview())
    await renderReady(promptsDraft())
    await click(previewButton())
    expect(screen.getByText(/1 tier, 2 formal requests/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/aiperf\.input_prompts/), { target: { value: 'one' } })
    expect(screen.queryByText(/1 tier, 2 formal requests/)).toBeNull()
    expect(startButton()).toBeDisabled()
  })

  it('re-enables preview when an edit cancels the in-flight request', async () => {
    const first = deferred<PerfPlanPreviewResponse>()
    vi.mocked(previewPerfPlan).mockReturnValue(first.promise)
    await renderReady(readyDraft())
    await click(previewButton())
    expect(previewButton()).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/workload\.input_lengths/), { target: { value: '2048' } })
    expect(previewButton()).toBeEnabled()
    await act(async () => {
      first.resolve(planPreview())
    })
    expect(previewButton()).toBeEnabled()
  })

  it('ignores a late preview from an older edit', async () => {    const first = deferred<PerfPlanPreviewResponse>()
    const second = deferred<PerfPlanPreviewResponse>()
    vi.mocked(previewPerfPlan).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    await renderReady(readyDraft())
    await click(previewButton())
    fireEvent.change(screen.getByLabelText(/workload\.input_lengths/), { target: { value: '2048' } })
    await click(previewButton())

    await act(async () => {
      second.resolve(planPreview({ plan_hash: '222222222222', total_runs: 7 }))
    })
    expect(screen.getByText('222222222222')).toBeInTheDocument()
    await act(async () => {
      first.resolve(planPreview({ plan_hash: '111111111111', total_runs: 3 }))
    })
    expect(screen.queryByText('111111111111')).toBeNull()
    expect(screen.getByText('222222222222')).toBeInTheDocument()
  })

  it('queries the original prompt task instead of invoking again after an unknown outcome', async () => {
    vi.mocked(validatePerfSingle).mockResolvedValue(singlePreview())
    const { onSubmit, onAttemptTask } = await renderReady(promptsDraft(), {
      onSubmit: async () => ({ outcome: 'unknown', taskId: 'perf_prompt_1' }),
    })
    await click(previewButton())
    await click(startButton())
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onAttemptTask).toHaveBeenLastCalledWith('perf_prompt_1')

    await click(startButton())
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onAttemptTask).toHaveBeenLastCalledWith('perf_prompt_1')
    expect(screen.getByText(/Checking the original task ID/)).toBeInTheDocument()
  })

  it('keeps one invoke through the shell and queries the original task after an unknown result', async () => {
    vi.mocked(validatePerfSingle).mockResolvedValue(singlePreview())
    const submitTask = vi.fn().mockRejectedValue(new Error('connection reset after request accepted'))
    const onSelect = vi.fn()
    function ShellHarness() {
      const [draft, setDraft] = useState(promptsDraft())
      return (
        <LocaleProvider>
          <TaskRunnerShell idPrefix="perf" title="Performance" configTitle="Config" statusTitle="Status"
            readyLabel="Ready" selectedTaskId={null} onSelect={onSelect} submitTask={submitTask}
            stopTask={async () => {}} getLog={async () => ({ text: '', head_line: 0, tail_line: 0, total_lines: 0 })}
            renderForm={({ onSubmit, disabled }) => (
              <AIPerfTaskForm connection={connection} draft={draft} onChange={setDraft} onSubmit={onSubmit}
                disabled={disabled} onValidateConnection={() => ({})} />
            )}
          />
        </LocaleProvider>
      )
    }
    render(<ShellHarness />)
    await flush()
    await click(previewButton())
    await click(startButton())
    expect(submitTask).toHaveBeenCalledTimes(1)
    const taskId = submitTask.mock.calls[0][1]

    await click(startButton())
    expect(submitTask).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenLastCalledWith(taskId)
    expect(screen.getByText(/Checking the original task ID/)).toBeInTheDocument()
  })

  it('clears the empty prompt error as soon as a prompt is entered', async () => {
    await renderReady(readyDraft({ inputSource: 'prompts' }))
    await click(previewButton())
    const text = document.getElementById('aiperf-prompts')!
    expect(text).toHaveAttribute('aria-invalid', 'true')

    fireEvent.change(text, { target: { value: 'fixed' } })
    expect(text).toHaveAttribute('aria-invalid', 'false')
  })

  it('clears a concurrency error once enough prompts are added', async () => {
    await renderReady(readyDraft({
      inputSource: 'prompts',
      prompts: { text: 'only', outputLimit: '128', mode: 'closed_loop', concurrency: '2', rate: '' },
    }))
    await click(previewButton())
    const concurrency = document.getElementById('aiperf-promptConcurrency')!
    expect(concurrency).toHaveAttribute('aria-invalid', 'true')

    fireEvent.change(document.getElementById('aiperf-prompts')!, { target: { value: 'one\ntwo' } })
    expect(concurrency).toHaveAttribute('aria-invalid', 'false')
  })

  it('keeps shared errors and drops branch-only errors across a source switch', async () => {
    await renderReady(readyDraft({ tokenizer: '', generated: { ...readyDraft().generated, maxModelLen: '' } }))
    await click(previewButton())
    expect(document.getElementById('aiperf-tokenizer')).toHaveAttribute('aria-invalid', 'true')
    expect(document.getElementById('aiperf-maxModelLen')).toHaveAttribute('aria-invalid', 'true')

    const source = screen.getByRole('combobox', { name: /Input source/ })
    fireEvent.change(source, { target: { value: 'prompts' } })
    expect(document.getElementById('aiperf-tokenizer')).toHaveAttribute('aria-invalid', 'true')

    fireEvent.change(source, { target: { value: 'generated' } })
    expect(document.getElementById('aiperf-maxModelLen')).toHaveAttribute('aria-invalid', 'false')
  })
})

describe('metrics monitoring', () => {
  it('hides nodes and disables the probe until monitoring is enabled', async () => {
    await renderReady(readyDraft())
    expect(screen.getByText(/Node metrics stay off/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/targets\[\]\.node_id/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Check metrics endpoints' })).toBeDisabled()
  })

  it('blocks preview and start when an enabled monitoring interval is invalid', async () => {
    await renderReady(readyDraft({ monitoring: monitoring({ sampleInterval: '0' }) }))
    await click(previewButton())
    expect(previewPerfPlan).not.toHaveBeenCalled()
    expect(document.getElementById('aiperf-sampleInterval')).toHaveAttribute('aria-invalid', 'true')
    await click(startButton())
    expect(submitPerfPlan).not.toHaveBeenCalled()
  })

  it('probes explicitly, marks the result stale after an edit, and clears a token on origin change', async () => {
    vi.mocked(probePerfTelemetry).mockResolvedValue({
      nodes: [{ node_id: 'prefill', status: 'available', metric_names: ['up'] }],
    } as never)
    await renderReady(readyDraft({ monitoring: monitoring() }))
    const token = document.getElementById('aiperf-node-n1-bearerToken') as HTMLInputElement
    fireEvent.change(token, { target: { value: 'node-secret' } })

    await click(screen.getByRole('button', { name: 'Check metrics endpoints' }))
    const [telemetry] = vi.mocked(probePerfTelemetry).mock.calls[0]
    expect(telemetry).toMatchObject({
      required: false,
      sample_interval_seconds: 1,
      targets: [{
        node_id: 'prefill', url: 'http://10.0.0.1:9000/metrics', headers: { Authorization: 'Bearer node-secret' },
      }],
    })
    expect(screen.getByText(/prefill: available \(up\)/)).toBeInTheDocument()

    fireEvent.change(document.getElementById('aiperf-node-n1-url')!, { target: { value: 'http://other:9000/metrics' } })
    expect(screen.getByText(/Node settings changed since the last check/)).toBeInTheDocument()
    expect(document.getElementById('aiperf-node-n1-bearerToken')).toHaveValue('')
  })

  it('blocks the start when required metrics are unavailable', async () => {
    vi.mocked(validatePerfSingle).mockResolvedValue(singlePreview())
    vi.mocked(probePerfTelemetry).mockResolvedValue({
      nodes: [{ node_id: 'prefill', status: 'unavailable', metric_names: [] }],
    } as never)
    await renderReady(readyDraft({
      inputSource: 'prompts',
      prompts: { text: 'hello', outputLimit: '128', mode: 'closed_loop', concurrency: '1', rate: '' },
      monitoring: monitoring({ required: true }),
    }))
    await click(previewButton())
    expect(startButton()).toBeEnabled()

    await click(screen.getByRole('button', { name: 'Check metrics endpoints' }))
    expect(screen.getByText(/Required node metrics are unavailable/)).toBeInTheDocument()
    expect(startButton()).toBeDisabled()
  })

  it('cancels a pending probe when monitoring is switched off', async () => {
    const pending = deferred<never>()
    vi.mocked(probePerfTelemetry).mockReturnValue(pending.promise)
    await renderReady(readyDraft({ monitoring: monitoring() }))
    await click(screen.getByRole('button', { name: 'Check metrics endpoints' }))
    const signal = vi.mocked(probePerfTelemetry).mock.calls[0][1]
    expect(signal?.aborted).toBe(false)

    fireEvent.click(document.getElementById('aiperf-monitoringEnabled')!)
    expect(signal?.aborted).toBe(true)
  })

  it('does not reuse an expired check after monitoring is switched off and on', async () => {
    vi.mocked(probePerfTelemetry).mockResolvedValue({
      nodes: [{ node_id: 'prefill', status: 'available', metric_names: ['up'] }],
    } as never)
    await renderReady(readyDraft({ monitoring: monitoring() }))
    const probeButton = () => screen.getByRole('button', { name: 'Check metrics endpoints' })
    await click(probeButton())
    expect(screen.getByText(/prefill: available \(up\)/)).toBeInTheDocument()

    fireEvent.click(document.getElementById('aiperf-monitoringEnabled')!)
    fireEvent.click(document.getElementById('aiperf-monitoringEnabled')!)

    // Re-enabling with the same values still starts a new check: the old
    // result is stale, not current.
    expect(screen.queryByText(/prefill: available \(up\)/)).not.toBeInTheDocument()
    expect(screen.getByText(/Node settings changed since the last check/)).toBeInTheDocument()
    expect(probeButton()).toBeEnabled()
    await click(probeButton())
    expect(probePerfTelemetry).toHaveBeenCalledTimes(2)
    expect(screen.getByText(/prefill: available \(up\)/)).toBeInTheDocument()
  })

  it('cancels a pending probe when the form unmounts', async () => {
    const pending = deferred<never>()
    vi.mocked(probePerfTelemetry).mockReturnValue(pending.promise)
    const { view } = await renderReady(readyDraft({ monitoring: monitoring() }))
    await click(screen.getByRole('button', { name: 'Check metrics endpoints' }))
    const signal = vi.mocked(probePerfTelemetry).mock.calls[0][1]

    view.unmount()
    expect(signal?.aborted).toBe(true)
  })
})

describe('plan import', () => {
  it('reports an imported plan task through the same callback', async () => {
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview())
    vi.mocked(submitPerfPlan).mockResolvedValue({ task_id: 'imported_task', state: 'queued', duplicate: false } as never)
    const { onPlanSubmitted } = await renderReady(readyDraft())

    const file = new File([JSON.stringify({ kind: 'iss-eval.perf-plan', schema_version: '1.0' })],
      'plan.json', { type: 'application/json' })
    fireEvent.change(screen.getByLabelText('Choose JSON file'), { target: { files: [file] } })
    await flush()
    await click(screen.getByRole('button', { name: 'Preview imported plan' }))
    await click(screen.getByRole('button', { name: 'Submit plan' }))
    expect(onPlanSubmitted).toHaveBeenCalledWith('imported_task')
  })

  it('keeps the imported task id when the outcome is unknown and queries it', async () => {
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview())
    vi.mocked(submitPerfPlan)
      .mockRejectedValueOnce(new Error('connection reset'))
      .mockResolvedValueOnce({ task_id: 'imported_task_2', state: 'queued', duplicate: false } as never)
    const { onAttemptTask, onPlanSubmitted } = await renderReady(readyDraft())

    const file = new File([JSON.stringify({ kind: 'iss-eval.perf-plan', schema_version: '1.0' })],
      'plan.json', { type: 'application/json' })
    fireEvent.change(screen.getByLabelText('Choose JSON file'), { target: { files: [file] } })
    await flush()
    await click(screen.getByRole('button', { name: 'Preview imported plan' }))
    await click(screen.getByRole('button', { name: 'Submit plan' }))
    expect(onAttemptTask).toHaveBeenCalledTimes(1)
    const firstId = vi.mocked(submitPerfPlan).mock.calls[0][1]
    expect(screen.getByText(/did not confirm the submission/)).toBeInTheDocument()

    await click(screen.getByRole('button', { name: 'Submit plan' }))
    expect(vi.mocked(submitPerfPlan).mock.calls[1][1]).toBe(firstId)
    expect(onPlanSubmitted).toHaveBeenCalledWith('imported_task_2')
  })
})

describe('independent cross-source pending ownership review', () => {
  it('blocks prompts while a generated attempt is unknown', async () => {
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview())
    vi.mocked(validatePerfSingle).mockResolvedValue(singlePreview())
    vi.mocked(submitPerfPlan).mockRejectedValue(new Error('connection reset'))
    const { onSubmit } = await renderReady(readyDraft({
      prompts: { text: 'one', outputLimit: '128', mode: 'closed_loop', concurrency: '1', rate: '' },
    }))
    await click(previewButton())
    await click(startButton())
    fireEvent.change(screen.getByRole('combobox', { name: /Input source/ }), { target: { value: 'prompts' } })
    await click(previewButton())
    await click(startButton())
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it.each(['generated', 'prompts'] as const)('blocks plan import while a %s attempt is unknown', async (source) => {
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview())
    vi.mocked(validatePerfSingle).mockResolvedValue(singlePreview())
    vi.mocked(submitPerfPlan).mockRejectedValue(new Error('connection reset'))
    const draft = readyDraft({ inputSource: source,
      prompts: { text: 'one', outputLimit: '128', mode: 'closed_loop', concurrency: '1', rate: '' } })
    await renderReady(draft, { onSubmit: async () => ({ outcome: 'unknown', taskId: 'prompt-task' }) })
    await click(previewButton())
    await click(startButton())
    expect(screen.getByLabelText('Choose JSON file')).toBeDisabled()
    await click(screen.getByRole('button', { name: 'New attempt' }))
    expect(screen.getByLabelText('Choose JSON file')).toBeEnabled()
  })

  it('does not invoke a generated plan while a prompt attempt is unknown', async () => {
    vi.mocked(validatePerfSingle).mockResolvedValue(singlePreview())
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview())
    vi.mocked(submitPerfPlan).mockResolvedValue({ task_id: 'new_generated', state: 'queued', duplicate: false } as never)
    const { onSubmit, onAttemptTask } = await renderReady(readyDraft({
      inputSource: 'prompts',
      prompts: { text: 'first request', outputLimit: '128', mode: 'closed_loop', concurrency: '1', rate: '' },
    }), { onSubmit: async () => ({ outcome: 'unknown', taskId: 'original_prompt_task' }) })
    await click(previewButton())
    await click(startButton())
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onAttemptTask).toHaveBeenCalledWith('original_prompt_task')
    fireEvent.change(screen.getByRole('combobox', { name: /Input source/ }), { target: { value: 'generated' } })
    await click(previewButton())
    await click(startButton())
    expect(submitPerfPlan).not.toHaveBeenCalled()
  })

  it('does not invoke the form plan while an imported attempt is unknown', async () => {
    vi.mocked(previewPerfPlan).mockResolvedValue(planPreview())
    vi.mocked(validatePerfSingle).mockResolvedValue(singlePreview())
    vi.mocked(submitPerfPlan)
      .mockRejectedValueOnce(new Error('connection reset after import accepted'))
      .mockResolvedValueOnce({ task_id: 'new_form_task', state: 'queued', duplicate: false } as never)
    const { onAttemptTask, onSubmit } = await renderReady(readyDraft({
      prompts: { text: 'one', outputLimit: '128', mode: 'closed_loop', concurrency: '1', rate: '' },
    }))
    const file = new File([JSON.stringify({ kind: 'iss-eval.perf-plan', schema_version: '1.0' })],
      'plan.json', { type: 'application/json' })
    fireEvent.change(screen.getByLabelText('Choose JSON file'), { target: { files: [file] } })
    await flush()
    await click(screen.getByRole('button', { name: 'Preview imported plan' }))
    await click(screen.getByRole('button', { name: 'Submit plan' }))
    expect(submitPerfPlan).toHaveBeenCalledTimes(1)
    expect(onAttemptTask).toHaveBeenCalledTimes(1)
    await click(previewButton())
    await click(startButton())
    expect(submitPerfPlan).toHaveBeenCalledTimes(1)
    fireEvent.change(screen.getByRole('combobox', { name: /Input source/ }), { target: { value: 'prompts' } })
    await click(previewButton())
    await click(startButton())
    expect(onSubmit).not.toHaveBeenCalled()
  })
})

describe('dependent error visibility review', () => {
  it('keeps the prompt error visible when the edited value is still invalid', async () => {
    await renderReady(readyDraft({ inputSource: 'prompts' }))
    await click(previewButton())
    const field = document.getElementById('aiperf-prompts')!
    expect(field).toHaveAttribute('aria-invalid', 'true')
    fireEvent.change(field, { target: { value: '   ' } })
    await flush()
    expect(field).toHaveAttribute('aria-invalid', 'true')
  })
})
