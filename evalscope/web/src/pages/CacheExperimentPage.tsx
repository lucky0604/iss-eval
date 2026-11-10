import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  getCacheExperiment,
  getCacheExperimentReportUrl,
  getCacheExperimentSummary,
  getCacheNodeMetrics,
  getCacheRequests,
  getCacheScenario,
} from '@/api/perf'
import type {
  CacheExperimentResponse,
  CacheExperimentSummaryResponse,
  CacheNodeMetricsResponse,
  CacheRequestsResponse,
  CacheScenarioResponse,
  CacheSubrunPlan,
} from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import { useScan } from '@/contexts/ReportsContext'
import { useQueryParams } from '@/hooks/useQueryParams'
import { formatMetric } from '@/domain/metric'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Card from '@/components/ui/Card'
import ErrorAlert from '@/components/ui/ErrorAlert'
import Eyebrow from '@/components/ui/Eyebrow'
import KpiStrip, { type KpiItem } from '@/components/ui/KpiStrip'
import Tabs from '@/components/ui/Tabs'
import { PerformanceQualityBanner, PerformanceResultHeader } from '@/components/perf/PerformanceResultShell'
import NodeWindowPanel from '@/components/perf/NodeWindowPanel'
import CacheComparisonPanel from '@/components/perf/CacheComparisonPanel'
import CacheMatrixChart from '@/components/perf/CacheMatrixChart'
import CrossExperimentComparisonPanel from '@/components/perf/CrossExperimentComparisonPanel'
import CacheRepetitionsPanel from '@/components/perf/CacheRepetitionsPanel'
import CacheRequestGroupsPanel from '@/components/perf/CacheRequestGroupsPanel'

const TH = 'type-table-xs border-b border-[var(--border)] px-3 py-2.5 text-right whitespace-nowrap first:text-left'
const TD = 'type-body-sm tabular-nums border-b border-[var(--border)] px-3 py-2.5 text-right whitespace-nowrap first:text-left text-[var(--text)]'
const ROW = 'transition-colors duration-150 hover:bg-[var(--bg-card2)]'
const ID_BUTTON = 'font-mono text-xs text-[var(--accent)] hover:underline disabled:cursor-not-allowed disabled:text-[var(--text-muted)] disabled:no-underline'
const TAB_KEYS = ['overview', 'matrix', 'comparison'] as const
type ExperimentTab = (typeof TAB_KEYS)[number]

function experimentTab(value?: string): ExperimentTab {
  return TAB_KEYS.includes(value as ExperimentTab) ? value as ExperimentTab : 'matrix'
}

function percentage(value: number | null | undefined): string {
  return value == null ? '—' : `${formatMetric(value * 100, null).primary}%`
}

function statusVariant(status: string | null | undefined): 'success' | 'warning' | 'danger' | 'default' {
  if (status === 'completed') return 'success'
  if (status === 'error') return 'danger'
  if (status === 'running' || status === 'partial') return 'warning'
  return 'default'
}

interface ReportProps {
  taskId: string
  rootPath: string
  tab: ExperimentTab
  subrun?: string
  baselineId?: string
  candidateId?: string
  setParam: (key: string, value?: string) => void
}

/** A fetched page tagged with the scope (subrun, offset, root, attempt) it answers. */
interface ScopedResult<T> {
  key: string
  value: T | null
  error: string
}

export default function CacheExperimentPage() {
  const { taskId = '' } = useParams()
  const { get, set } = useQueryParams()
  const { rootPath: ctxRoot } = useScan()
  const rootPath = get('root_path') ?? ctxRoot
  // A different outputs root is a different archive namespace: subrun and
  // comparison picks from the previous root must not silently carry over.
  const previousRoot = useRef(rootPath)
  useEffect(() => {
    if (previousRoot.current === rootPath) return
    previousRoot.current = rootPath
    for (const key of ['subrun', 'baseline', 'candidate']) set(key, undefined)
  }, [rootPath, set])
  return <CacheExperimentReport
    key={`${rootPath}\0${taskId}`}
    taskId={taskId}
    rootPath={rootPath}
    tab={experimentTab(get('tab'))}
    subrun={get('subrun')}
    baselineId={get('baseline')}
    candidateId={get('candidate')}
    setParam={set}
  />
}

