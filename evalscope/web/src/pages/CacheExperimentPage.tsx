import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getCacheExperiment, getCacheNodeMetrics, getCacheRequests, getCacheScenario } from '@/api/perf'
import type { CacheExperimentResponse, CacheNodeMetricsResponse, CacheRequestsResponse, CacheScenarioResponse, CacheSubrunPlan } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import { formatMetric } from '@/domain/metric'
import Button from '@/components/ui/Button'
import NodeWindowPanel from '@/components/perf/NodeWindowPanel'
import CacheComparisonPanel from '@/components/perf/CacheComparisonPanel'
import CacheMatrixChart from '@/components/perf/CacheMatrixChart'
import CrossExperimentComparisonPanel from '@/components/perf/CrossExperimentComparisonPanel'
import CacheRepetitionsPanel from '@/components/perf/CacheRepetitionsPanel'
import CacheRequestGroupsPanel from '@/components/perf/CacheRequestGroupsPanel'

function percentage(value: number | null | undefined): string {
  return value == null ? '—' : `${formatMetric(value * 100, null).primary}%`
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
  return <div className="space-y-4 page-enter">
    <h1 className="text-xl font-semibold">{zh ? '前缀缓存实验' : 'Prefix cache experiment'} · {taskId}</h1>
    <a className="text-sm text-[var(--accent)]" href={`/api/v1/perf/experiments/${encodeURIComponent(taskId)}/report`} target="_blank" rel="noreferrer">{zh ? '打开 HTML 摘要' : 'Open HTML summary'}</a>
    {error && <p role="alert">{error}</p>}
    {experiment && <>
      <p>{zh ? '状态' : 'Status'}: {experiment.state.status} · {zh ? '质量' : 'Quality'}: {experiment.state.quality ?? '—'} · {experiment.state.completed_subruns}/{experiment.state.total_subruns}</p>
      <details className="rounded border border-[var(--border)] p-3"><summary>{zh ? '实验身份与预算' : 'Experiment identity and budget'}</summary>
        <p className="break-all text-sm">{zh ? '计划哈希' : 'Plan hash'}: {experiment.plan.plan_hash}</p>
        <p className="break-all text-sm">{zh ? '配置哈希' : 'Configuration hash'}: {experiment.plan.configuration_hash}</p>
        <p className="break-all text-sm">Tokenizer: {experiment.plan.tokenizer_id} · Template: {experiment.plan.template_id}</p>
        <p className="break-all text-sm">{zh ? '已核验配置（不含凭证）' : 'Verified configuration (credentials excluded)'}: {experiment.plan.configuration ? JSON.stringify(experiment.plan.configuration) : '—'}</p>
        <p className="text-sm">{zh ? '正式 / 预热请求预算' : 'Profiling / primer request budget'}: {experiment.plan.profiling_requests} / {experiment.plan.primer_requests}</p>
      </details>
      <p className="text-sm text-[var(--text-muted)]">{zh ? '目标比例、生成比例与服务端报告的缓存命中彼此独立；缺失值显示为 —。TTFT 和吞吐来自 AIPerf 客户端汇总。' : 'Target, generated reuse, and server-reported cache hits are distinct; missing values are shown as —. TTFT and throughput come from the AIPerf client summary.'}</p>
      <div className="overflow-x-auto"><table className="w-full min-w-max whitespace-nowrap text-left text-sm [&_th]:px-2 [&_td]:px-2">
        <thead><tr><th>{zh ? '子任务' : 'Run'}</th><th>{zh ? '负载' : 'Load'}</th><th>{zh ? '重复' : 'Repeat'}</th><th>{zh ? '目标' : 'Target'}</th><th>{zh ? '实际生成' : 'Generated'}</th><th>{zh ? '服务端命中' : 'Server cache'}</th><th>{zh ? '覆盖请求' : 'Observed requests'}</th><th>{zh ? '平均 TTFT（秒）' : 'Mean TTFT (s)'}</th><th>{zh ? '请求/秒' : 'Requests/s'}</th><th>{zh ? '成功' : 'Success'}</th><th>{zh ? '状态' : 'Status'}</th><th>{zh ? '对比' : 'Compare'}</th></tr></thead>
        <tbody>{experiment.plan.subruns.slice(matrixOffset, matrixOffset + 50).map((row) => {
          const scenario = scenarios[row.subrun_id]
          const state = experiment.state.subruns.find((item) => item.subrun_id === row.subrun_id)
          return <tr key={row.subrun_id}>
            <td><button className="text-[var(--accent)]" disabled={!scenario} onClick={() => scenario && select(row.subrun_id, scenario)}>{row.subrun_id}</button></td>
            <td>{row.load_mode === 'closed_loop' ? row.load_point.concurrency : row.load_point.rate} ({row.load_mode})</td><td>{row.repetition + 1}</td>
            <td>{percentage(row.target_ratio)}</td><td>{percentage(scenario?.generated_ratio)}</td><td>{percentage(scenario?.cache?.ratio)}</td>
            <td>{scenario?.cache ? `${scenario.cache.observed_requests}/${scenario.cache.successful_requests ?? '—'}` : '—'}</td>
            <td>{formatMetric(scenario?.result?.metrics?.find((metric) => metric.key === 'time_to_first_token')?.value, null).primary}</td>
            <td>{formatMetric(scenario?.result?.metrics?.find((metric) => metric.key === 'request_throughput')?.value, null).primary}</td>
            <td>{scenario?.result ? `${scenario.result.profiling_successes}/${scenario.result.profiling_requests}` : '—'}</td><td>{state?.status}</td>
            <td className="whitespace-nowrap"><button disabled={!scenario} aria-label={`${zh ? '基准' : 'Baseline'} ${row.subrun_id}`} onClick={() => scenario && setBaseline({ plan: row, scenario })}>{zh ? '基准' : 'Baseline'}</button>{' / '}<button disabled={!scenario} aria-label={`${zh ? '候选' : 'Candidate'} ${row.subrun_id}`} onClick={() => scenario && setCandidate({ plan: row, scenario })}>{zh ? '候选' : 'Candidate'}</button></td>
          </tr>
        })}</tbody>
      </table></div>
      {experiment.plan.subruns.length > 50 && <div className="flex gap-2 text-sm">
        <span>{matrixOffset + 1}–{Math.min(matrixOffset + 50, experiment.plan.subruns.length)} / {experiment.plan.subruns.length}</span>
        <Button variant="outline" size="sm" disabled={matrixOffset === 0} onClick={() => setMatrixOffset(Math.max(0, matrixOffset - 50))}>{zh ? '上一页' : 'Previous'}</Button>
        <Button variant="outline" size="sm" disabled={matrixOffset + 50 >= experiment.plan.subruns.length} onClick={() => setMatrixOffset(matrixOffset + 50)}>{zh ? '下一页' : 'Next'}</Button>
      </div>}
      <CacheMatrixChart rows={experiment.plan.subruns.slice(matrixOffset, matrixOffset + 50)} scenarios={scenarios} />
      <CacheComparisonPanel baseline={baseline} candidate={candidate} />
      <CrossExperimentComparisonPanel experiment={experiment} baseline={baseline} />
      <CacheRepetitionsPanel taskId={taskId} planHash={experiment.plan.plan_hash} rows={experiment.plan.subruns.slice(matrixOffset, matrixOffset + 50)} />
      {selected && selectedScenario && <section className="space-y-2">
        <h2 className="font-semibold">{selected}</h2>
        {experiment.plan.subruns.filter((row) => row.subrun_id === selected).map((row) => <details key={row.subrun_id} className="rounded border border-[var(--border)] p-3"><summary>{zh ? '输入和执行配置' : 'Input and execution details'}</summary>
          <p className="text-sm">{zh ? '执行顺序 / 随机种子 / 计划请求 / 输出上限' : 'Execution order / seed / planned requests / output limit'}: {row.order + 1} / {row.seed} / {row.request_count} / {row.output_token_limit ?? '—'}</p>
          <p className="text-sm">{zh ? '生成的每请求输入 token 数' : 'Generated input tokens per request'}: {selectedScenario.input_tokens_per_request ?? '—'}</p>
          <p className="break-all text-sm">{zh ? '命名空间' : 'Namespace'}: {row.namespace}</p>
          <p className="break-all text-sm">{zh ? '请求哈希集合 SHA-256' : 'Request-hash list SHA-256'}: {selectedScenario.request_hashes_sha256 ?? '—'}</p>
          <p className="text-sm">{zh ? '缓存关闭状态和完整路由覆盖未知；命名空间只提供新输入，不证明服务缓存被清空。' : 'Cache-disabled state and full routing coverage are unknown; a fresh namespace does not prove the server cache was cleared.'}</p>
        </details>)}
        <p>{zh ? '质量原因' : 'Quality reasons'}: {selectedScenario.quality_reasons?.join(', ') || '—'}</p>
        <p>{zh ? '压测请求 / 成功 / 失败' : 'Requests / successes / errors'}: {selectedScenario.result ? `${selectedScenario.result.profiling_requests} / ${selectedScenario.result.profiling_successes} / ${selectedScenario.result.profiling_errors}` : '—'}</p>
        <ul>{selectedScenario.result?.metrics?.map((metric) => <li key={`${metric.source}-${metric.key}`}>{metric.key}: {metric.value ?? '—'} {metric.unit} ({metric.source}, {metric.scope}, n={metric.count ?? '—'}, {metric.reason ?? metric.availability})</li>)}</ul>
        <NodeWindowPanel key={selected} analysis={selectedScenario.node_analysis} taskId={taskId} subrunId={selected} />
        <CacheRequestGroupsPanel key={selected} cache={selectedScenario.cache} taskId={taskId} subrunId={selected} />
        <h3 className="font-semibold">{zh ? '请求明细' : 'Request records'}</h3>
        {requests ? <><div className="overflow-x-auto"><table className="w-full min-w-max whitespace-nowrap text-left text-sm [&_th]:px-2 [&_td]:px-2"><thead><tr>
          <th># / ID</th><th>{zh ? '状态' : 'Status'}</th><th>{zh ? '延迟（秒）' : 'Latency (s)'}</th><th>TTFT (s)</th>
          <th>{zh ? '输入 token（来源）' : 'Input tokens (source)'}</th><th>{zh ? '缓存 token（服务端）' : 'Cached tokens (server)'}</th>
        </tr></thead><tbody>{requests.requests.map((row) => <tr key={row.index}>
          <td>{row.index} / {row.request_id ?? '—'} / {row.session_id ?? '—'} / {row.turn_index ?? '—'}</td><td>{row.status}</td><td>{row.latency_seconds ?? '—'}</td><td>{row.ttft_seconds ?? '—'}</td>
          <td>{row.prompt_tokens ?? '—'} ({row.prompt_tokens_source ?? '—'})</td><td>{row.cached_tokens ?? '—'}</td>
        </tr>)}</tbody></table></div><div className="flex gap-2 text-sm">
          <span>{requests.requests.length ? requests.offset + 1 : 0}–{requests.offset + requests.requests.length} / {requests.total}</span>
          <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - requests.limit))}>{zh ? '上一页' : 'Previous'}</Button>
          <Button variant="outline" size="sm" disabled={offset + requests.limit >= requests.total} onClick={() => setOffset(offset + requests.limit)}>{zh ? '下一页' : 'Next'}</Button>
        </div></> : <p>—</p>}
        <h3 className="font-semibold">{zh ? '节点原始样本（语义待验证）' : 'Raw node samples (semantics unverified)'}</h3>
        <p className="text-sm">{metrics ? `${metrics.samples.length} / ${metrics.total}` : '—'}</p>
        {metrics && <><div className="overflow-auto max-h-80"><table className="w-full min-w-max whitespace-nowrap text-left text-sm [&_th]:px-2 [&_td]:px-2"><thead><tr><th>{zh ? '时间' : 'Timestamp'}</th><th>{zh ? '节点' : 'Node'}</th><th>{zh ? '指标' : 'Metric'}</th><th>{zh ? '观测' : 'Observation'}</th></tr></thead>
          <tbody>{metrics.samples.map((sample, index) => <tr key={`${sample.timestamp_ns}-${sample.metric_name}-${index}`}><td>{sample.timestamp_ns}</td><td>{sample.node_id ?? '—'} / {sample.role ?? '—'}</td><td>{sample.metric_name}</td><td>{JSON.stringify(sample.observation)}</td></tr>)}</tbody>
        </table></div><div className="flex gap-2 text-sm"><span>{metrics.samples.length ? metrics.offset + 1 : 0}–{metrics.offset + metrics.samples.length} / {metrics.total}</span>
          <Button variant="outline" size="sm" disabled={metricOffset === 0} onClick={() => setMetricOffset(Math.max(0, metricOffset - metrics.limit))}>{zh ? '上一页' : 'Previous'}</Button>
          <Button variant="outline" size="sm" disabled={metricOffset + metrics.limit >= metrics.total} onClick={() => setMetricOffset(metricOffset + metrics.limit)}>{zh ? '下一页' : 'Next'}</Button></div></>}
      </section>}
    </>}
  </div>
}
