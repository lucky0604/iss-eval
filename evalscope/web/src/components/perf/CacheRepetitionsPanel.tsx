import { useState } from 'react'
import type { CacheScenarioResponse, CacheSubrunPlan } from '@/api/generated/contracts'
import { comparableMetric } from '@/domain/perf/cacheComparison'
import { useLocale } from '@/contexts/LocaleContext'

interface Props { rows: CacheSubrunPlan[]; scenarios: Record<string, CacheScenarioResponse> }
const groupKey = (row: CacheSubrunPlan) => `${row.target_ratio}:${row.load_mode}:${row.load_point.concurrency ?? row.load_point.rate}`

export default function CacheRepetitionsPanel({ rows, scenarios }: Props) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [group, setGroup] = useState('')
  const [metricKey, setMetricKey] = useState('time_to_first_token')
  const groups = [...new Set(rows.map(groupKey))]
  const active = groups.includes(group) ? group : groups[0]
  const entries = rows.filter((row) => groupKey(row) === active).map((row) => {
    const result = scenarios[row.subrun_id]?.result
    const metrics = result?.metrics?.filter((metric) => metric.key === metricKey) ?? []
    const [metric] = metrics
    return { row, result, metric: metrics.length === 1 ? metric : undefined }
  })
  const reference = entries.find(({ result, metric }) => result?.quality !== 'invalid' && metric?.value != null && metric.availability !== 'unavailable' && Number.isFinite(metric.value))
  const valid = entries.filter(({ result, metric }) => reference && result?.quality !== 'invalid' && result?.engine === reference.result?.engine &&
    result?.executor_version === reference.result?.executor_version && comparableMetric(reference.metric, metric))
  const values = valid.map(({ metric }) => metric!.value!)
  return <section className="space-y-2 rounded border border-[var(--border)] p-3" aria-label={zh ? '重复运行离散度' : 'Repetition dispersion'}>
    <h2 className="font-semibold">{zh ? '重复运行离散度' : 'Repetition dispersion'}</h2>
    <p className="text-sm">{zh ? '仅统计当前矩阵页，分别显示各轮值和样本数；最小值与最大值描述轮次间离散度，不合并尾延迟。' : 'Current matrix page only. Values and sample counts are shown per repetition; the range describes between-run dispersion and does not pool tail latencies.'}</p>
    {active && <label>{zh ? '目标比例 / 负载' : 'Target ratio / load'} <select value={active} onChange={(event) => setGroup(event.target.value)}>{groups.map((key) => <option key={key}>{key}</option>)}</select></label>}{' '}
    <label>{zh ? '指标' : 'Metric'} <select value={metricKey} onChange={(event) => setMetricKey(event.target.value)}><option value="time_to_first_token">TTFT</option><option value="request_throughput">{zh ? '请求吞吐' : 'Request throughput'}</option></select></label>
    <p className="text-sm">{zh ? '可比轮数' : 'Comparable repetitions'}: {values.length} / {entries.length} · {zh ? '范围' : 'Range'}: {values.length ? `${Math.min(...values)}–${Math.max(...values)} ${reference?.metric?.unit}` : '—'}</p>
    <ul className="text-sm">{entries.map(({ row, metric }) => <li key={row.subrun_id}>#{row.repetition + 1} · {row.subrun_id}: {valid.some((item) => item.row.subrun_id === row.subrun_id) ? `${metric?.value} ${metric?.unit} (n=${metric?.count ?? '—'}, ${metric?.source}, ${metric?.scope})` : '—'}</li>)}</ul>
  </section>
}