function CacheExperimentReport({ taskId, rootPath, tab, subrun, baselineId, candidateId, setParam }: ReportProps) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [experiment, setExperiment] = useState<CacheExperimentResponse | null>(null)
  const [summary, setSummary] = useState<CacheExperimentSummaryResponse | null>(null)
  const [scenarios, setScenarios] = useState<Record<string, CacheScenarioResponse>>({})
  const [metricsSlice, setMetricsSlice] = useState<ScopedResult<CacheNodeMetricsResponse>>({ key: '', value: null, error: '' })
  const [requestsSlice, setRequestsSlice] = useState<ScopedResult<CacheRequestsResponse>>({ key: '', value: null, error: '' })
  const [offset, setOffset] = useState(0)
  const [metricOffset, setMetricOffset] = useState(0)
  const [matrixOffset, setMatrixOffset] = useState(0)
  const [error, setError] = useState('')
  const [summaryError, setSummaryError] = useState('')
  const [summaryAttempt, setSummaryAttempt] = useState(0)
  const [detailError, setDetailError] = useState('')
  const [detailAttempt, setDetailAttempt] = useState(0)
  const [requestsAttempt, setRequestsAttempt] = useState(0)
  const [metricsAttempt, setMetricsAttempt] = useState(0)
  const selected = subrun ?? ''
  // Each fetch result carries the scope it answered. Reading through the key
  // means a subrun/offset switch shows a loading state (or the matching cached
  // page) instead of the previous subrun's rows while the next request is in
  // flight, and a late response cannot land under a newer scope.
  const requestsKey = `${taskId}\u0000${selected}\u0000${offset}\u0000${rootPath}\u0000${requestsAttempt}`
  const metricsKey = `${taskId}\u0000${selected}\u0000${metricOffset}\u0000${rootPath}\u0000${metricsAttempt}`
  const requests = requestsSlice.key === requestsKey ? requestsSlice.value : null
  const requestsError = requestsSlice.key === requestsKey ? requestsSlice.error : ''
  const metrics = metricsSlice.key === metricsKey ? metricsSlice.value : null
  const metricsError = metricsSlice.key === metricsKey ? metricsSlice.error : ''

  useEffect(() => {
    let active = true
    getCacheExperiment(taskId, undefined, rootPath).then((value) => {
      if (active) { setExperiment(value); setError('') }
    }).catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : String(err)) })
    return () => { active = false }
  }, [taskId, rootPath])

  useEffect(() => {
    let active = true
    getCacheExperimentSummary(taskId, undefined, rootPath).then((value) => {
      if (active) { setSummary(value); setSummaryError('') }
    }).catch((err: unknown) => { if (active) setSummaryError(err instanceof Error ? err.message : String(err)) })
    return () => { active = false }
  }, [taskId, rootPath, summaryAttempt])

  // Refresh only while the experiment is non-terminal and the page is visible;
  // a finished experiment stops polling, bounded by one experiment + one
  // summary request per tick rather than one request per planned subrun.
  const status = experiment?.state.status
  useEffect(() => {
    if (status !== 'running' && status !== 'unknown') return
    let active = true
    const controller = new AbortController()
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      getCacheExperiment(taskId, controller.signal, rootPath).then((value) => { if (active) setExperiment(value) }).catch(() => {})
      getCacheExperimentSummary(taskId, controller.signal, rootPath).then((value) => { if (active) setSummary(value) }).catch(() => {})
    }, 5000)
    return () => { active = false; controller.abort(); window.clearInterval(timer) }
  }, [status, taskId, rootPath])

  useEffect(() => {
    if (!experiment) return
    const controller = new AbortController()
    const rows = experiment.state.subruns.slice(matrixOffset, matrixOffset + 50).filter((row) => row.scenario)
    Promise.allSettled(rows.map(async (row) => [row.subrun_id,
      await getCacheScenario(taskId, row.subrun_id, controller.signal, rootPath)] as const)).then((values) => {
      if (controller.signal.aborted) return
      setScenarios((previous) => ({ ...previous, ...Object.fromEntries(
        values.filter((value) => value.status === 'fulfilled').map((value) => value.value)) }))
    })
    return () => controller.abort()
  }, [taskId, experiment, matrixOffset, rootPath])

  const wantedIds = useMemo(() => Array.from(new Set(
    [subrun, baselineId, candidateId].filter((id): id is string => Boolean(id)))), [subrun, baselineId, candidateId])
  useEffect(() => {
    if (!experiment) return
    const wanted = wantedIds.filter((id) => !scenarios[id]
      && experiment.plan.subruns.some((row) => row.subrun_id === id)
      && experiment.state.subruns.some((row) => row.subrun_id === id && row.scenario))
    if (!wanted.length) return
    const controller = new AbortController()
    Promise.allSettled(wanted.map(async (id) => [id,
      await getCacheScenario(taskId, id, controller.signal, rootPath)] as const)).then((values) => {
      if (controller.signal.aborted) return
      const merged: Record<string, CacheScenarioResponse> = {}
      values.forEach((value, index) => {
        if (value.status === 'fulfilled') merged[value.value[0]] = value.value[1]
        else if (wanted[index] === subrun) setDetailError(value.reason instanceof Error ? value.reason.message : String(value.reason))
      })
      if (Object.keys(merged).length) setScenarios((previous) => ({ ...previous, ...merged }))
    })
    return () => controller.abort()
  }, [experiment, taskId, rootPath, scenarios, subrun, wantedIds, detailAttempt])

  // URL-restored identifiers are revalidated against the saved plan, so an
  // outdated shared link never opens an unrelated or missing subrun.
  useEffect(() => {
    if (!experiment) return
    const known = new Set(experiment.plan.subruns.map((row) => row.subrun_id))
    for (const [key, value] of [['subrun', subrun], ['baseline', baselineId], ['candidate', candidateId]] as const) {
      if (value && !known.has(value)) setParam(key, undefined)
    }
  }, [experiment, subrun, baselineId, candidateId, setParam])

  useEffect(() => {
    if (!selected) return
    let active = true
    const controller = new AbortController()
    getCacheRequests(taskId, selected, offset, rootPath, controller.signal).then((value) => {
      if (active) setRequestsSlice({ key: requestsKey, value, error: '' })
    }).catch((err: unknown) => {
      if (active) setRequestsSlice({ key: requestsKey, value: null, error: err instanceof Error ? err.message : String(err) })
    })
    return () => { active = false; controller.abort() }
  }, [taskId, selected, offset, rootPath, requestsKey])

  useEffect(() => {
    if (!selected) return
    let active = true
    const controller = new AbortController()
    getCacheNodeMetrics(taskId, selected, metricOffset, rootPath, controller.signal).then((value) => {
      if (active) setMetricsSlice({ key: metricsKey, value, error: '' })
    }).catch((err: unknown) => {
      if (active) setMetricsSlice({ key: metricsKey, value: null, error: err instanceof Error ? err.message : String(err) })
    })
    return () => { active = false; controller.abort() }
  }, [taskId, selected, metricOffset, rootPath, metricsKey])

  const select = (id: string) => {
    setOffset(0)
    setMetricOffset(0)
    setDetailError('')
    setParam('subrun', id)
  }

  const pick = (id?: string): { plan: CacheSubrunPlan; scenario: CacheScenarioResponse } | undefined => {
    if (!experiment || !id) return undefined
    const plan = experiment.plan.subruns.find((row) => row.subrun_id === id)
    const scenario = scenarios[id]
    return plan && scenario?.subrun_id === id ? { plan, scenario } : undefined
  }
  const selectedPlan = experiment?.plan.subruns.find((row) => row.subrun_id === selected)
  const selectedScenario = selected ? scenarios[selected] ?? null : null
  const baseline = pick(baselineId)
  const candidate = pick(candidateId)

  const plannedRequests = summary ? summary.groups.reduce((sum, group) => sum + (group.planned_requests ?? 0), 0) : 0
  const observedRequests = summary ? summary.groups.reduce((sum, group) => sum + (group.observed_requests ?? 0), 0) : 0
  const successfulRequests = summary ? summary.groups.reduce((sum, group) => sum + (group.successful_requests ?? 0), 0) : 0
  const kpis: KpiItem[] = summary ? [
    {
      label: zh ? '已完成子运行' : 'Completed subruns',
      value: `${summary.coverage.completed ?? 0}/${summary.coverage.planned ?? 0}`,
    },
    {
      label: zh ? '可读子运行' : 'Readable subruns',
      value: `${summary.coverage.readable ?? 0}/${summary.coverage.planned ?? 0}`,
    },
    {
      label: zh ? '计划请求（预算）' : 'Planned requests (budget)',
      value: String(plannedRequests),
    },
    {
      label: zh ? '已观测请求' : 'Observed requests',
      value: String(observedRequests),
    },
    {
      label: zh ? '成功请求（可读子运行）' : 'Successful requests (readable runs)',
      value: String(successfulRequests),
    },
  ] : experiment ? [{
    label: zh ? '已完成子运行' : 'Completed subruns',
    value: `${experiment.state.completed_subruns}/${experiment.state.total_subruns}`,
  }] : []

  // Plan-wide counts sit above the tabs so paging the matrix never changes
  // them, and every tab keeps the same trustworthy headline.
  const summaryPanel = <div className="flex flex-col gap-3">
    {summaryError && <ErrorAlert role="alert">
      <div className="flex items-center justify-between gap-3">
        <span>{summaryError}</span>
        <Button variant="outline" size="sm" onClick={() => setSummaryAttempt((value) => value + 1)}>{zh ? '重试' : 'Retry'}</Button>
      </div>
    </ErrorAlert>}
    <KpiStrip items={kpis} layout="dense" />
    {summary && <Card
      title={zh ? '全实验覆盖' : 'Plan-wide coverage'}
      badge={<Badge variant={summary.completeness === 'partial' ? 'warning' : 'success'}>{summary.completeness}</Badge>}
    >
      <div className="space-y-3">
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(summary.counts_by_status ?? {}).map(([key, count]) =>
            <Badge key={key} variant={statusVariant(key)}>{key} {count}</Badge>)}
          {Object.entries(summary.counts_by_quality ?? {}).map(([key, count]) =>
            <Badge key={`quality-${key}`} variant={key === 'complete' ? 'success' : key === 'invalid' ? 'danger' : 'warning'}>
              {zh ? '质量' : 'quality'}: {key} {count}
            </Badge>)}
        </div>
        <p className="type-body-xs text-[var(--text-muted)]">{zh
          ? `可读 ${summary.coverage.readable ?? 0} 组，纳入请求统计 ${summary.coverage.included ?? 0} 组；未开始 ${summary.missing_subruns ?? 0} 组，不可读 ${summary.unreadable_subruns ?? 0} 组。`
          : `readable ${summary.coverage.readable ?? 0}, included in request totals ${summary.coverage.included ?? 0}; missing ${summary.missing_subruns ?? 0}, unreadable ${summary.unreadable_subruns ?? 0}.`}</p>
        {summary.completeness === 'partial' && <p role="status" className="type-body-xs text-[var(--warning)]">{zh
          ? `摘要范围不完整（${summary.completeness_reason ?? 'unknown'}）：请求总数只汇总可读子运行，不代表全实验完整结果。`
          : `The summary scope is partial (${summary.completeness_reason ?? 'unknown'}): request totals cover readable subruns only and are not a complete experiment result.`}</p>}
      </div>
    </Card>}
  </div>

  const overviewPanel = <div className="flex flex-col gap-4">
    <p className="type-body-xs text-[var(--text-muted)]">{zh
      ? '目标比例、生成比例与服务端报告的缓存命中彼此独立；缺失值显示为 —。全实验只汇总可信计数和请求数，TTFT、吞吐与分位保留在各组/各轮次，不跨负载或目标比例取平均。'
      : 'Target, generated reuse, and server-reported cache hits are distinct; missing values are shown as —. Plan-wide totals cover only trusted counts and request counts; TTFT, throughput and percentiles stay per group and repetition and are never averaged across loads or target ratios.'}</p>
    {experiment ? <Card title={zh ? '实验身份与预算' : 'Experiment identity and budget'} collapsible>
      <dl className="grid gap-x-6 gap-y-3 md:grid-cols-2">
        <div className="min-w-0"><dt className="type-label-xs">{zh ? '计划哈希' : 'Plan hash'}</dt>
          <dd className="mt-1 break-all font-mono text-xs text-[var(--text)]">{experiment.plan.plan_hash}</dd></div>
        <div className="min-w-0"><dt className="type-label-xs">{zh ? '配置哈希' : 'Configuration hash'}</dt>
          <dd className="mt-1 break-all font-mono text-xs text-[var(--text)]">{experiment.plan.configuration_hash}</dd></div>
        <div className="min-w-0"><dt className="type-label-xs">Tokenizer</dt>
          <dd className="mt-1 break-all font-mono text-xs text-[var(--text)]">{experiment.plan.tokenizer_id}</dd></div>
        <div className="min-w-0"><dt className="type-label-xs">Template</dt>
          <dd className="mt-1 break-all font-mono text-xs text-[var(--text)]">{experiment.plan.template_id}</dd></div>
        <div className="min-w-0"><dt className="type-label-xs">{zh ? '正式 / 预热请求预算' : 'Profiling / primer request budget'}</dt>
          <dd className="mt-1 tabular-nums text-sm text-[var(--text)]">{experiment.plan.profiling_requests} / {experiment.plan.primer_requests}</dd></div>
        {experiment.plan.configuration && <div className="min-w-0 md:col-span-2">
          <dt className="type-label-xs">{zh ? '已核验配置（不含凭证）' : 'Verified configuration (credentials excluded)'}</dt>
          <dd className="mt-1 break-all font-mono text-xs text-[var(--text-muted)]">{JSON.stringify(experiment.plan.configuration)}</dd>
        </div>}
      </dl>
    </Card> : null}
  </div>

  const matrixPanel = experiment ? <div className="flex flex-col gap-4">
    <Card title={zh ? '子运行矩阵' : 'Subrun matrix'} badge={
      <span className="type-body-xs text-[var(--text-muted)]">{experiment.plan.subruns.length} {zh ? '组' : 'runs'} · {zh ? '当前页' : 'page'} {matrixOffset + 1}–{Math.min(matrixOffset + 50, experiment.plan.subruns.length)}</span>
    }>
      <div className="overflow-x-auto">
        <table className="w-full min-w-max border-collapse">
          <thead><tr>
            <th className={TH}>{zh ? '子任务' : 'Run'}</th><th className={TH}>{zh ? '负载' : 'Load'}</th>
            <th className={TH}>{zh ? '重复' : 'Repeat'}</th><th className={TH}>{zh ? '目标' : 'Target'}</th>
            <th className={TH}>{zh ? '实际生成' : 'Generated'}</th><th className={TH}>{zh ? '服务端命中' : 'Server cache'}</th>
            <th className={TH}>{zh ? '覆盖请求' : 'Observed requests'}</th><th className={TH}>{zh ? '平均 TTFT（秒）' : 'Mean TTFT (s)'}</th>
            <th className={TH}>{zh ? '请求/秒' : 'Requests/s'}</th><th className={TH}>{zh ? '成功' : 'Success'}</th>
            <th className={TH}>{zh ? '状态' : 'Status'}</th><th className={TH}>{zh ? '对比' : 'Compare'}</th>
          </tr></thead>
          <tbody>{experiment.plan.subruns.slice(matrixOffset, matrixOffset + 50).map((row) => {
            const scenario = scenarios[row.subrun_id]
            const state = experiment.state.subruns.find((item) => item.subrun_id === row.subrun_id)
            return <tr key={row.subrun_id} className={ROW}>
              <td className={TD}>
                <button className={ID_BUTTON} aria-pressed={selected === row.subrun_id} disabled={!scenario}
                  onClick={() => scenario && select(row.subrun_id)}>
                  {row.subrun_id}
                </button>
              </td>
              <td className={TD}>{row.load_mode === 'closed_loop' ? row.load_point.concurrency : row.load_point.rate} ({row.load_mode})</td>
              <td className={TD}>{row.repetition + 1}</td>
              <td className={TD}>{percentage(row.target_ratio)}</td><td className={TD}>{percentage(scenario?.generated_ratio)}</td>
              <td className={TD}>{percentage(scenario?.cache?.ratio)}</td>
              <td className={TD}>{scenario?.cache ? `${scenario.cache.observed_requests}/${scenario.cache.successful_requests ?? '—'}` : '—'}</td>
              <td className={TD}>{formatMetric(scenario?.result?.metrics?.find((metric) => metric.key === 'time_to_first_token')?.value, null).primary}</td>
              <td className={TD}>{formatMetric(scenario?.result?.metrics?.find((metric) => metric.key === 'request_throughput')?.value, null).primary}</td>
              <td className={TD}>{scenario?.result ? `${scenario.result.profiling_successes}/${scenario.result.profiling_requests}` : '—'}</td>
              <td className={TD}><Badge variant={statusVariant(state?.status)}>{state?.status ?? '—'}</Badge></td>
              <td className={TD}>
                <div className="flex justify-end gap-1">
                  <Button variant="ghost" size="sm" disabled={!scenario} aria-pressed={baselineId === row.subrun_id}
                    aria-label={`${zh ? '基准' : 'Baseline'} ${row.subrun_id}`}
                    onClick={() => scenario && setParam('baseline', row.subrun_id)}>{zh ? '基准' : 'Baseline'}</Button>
                  <Button variant="ghost" size="sm" disabled={!scenario} aria-pressed={candidateId === row.subrun_id}
                    aria-label={`${zh ? '候选' : 'Candidate'} ${row.subrun_id}`}
                    onClick={() => scenario && setParam('candidate', row.subrun_id)}>{zh ? '候选' : 'Candidate'}</Button>
                </div>
              </td>
            </tr>
          })}</tbody>
        </table>
      </div>
      {experiment.plan.subruns.length > 50 && <div className="mt-3 flex items-center justify-between gap-2 text-sm">
        <span className="type-body-xs text-[var(--text-muted)]">{matrixOffset + 1}–{Math.min(matrixOffset + 50, experiment.plan.subruns.length)} / {experiment.plan.subruns.length}</span>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={matrixOffset === 0} onClick={() => setMatrixOffset(Math.max(0, matrixOffset - 50))}>{zh ? '上一页' : 'Previous'}</Button>
          <Button variant="outline" size="sm" disabled={matrixOffset + 50 >= experiment.plan.subruns.length} onClick={() => setMatrixOffset(matrixOffset + 50)}>{zh ? '下一页' : 'Next'}</Button>
        </div>
      </div>}
    </Card>
    <CacheMatrixChart rows={experiment.plan.subruns.slice(matrixOffset, matrixOffset + 50)} scenarios={scenarios} />
    {selected && <>
      {detailError && <ErrorAlert role="alert">
        <div className="flex items-center justify-between gap-3">
          <span>{detailError}</span>
          <Button variant="outline" size="sm" onClick={() => {
            setScenarios((previous) => {
              const next = { ...previous }
              delete next[selected]
              return next
            })
            setDetailError('')
            setDetailAttempt((value) => value + 1)
          }}>{zh ? '重试' : 'Retry'}</Button>
        </div>
      </ErrorAlert>}
      {!selectedScenario && !detailError && <p role="status" className="type-body-xs text-[var(--text-muted)]">{zh ? '正在加载子运行…' : 'Loading subrun…'}</p>}
      {selectedScenario && <Card
        title={`${zh ? '子运行详情' : 'Subrun detail'} · ${selectedPlan
          ? `${percentage(selectedPlan.target_ratio)} · ${selectedPlan.load_mode === 'closed_loop' ? selectedPlan.load_point.concurrency : selectedPlan.load_point.rate} · #${selectedPlan.repetition + 1}`
          : selected}`}
        badge={<div className="flex items-center gap-2">
          <Badge variant={statusVariant(selectedScenario.status)}>{selectedScenario.status}</Badge>
          <Button variant="ghost" size="sm" onClick={() => setParam('subrun', undefined)}>{zh ? '关闭' : 'Close'}</Button>
        </div>}
      >
        <div className="space-y-5">
          <section className="space-y-3">
            <Eyebrow>{zh ? '输入和执行配置' : 'Input and execution details'}</Eyebrow>
            {experiment.plan.subruns.filter((row) => row.subrun_id === selected).map((row) => <dl key={row.subrun_id} className="grid gap-x-6 gap-y-3 md:grid-cols-2">
              <div><dt className="type-label-xs">{zh ? '执行顺序' : 'Execution order'}</dt><dd className="mt-1 tabular-nums text-sm">{row.order + 1}</dd></div>
              <div><dt className="type-label-xs">{zh ? '随机种子' : 'Seed'}</dt><dd className="mt-1 tabular-nums text-sm">{row.seed}</dd></div>
              <div><dt className="type-label-xs">{zh ? '计划请求' : 'Planned requests'}</dt><dd className="mt-1 tabular-nums text-sm">{row.request_count}</dd></div>
              <div><dt className="type-label-xs">{zh ? '输出上限' : 'Output limit'}</dt><dd className="mt-1 tabular-nums text-sm">{row.output_token_limit ?? '—'}</dd></div>
              <div><dt className="type-label-xs">{zh ? '生成的每请求输入 token 数' : 'Generated input tokens per request'}</dt>
                <dd className="mt-1 tabular-nums text-sm">{selectedScenario.input_tokens_per_request ?? '—'}</dd></div>
              <div><dt className="type-label-xs">{zh ? '质量原因' : 'Quality reasons'}</dt>
                <dd className="mt-1 flex flex-wrap gap-1.5">
                  {selectedScenario.quality_reasons?.length
                    ? selectedScenario.quality_reasons.map((reason) => <Badge key={reason} variant="warning">{reason}</Badge>)
                    : <span className="text-sm text-[var(--text-muted)]">—</span>}
                </dd></div>
              <div className="md:col-span-2"><dt className="type-label-xs">{zh ? '命名空间' : 'Namespace'}</dt>
                <dd className="mt-1 break-all font-mono text-xs text-[var(--text)]">{row.namespace}</dd></div>
              <div className="md:col-span-2"><dt className="type-label-xs">{zh ? '请求哈希集合 SHA-256' : 'Request-hash list SHA-256'}</dt>
                <dd className="mt-1 break-all font-mono text-xs text-[var(--text)]">{selectedScenario.request_hashes_sha256 ?? '—'}</dd></div>
            </dl>)}
            <p className="type-body-xs text-[var(--text-muted)]">{zh
              ? '缓存关闭状态和完整路由覆盖未知；命名空间只提供新输入，不证明服务缓存被清空。'
              : 'Cache-disabled state and full routing coverage are unknown; a fresh namespace does not prove the server cache was cleared.'}</p>
          </section>
          <section className="space-y-3">
            <Eyebrow>{zh ? '压测结果' : 'Profiling result'}</Eyebrow>
            <p className="tabular-nums text-sm">{zh ? '请求 / 成功 / 失败' : 'Requests / successes / errors'}: {selectedScenario.result
              ? `${selectedScenario.result.profiling_requests} / ${selectedScenario.result.profiling_successes} / ${selectedScenario.result.profiling_errors}`
              : '—'}</p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-max border-collapse">
                <thead><tr>
                  <th className={TH}>Metric</th><th className={TH}>Value</th><th className={TH}>Unit</th>
                  <th className={TH}>Source</th><th className={TH}>Scope</th><th className={TH}>n</th><th className={TH}>Note</th>
                </tr></thead>
                <tbody>{selectedScenario.result?.metrics?.map((metric) => <tr key={`${metric.source}-${metric.key}`} className={ROW}>
                  <td className={TD}>{metric.key}</td><td className={TD}>{metric.value ?? '—'}</td><td className={TD}>{metric.unit}</td>
                  <td className={TD}>{metric.source}</td><td className={TD}>{metric.scope}</td><td className={TD}>{metric.count ?? '—'}</td>
                  <td className={TD}>{metric.reason ?? metric.availability}</td>
                </tr>)}</tbody>
              </table>
            </div>
          </section>
          <NodeWindowPanel key={selected} analysis={selectedScenario.node_analysis} taskId={taskId} subrunId={selected} rootPath={rootPath} />
          <CacheRequestGroupsPanel key={selected} cache={selectedScenario.cache} taskId={taskId} subrunId={selected} rootPath={rootPath} />
          <section className="space-y-3">
            <Eyebrow>{zh ? '请求明细' : 'Request records'}</Eyebrow>
            {requestsError && <ErrorAlert role="alert">
              <div className="flex items-center justify-between gap-3">
                <span>{requestsError}</span>
                <Button variant="outline" size="sm" onClick={() => setRequestsAttempt((value) => value + 1)}>{zh ? '重试' : 'Retry'}</Button>
              </div>
            </ErrorAlert>}
            {requests ? <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-max border-collapse"><thead><tr>
                  <th className={TH}># / ID</th><th className={TH}>{zh ? '状态' : 'Status'}</th><th className={TH}>{zh ? '延迟（秒）' : 'Latency (s)'}</th><th className={TH}>TTFT (s)</th>
                  <th className={TH}>{zh ? '输入 token（来源）' : 'Input tokens (source)'}</th><th className={TH}>{zh ? '缓存 token（服务端）' : 'Cached tokens (server)'}</th>
                </tr></thead><tbody>{requests.requests.map((row) => <tr key={row.index} className={ROW}>
                  <td className={TD}>{row.index} / {row.request_id ?? '—'} / {row.session_id ?? '—'} / {row.turn_index ?? '—'}</td>
                  <td className={TD}>{row.status}</td><td className={TD}>{row.latency_seconds ?? '—'}</td><td className={TD}>{row.ttft_seconds ?? '—'}</td>
                  <td className={TD}>{row.prompt_tokens ?? '—'} ({row.prompt_tokens_source ?? '—'})</td><td className={TD}>{row.cached_tokens ?? '—'}</td>
                </tr>)}</tbody></table>
              </div>
              <div className="flex items-center justify-between gap-2 text-sm">
                <span className="type-body-xs text-[var(--text-muted)]">{requests.requests.length ? requests.offset + 1 : 0}–{requests.offset + requests.requests.length} / {requests.total}</span>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - requests.limit))}>{zh ? '上一页' : 'Previous'}</Button>
                  <Button variant="outline" size="sm" disabled={offset + requests.limit >= requests.total} onClick={() => setOffset(offset + requests.limit)}>{zh ? '下一页' : 'Next'}</Button>
                </div>
              </div>
            </> : !requestsError && <p role="status" className="text-sm text-[var(--text-muted)]">{zh ? '正在加载请求…' : 'Loading requests…'}</p>}
          </section>
          <section className="space-y-3">
            <Eyebrow>{zh ? '节点原始样本（语义待验证）' : 'Raw node samples (semantics unverified)'}</Eyebrow>
            {metricsError && <ErrorAlert role="alert">
              <div className="flex items-center justify-between gap-3">
                <span>{metricsError}</span>
                <Button variant="outline" size="sm" onClick={() => setMetricsAttempt((value) => value + 1)}>{zh ? '重试' : 'Retry'}</Button>
              </div>
            </ErrorAlert>}
            <p className="tabular-nums text-sm">{metrics ? `${metrics.samples.length} / ${metrics.total}` : metricsError ? '—' : (zh ? '加载中…' : 'Loading…')}</p>
            {metrics && <>
              <div className="max-h-80 overflow-auto">
                <table className="w-full min-w-max border-collapse"><thead><tr>
                  <th className={TH}>{zh ? '时间' : 'Timestamp'}</th><th className={TH}>{zh ? '节点' : 'Node'}</th>
                  <th className={TH}>{zh ? '指标' : 'Metric'}</th><th className={TH}>{zh ? '观测' : 'Observation'}</th>
                </tr></thead>
                  <tbody>{metrics.samples.map((sample, index) => <tr key={`${sample.timestamp_ns}-${sample.metric_name}-${index}`} className={ROW}>
                    <td className={TD}>{sample.timestamp_ns}</td><td className={TD}>{sample.node_id ?? '—'} / {sample.role ?? '—'}</td>
                    <td className={TD}>{sample.metric_name}</td><td className={TD}>{JSON.stringify(sample.observation)}</td>
                  </tr>)}</tbody>
                </table>
              </div>
              <div className="flex items-center justify-between gap-2 text-sm">
                <span className="type-body-xs text-[var(--text-muted)]">{metrics.samples.length ? metrics.offset + 1 : 0}–{metrics.offset + metrics.samples.length} / {metrics.total}</span>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" disabled={metricOffset === 0} onClick={() => setMetricOffset(Math.max(0, metricOffset - metrics.limit))}>{zh ? '上一页' : 'Previous'}</Button>
                  <Button variant="outline" size="sm" disabled={metricOffset + metrics.limit >= metrics.total} onClick={() => setMetricOffset(metricOffset + metrics.limit)}>{zh ? '下一页' : 'Next'}</Button>
                </div>
              </div>
            </>}
          </section>
        </div>
      </Card>}
    </>}
  </div> : null

  const comparisonPanel = experiment ? <div className="flex flex-col gap-4">
    <Card title={zh ? '基线与候选' : 'Baseline and candidate'}>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge>{zh ? '基准' : 'Baseline'}</Badge>
          <span className="break-all font-mono text-xs">{baselineId ?? '—'}</span>
          {baselineId && <Button variant="ghost" size="sm" onClick={() => setParam('baseline', undefined)}>{zh ? '清除基准' : 'Clear baseline'}</Button>}
          <Badge>{zh ? '候选' : 'Candidate'}</Badge>
          <span className="break-all font-mono text-xs">{candidateId ?? '—'}</span>
          {candidateId && <Button variant="ghost" size="sm" onClick={() => setParam('candidate', undefined)}>{zh ? '清除候选' : 'Clear candidate'}</Button>}
        </div>
        <p className="type-body-xs text-[var(--text-muted)]">{zh
          ? '选择保存在 URL 中，刷新或分享后会按当前计划的身份与可比条件重新验证；清除候选不会清除基准，切换输出根目录会清空选择。'
          : 'Selections live in the URL and are revalidated against the current plan on refresh or share; clearing the candidate keeps the baseline, and switching outputs root clears both.'}</p>
      </div>
    </Card>
    <CacheComparisonPanel baseline={baseline} candidate={candidate} />
    <CrossExperimentComparisonPanel experiment={experiment} baseline={baseline} rootPath={rootPath} />
    <CacheRepetitionsPanel taskId={taskId} planHash={experiment.plan.plan_hash}
      rows={experiment.plan.subruns.slice(matrixOffset, matrixOffset + 50)} rootPath={rootPath} />
  </div> : null

  return <div className="page-enter space-y-4">
    <PerformanceResultHeader
      breadcrumbs={[
        { label: zh ? '性能测试' : 'Performance', href: `/performance?root_path=${encodeURIComponent(rootPath)}` },
        { label: zh ? '前缀缓存实验' : 'Prefix cache experiment' },
        { label: taskId },
      ]}
      testType="cache_experiment"
      title={taskId}
      meta={[
        { label: zh ? '计划哈希' : 'Plan hash', value: experiment?.plan.plan_hash },
        {
          label: zh ? '子运行' : 'Subruns',
          value: experiment ? `${experiment.state.completed_subruns}/${experiment.state.total_subruns}` : null,
        },
        { label: zh ? '阶段' : 'Phase', value: experiment?.state.phase ?? experiment?.state.last_phase },
      ]}
      status={experiment?.state.status}
      quality={experiment ? experiment.state.quality : undefined}
      htmlUrl={getCacheExperimentReportUrl(taskId, rootPath)}
    />
    <PerformanceQualityBanner
      status={experiment?.state.status}
      quality={experiment ? experiment.state.quality : undefined}
    />
    {error && <ErrorAlert role="alert">{error}</ErrorAlert>}
    {experiment && <>
      {summaryPanel}
      <Tabs
        tabs={[
          { key: 'overview', label: zh ? '总览' : 'Overview', panelId: 'experiment-overview-panel' },
          { key: 'matrix', label: zh ? '场景矩阵' : 'Scenario matrix', panelId: 'experiment-matrix-panel' },
          { key: 'comparison', label: zh ? '对比' : 'Comparison', panelId: 'experiment-comparison-panel' },
        ]}
        activeKey={tab}
        onChange={(key) => setParam('tab', key)}
        panels={{
          'experiment-overview-panel': overviewPanel,
          'experiment-matrix-panel': matrixPanel,
          'experiment-comparison-panel': comparisonPanel,
        }}
      />
    </>}
  </div>
}
