import { useState } from 'react'
import type { CacheScenarioResponse, CacheSubrunPlan, MetricStatistics } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'

interface Props { rows: CacheSubrunPlan[]; scenarios: Record<string, CacheScenarioResponse> }
const loadKey = (row: CacheSubrunPlan) => `${row.load_mode}:${row.load_point.concurrency ?? row.load_point.rate}`

export default function CacheMatrixChart({ rows, scenarios }: Props) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [group, setGroup] = useState('')
  const [metricKey, setMetricKey] = useState('time_to_first_token')
  const groups = [...new Set(rows.map((row) => `${loadKey(row)}:${row.repetition}`))]
  const active = groups.includes(group) ? group : groups[0]
  const candidates = rows.filter((row) => `${loadKey(row)}:${row.repetition}` === active).flatMap((row) => {
    const result = scenarios[row.subrun_id]?.result
    const metrics = result?.metrics?.filter((metric) => metric.key === metricKey && metric.value != null && metric.availability !== 'unavailable') ?? []
    return result?.quality === 'invalid' || metrics.length !== 1 ? [] : [{ row, result, metric: metrics[0] }]
  })
  const reference = candidates[0]
  const sameSemantics = (metric: MetricStatistics) => reference && metric.scope === reference.metric.scope &&
    metric.source === reference.metric.source && metric.unit === reference.metric.unit
  const points = candidates.filter((item) => sameSemantics(item.metric) && item.result?.engine === reference?.result?.engine &&
    item.result?.executor_version === reference?.result?.executor_version && Number.isFinite(item.metric.value))
  const top = Math.max(1e-12, ...points.map((item) => item.metric.value!))
  return <section className="space-y-2 rounded border border-[var(--border)] p-3" aria-label={zh ? '矩阵散点图' : 'Matrix scatter plot'}>
    <h2 className="font-semibold">{zh ? '矩阵散点图' : 'Matrix scatter plot'}</h2>
    <p className="text-sm">{zh ? '仅显示当前页，固定负载和重复序号；缺失点不补零、不连线。' : 'Current page only; load and repetition are fixed. Missing points are neither filled nor connected.'}</p>
    {active && <label className="text-sm">{zh ? '负载 / 重复' : 'Load / repetition'} <select value={active} onChange={(event) => setGroup(event.target.value)}>
      {groups.map((value) => { const row = rows.find((item) => `${loadKey(item)}:${item.repetition}` === value)!
        return <option value={value} key={value}>{loadKey(row)} / {row.repetition + 1}</option> })}</select></label>}
    {' '}<label className="text-sm">{zh ? '指标' : 'Metric'} <select value={metricKey} onChange={(event) => setMetricKey(event.target.value)}>
      <option value="time_to_first_token">TTFT</option><option value="request_throughput">{zh ? '请求吞吐' : 'Request throughput'}</option></select></label>
    {points.length ? <><p className="text-xs">{reference?.metric.source} / {reference?.metric.scope} / {reference?.metric.unit}; n={points.length}</p>
      <svg role="img" aria-label={zh ? '目标比例与指标散点图' : 'Target ratio versus metric scatter plot'} viewBox="0 0 100 40" className="h-28 w-full" preserveAspectRatio="none">
        {points.map(({ row, metric }) => <circle key={row.subrun_id} cx={2 + row.target_ratio * 96} cy={38 - metric.value! / top * 36} r="1.3">
          <title>{row.subrun_id}: {row.target_ratio * 100}% → {metric.value} {metric.unit}, n={metric.count ?? '—'}</title></circle>)}
      </svg><p className="text-xs">0% → 100% · {zh ? '目标前缀复用' : 'target prefix reuse'}</p></> : <p>{zh ? '没有可比的已观测指标。' : 'No comparable observed metrics.'}</p>}
  </section>
}
