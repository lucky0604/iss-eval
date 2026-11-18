import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import type { PerfPlanPreviewResponse, PerfSinglePlanResponse } from '@/api/generated/contracts'
import type { ModelConnection } from '@/components/tasks/taskConfig'
import { defaultAIPerfDraft, type AIPerfTaskDraft } from '@/components/tasks/aiperfDraft'
import TaskRunnerShell from '@/components/tasks/TaskRunnerShell'
import { submitPerfSingle } from '@/api/perf'
import AIPerfTaskForm from './AIPerfTaskForm'

// The monitor polls task state; this suite is about the submit path.
vi.mock('@/components/tasks/TaskRunMonitor', () => ({ default: () => null }))

const connection: ModelConnection = {
  model: 'served-model', url: 'http://localhost:1234/v1', apiKey: 'sk-model', alias: '', api: 'openai',
}

function readyDraft(patch: Partial<AIPerfTaskDraft> = {}): AIPerfTaskDraft {
  return {
    ...defaultAIPerfDraft(),
    tokenizer: 'tok',
    generated: { ...defaultAIPerfDraft().generated, maxModelLen: '32768' },
    ...patch,
  }
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

const singlePreview = {
  engine_available: true, profiling_requests: 2, warnings: [],
} as PerfSinglePlanResponse

interface RecordedCall {
  url: string
  method: string
  headers: Record<string, string>
  body: unknown
}

/** Minimal response surface used by the fetch client. */
function respond(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response)
}

