import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import { getCacheExperiment, getCacheNodeMetrics, getCacheRequests, getCacheScenario } from '@/api/perf'
import type { CacheExperimentResponse, CacheNodeMetricsResponse, CacheRequestsResponse, CacheScenarioResponse, CacheSubrunPlan } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import { formatMetric } from '@/domain/metric'
import Badge from '@/components/ui/Badge'
import Breadcrumb from '@/components/ui/Breadcrumb'
import Button from '@/components/ui/Button'
import Card from '@/components/ui/Card'
import ErrorAlert from '@/components/ui/ErrorAlert'
import Eyebrow from '@/components/ui/Eyebrow'
import KpiStrip, { type KpiItem } from '@/components/ui/KpiStrip'
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

function percentage(value: number | null | undefined): string {
  return value == null ? '—' : `${formatMetric(value * 100, null).primary}%`
}

function mean(values: number[]): number | null {
  if (!values.length) return null
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

function statusVariant(status: string | null | undefined): 'success' | 'warning' | 'danger' | 'default' {
  if (status === 'completed') return 'success'
  if (status === 'error') return 'danger'
  if (status === 'running' || status === 'partial') return 'warning'
  return 'default'
}

function qualityVariant(quality: string | null | undefined): 'success' | 'warning' | 'danger' | 'default' {
  if (quality === 'complete') return 'success'
  if (quality === 'partial') return 'warning'
  if (quality === 'invalid') return 'danger'
  return 'default'
}

export default function CacheExperimentPage() {
  const { taskId = '' } = useParams()
  return <CacheExperimentReport key={taskId} taskId={taskId} />
}

function CacheExperimentReport({ taskId }: { taskId: string }) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [experiment, setExperiment] = useState<CacheExperimentResponse | null>(null)
  const [scenarios, setScenarios] = useState<Record<string, CacheScenarioResponse>>({})
  const [metrics, setMetrics] = useState<CacheNodeMetricsResponse | null>(null)
  const [requests, setRequests] = useState<CacheRequestsResponse | null>(null)
  const [offset, setOffset] = useState(0)
  const [metricOffset, setMetricOffset] = useState(0)
  const [matrixOffset, setMatrixOffset] = useState(0)
  const [selected, setSelected] = useState('')
  const [selectedScenario, setSelectedScenario] = useState<CacheScenarioResponse | null>(null)
  const [baseline, setBaseline] = useState<{ plan: CacheSubrunPlan; scenario: CacheScenarioResponse }>()
  const [candidate, setCandidate] = useState<{ plan: CacheSubrunPlan; scenario: CacheScenarioResponse }>()
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    getCacheExperiment(taskId).then((value) => { if (active) setExperiment(value) })
      .catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : String(err)) })
    return () => { active = false }
  }, [taskId])
  useEffect(() => {
    if (!experiment) return
    let active = true
    const rows = experiment.state.subruns.slice(matrixOffset, matrixOffset + 50).filter((row) => row.scenario)
    Promise.allSettled(rows.map(async (row) => [row.subrun_id, await getCacheScenario(taskId, row.subrun_id)] as const))
      .then((values) => {
        if (active) setScenarios(Object.fromEntries(values.filter((value) => value.status === 'fulfilled').map((value) => value.value)))
      })
    return () => { active = false }
  }, [taskId, experiment, matrixOffset])
  useEffect(() => {
    if (!selected) return
    let active = true
    getCacheRequests(taskId, selected, offset).then((value) => { if (active) setRequests(value) })
      .catch(() => { if (active) setRequests(null) })
    return () => { active = false }
  }, [taskId, selected, offset])
  useEffect(() => {
    if (!selected) return
    let active = true
    getCacheNodeMetrics(taskId, selected, metricOffset).then((value) => { if (active) setMetrics(value) })
      .catch(() => { if (active) setMetrics(null) })
    return () => { active = false }
  }, [taskId, selected, metricOffset])
  const select = (id: string, scenario: CacheScenarioResponse) => {
    if (id === selected) { setOffset(0); return }
    setSelected(id)
    setSelectedScenario(scenario)
    setMetrics(null)
    setRequests(null)
    setOffset(0)
    setMetricOffset(0)
  }

  const loaded = Object.values(scenarios)
  const cacheRatios = loaded
    .map((scenario) => scenario.cache?.ratio)
    .filter((value): value is number => typeof value === 'number')
  const firstToken = loaded
    .map((scenario) => scenario.result?.metrics?.find((metric) => metric.key === 'time_to_first_token')?.value)
    .filter((value): value is number => typeof value === 'number')
  const successes = loaded.reduce((sum, scenario) => sum + (scenario.result?.profiling_successes ?? 0), 0)
  const planned = loaded.reduce((sum, scenario) => sum + (scenario.result?.profiling_requests ?? 0), 0)
  const kpis: KpiItem[] = experiment ? [
    {
      label: zh ? '已完成子运行' : 'Completed subruns',
      value: `${experiment.state.completed_subruns}/${experiment.state.total_subruns}`,
    },
    {
      label: zh
        ? `服务端缓存命中均值（已加载 ${cacheRatios.length} 组）`
        : `Mean server cache hit (loaded ${cacheRatios.length})`,
      value: percentage(mean(cacheRatios)),
    },
    {
      label: zh ? `平均 TTFT（已加载 ${firstToken.length} 组，秒）` : `Mean TTFT (loaded ${firstToken.length}, s)`,
      value: firstToken.length ? formatMetric(mean(firstToken), null).primary : '—',
    },
    {
      label: zh ? `成功请求（已加载 ${loaded.length} 组）` : `Successful requests (loaded ${loaded.length})`,
      value: planned ? `${successes}/${planned}` : '—',
    },
  ] : []

  return <div className="page-enter space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 space-y-2">
        <Breadcrumb items={[
          { label: zh ? '性能测试' : 'Performance', href: '/tasks?tab=perf' },
          { label: zh ? '前缀缓存实验' : 'Prefix cache experiment' },
          { label: taskId },
        ]} />
        <div className="min-w-0">
          <Eyebrow>{zh ? '缓存实验报告' : 'Cache experiment report'}</Eyebrow>
          <h1 className="mt-1 break-all font-mono text-xl font-semibold text-[var(--text)]">{taskId}</h1>
        </div>
        {experiment && <div className="flex flex-wrap items-center gap-2">
          <Badge variant={statusVariant(experiment.state.status)}>{experiment.state.status}</Badge>
          <Badge variant={qualityVariant(experiment.state.quality)}>
            {zh ? '质量' : 'Quality'}: {experiment.state.quality ?? '—'}
          </Badge>
          <span className="type-body-xs text-[var(--text-muted)]">
            {experiment.state.completed_subruns}/{experiment.state.total_subruns} {zh ? '子运行' : 'subruns'}
          </span>
        </div>}
      </div>
      <a
        className="inline-flex items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--border-md)] px-4 py-2 text-sm text-[var(--text)] transition-colors duration-150 hover:border-[var(--accent)] hover:text-[var(--accent)]"
        href={`/api/v1/perf/experiments/${encodeURIComponent(taskId)}/report`}
        target="_blank"
        rel="noreferrer"
      >
        {zh ? '打开 HTML 摘要' : 'Open HTML summary'}
        <ExternalLink size={14} />
      </a>
    </div>
    {error && <ErrorAlert role="alert">{error}</ErrorAlert>}
    {experiment && <>
      <KpiStrip items={kpis} layout="dense" />
      <p className="type-body-xs text-[var(--text-muted)]">{zh
        ? '目标比例、生成比例与服务端报告的缓存命中彼此独立；缺失值显示为 —。TTFT 和吞吐来自 AIPerf 客户端汇总，KPI 仅统计当前页已加载的子运行。'
        : 'Target, generated reuse, and server-reported cache hits are distinct; missing values are shown as —. TTFT and throughput come from the AIPerf client summary, and KPIs cover only the subruns loaded for this page.'}</p>
      <Card title={zh ? '实验身份与预算' : 'Experiment identity and budget'} collapsible>
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
      </Card>
      <Card title={zh ? '子运行矩阵' : 'Subrun matrix'} badge={
        <span className="type-body-xs text-[var(--text-muted)]">{experiment.plan.subruns.length} {zh ? '组' : 'runs'}</span>
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
                  <button className={ID_BUTTON} disabled={!scenario} onClick={() => scenario && select(row.subrun_id, scenario)}>
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
                    <Button variant="ghost" size="sm" disabled={!scenario} aria-label={`${zh ? '基准' : 'Baseline'} ${row.subrun_id}`}
                      onClick={() => scenario && setBaseline({ plan: row, scenario })}>{zh ? '基准' : 'Baseline'}</Button>
                    <Button variant="ghost" size="sm" disabled={!scenario} aria-label={`${zh ? '候选' : 'Candidate'} ${row.subrun_id}`}
                      onClick={() => scenario && setCandidate({ plan: row, scenario })}>{zh ? '候选' : 'Candidate'}</Button>
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
      <CacheComparisonPanel baseline={baseline} candidate={candidate} />
      <CrossExperimentComparisonPanel experiment={experiment} baseline={baseline} />
      <CacheRepetitionsPanel taskId={taskId} planHash={experiment.plan.plan_hash} rows={experiment.plan.subruns.slice(matrixOffset, matrixOffset + 50)} />
      {selected && selectedScenario && <Card
        title={`${zh ? '子运行详情' : 'Subrun detail'} · ${selected}`}
        badge={<Badge variant={statusVariant(selectedScenario.status)}>{selectedScenario.status}</Badge>}
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
          <NodeWindowPanel key={selected} analysis={selectedScenario.node_analysis} taskId={taskId} subrunId={selected} />
          <CacheRequestGroupsPanel key={selected} cache={selectedScenario.cache} taskId={taskId} subrunId={selected} />
          <section className="space-y-3">
            <Eyebrow>{zh ? '请求明细' : 'Request records'}</Eyebrow>
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
            </> : <p className="text-sm text-[var(--text-muted)]">—</p>}
          </section>
          <section className="space-y-3">
            <Eyebrow>{zh ? '节点原始样本（语义待验证）' : 'Raw node samples (semantics unverified)'}</Eyebrow>
            <p className="tabular-nums text-sm">{metrics ? `${metrics.samples.length} / ${metrics.total}` : '—'}</p>
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
  </div>
}