function installFetch(routes: {
  preview?: () => Promise<Response>
  planInvoke?: () => Promise<Response>
  singleValidate?: () => Promise<Response>
  invoke?: () => Promise<Response>
}) {
  const calls: RecordedCall[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    calls.push({
      url,
      method: init?.method ?? 'GET',
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    })
    if (url.includes('/api/v1/perf/engines')) {
      return respond({ engines: [{ engine: 'aiperf', available: true, reason: null }] })
    }
    if (url.includes('/api/v1/perf/plans/preview')) return (routes.preview ?? (() => respond(planPreview())))()
    if (url.includes('/api/v1/perf/plans/invoke')) {
      return (routes.planInvoke ?? (() => respond({ task_id: 'perf_matrix_task', state: 'queued', duplicate: false })))()
    }
    if (url.includes('/api/v1/perf/validate-single')) return (routes.singleValidate ?? (() => respond(singlePreview)))()
    if (url.includes('/api/v1/perf/invoke')) {
      return (routes.invoke ?? (() => respond({ task_id: 'perf_prompt_task', state: 'queued' })))()
    }
    return respond({ error: `unexpected fetch ${url}` }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

function ShellHarness({ draft, onSubmitPlan, onSelectSpy }: {
  draft: AIPerfTaskDraft
  onSubmitPlan?: (taskId: string) => void
  onSelectSpy?: (taskId: string) => void
}) {
  const [selected, setSelected] = useState<string | null>(null)
  const [current, setCurrent] = useState(draft)
  const select = (taskId: string) => {
    setSelected(taskId)
    onSelectSpy?.(taskId)
  }
  return (
    <LocaleProvider>
      <TaskRunnerShell idPrefix="perf" title="Performance" configTitle="Config" statusTitle="Status"
        readyLabel="Ready" selectedTaskId={selected} onSelect={select} stopTask={async () => {}}
        getLog={async () => ({ text: '', head_line: 0, tail_line: 0, total_lines: 0 })}
        submitTask={(payload, id) => {
          const settings = payload.aiperf as Record<string, unknown>
          return Array.isArray(settings.input_prompts) ? submitPerfSingle(payload, id) : Promise.resolve({
            task_id: id, status: 'queued',
          } as never)
        }}
        renderForm={({ onSubmit, disabled }) => (
          <AIPerfTaskForm connection={connection} draft={current} onChange={setCurrent} onSubmit={onSubmit}
            disabled={disabled} onValidateConnection={() => ({})}
            onAttemptTask={select}
            onPlanSubmitted={(taskId) => {
              select(taskId)
              onSubmitPlan?.(taskId)
            }} />
        )}
      />
    </LocaleProvider>
  )
}

const previewButton = () => screen.getByRole('button', { name: 'Preview test' })
const startButton = () => screen.getByRole('button', { name: 'Start test' })

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

async function renderReady(draft: AIPerfTaskDraft, props: {
  onSubmitPlan?: (taskId: string) => void
  onSelectSpy?: (taskId: string) => void
} = {}) {
  render(<ShellHarness draft={draft} {...props} />)
  await flush()
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('AIPerf submission through the real fetch client', () => {
  it('submits a generated matrix with the previewed hash, acknowledgement and runtime binding', async () => {
    const { calls } = installFetch({
      preview: () => respond(planPreview({ requires_acknowledgement: ['context_window_exceeded'] })),
    })
    const onPlanSubmitted = vi.fn()
    await renderReady(readyDraft(), { onSubmitPlan: onPlanSubmitted })

    await click(previewButton())
    fireEvent.click(screen.getByRole('checkbox', { name: 'context_window_exceeded' }))
    await click(startButton())
    await flush()

    const preview = calls.find((call) => call.url.includes('/api/v1/perf/plans/preview'))!
    expect(preview.url).toBe('http://localhost:3000/api/v1/perf/plans/preview?offset=0&limit=100')
    expect(preview.method).toBe('POST')
    expect(preview.headers['Content-Type']).toBe('application/json')
    const planText = (preview.body as { content: string }).content
    expect(JSON.parse(planText)).toMatchObject({
      kind: 'iss-eval.perf-plan', schema_version: '1.1', workload: { prefix_pool_size: 1 },
    })

    const invoke = calls.find((call) => call.url.includes('/api/v1/perf/plans/invoke'))!
    expect(invoke.url).toBe('http://localhost:3000/api/v1/perf/plans/invoke?async=1')
    expect(invoke.method).toBe('POST')
    expect(invoke.headers['EvalScope-Task-Id']).toMatch(/^perf_matrix_/)
    expect(invoke.headers['EvalScope-Plan-Hash']).toBe('hash-1')
    expect(invoke.body).toEqual({
      content: planText,
      acknowledged: ['context_window_exceeded'],
      expected_plan_hash: 'hash-1',
      runtime: { api_key: 'sk-model' },
    })
    expect(onPlanSubmitted).toHaveBeenCalledWith('perf_matrix_task')
    expect(sessionStorage.getItem('evalscope-active-task-perf')).toBe('perf_matrix_task')
  })

  it('submits real prompts verbatim through the single-run envelope', async () => {
    const { calls } = installFetch({})
    const onSelect = vi.fn()
    await renderReady(readyDraft({
      inputSource: 'prompts',
      prompts: { text: ' first request \nsecond request', outputLimit: '128', mode: 'closed_loop', concurrency: '1', rate: '' },
    }), { onSelectSpy: onSelect })

    await click(previewButton())
    const validate = calls.find((call) => call.url.includes('/api/v1/perf/validate-single'))!
    expect(validate.headers['EvalScope-Task-Id']).toMatch(/^perf_single_preview_/)
    expect((validate.body as { aiperf: { input_prompts: string[] } }).aiperf.input_prompts)
      .toEqual([' first request ', 'second request'])

    await click(startButton())
    await flush()

    const invokes = calls.filter((call) => call.url.includes('/api/v1/perf/invoke'))
    expect(invokes).toHaveLength(1)
    expect(invokes[0].url).toBe('http://localhost:3000/api/v1/perf/invoke?async=1')
    expect(invokes[0].headers['EvalScope-Task-Id']).toMatch(/^perf_/)
    expect((invokes[0].body as { aiperf: { input_prompts: string[] } }).aiperf.input_prompts)
      .toEqual([' first request ', 'second request'])
    expect(onSelect).toHaveBeenLastCalledWith(invokes[0].headers['EvalScope-Task-Id'])
    expect(screen.getByText(/Prompts accepted/)).toBeInTheDocument()
  })

  it('keeps one invoke after a failed prompt submission and queries the original task', async () => {
    const { calls } = installFetch({ invoke: () => respond({ error: 'worker unavailable' }, 500) })
    const onSelect = vi.fn()
    await renderReady(readyDraft({
      inputSource: 'prompts',
      prompts: { text: 'only request', outputLimit: '128', mode: 'closed_loop', concurrency: '1', rate: '' },
    }), { onSelectSpy: onSelect })

    await click(previewButton())
    await click(startButton())
    await flush()

    const invokes = calls.filter((call) => call.url.includes('/api/v1/perf/invoke'))
    expect(invokes).toHaveLength(1)
    const taskId = invokes[0].headers['EvalScope-Task-Id']
    expect(screen.getByText(/did not confirm the prompt submission/)).toBeInTheDocument()
    expect(onSelect).toHaveBeenLastCalledWith(taskId)

    await click(startButton())
    await flush()
    expect(calls.filter((call) => call.url.includes('/api/v1/perf/invoke'))).toHaveLength(1)
    expect(onSelect).toHaveBeenLastCalledWith(taskId)
    expect(screen.getByText(/Checking the original task ID/)).toBeInTheDocument()
  })
})
