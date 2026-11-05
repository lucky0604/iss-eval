import { useState } from 'react'
import type { CacheScenarioResponse, CacheSubrunPlan, MetricStatistics } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import { PANEL_SELECT } from './panelControls'

interface Props { rows: CacheSubrunPlan[]; scenarios: Record<string, CacheScenarioResponse> }
const loadKey = (row: CacheSubrunPlan) => `${row.load_mode}:${row.load_point.concurrency ?? row.load_point.rate}`

export default function CacheMatrixChart({ rows, scenarios }: Props) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [group, setGroup] = useState('')
  const [axis, setAxis] = useState<'ratio' | 'load'>('ratio')
  const [metricKey, setMetricKey] = useState('time_to_first_token')
  const groupKey = (row: CacheSubrunPlan) => axis === 'ratio' ? `${loadKey(row)}:${row.repetition}` : `${row.target_ratio}:${row.load_mode}:${row.repetition}`
  const groups = [...new Set(rows.map(groupKey))]
  const active = groups.includes(group) ? group : groups[0]
  const xValue = (row: CacheSubrunPlan) => axis === 'ratio' ? row.target_ratio : (row.load_point.concurrency ?? row.load_point.rate ?? NaN)
  const candidates = rows.filter((row) => groupKey(row) === active).flatMap((row) => {
    const result = scenarios[row.subrun_id]?.result
    const metrics = result?.metrics?.filter((metric) => metric.key === metricKey && metric.value != null && metric.availability !== 'unavailable') ?? []
    if (!Number.isFinite(xValue(row)) || result?.quality === 'invalid' || metrics.length !== 1) return []
    const [metric] = metrics
    return [{ row, result, metric }]
  })
  const reference = candidates[0]
  const sameSemantics = (metric: MetricStatistics) => reference && metric.scope === reference.metric.scope &&
    metric.source === reference.metric.source && metric.unit === reference.metric.unit
  const points = candidates.filter((item) => sameSemantics(item.metric) && item.result?.engine === reference?.result?.engine &&
    item.result?.executor_version === reference?.result?.executor_version && Number.isFinite(item.metric.value))
  const top = Math.max(1e-12, ...points.map((item) => item.metric.value!))
  const minimum = axis === 'ratio' ? 0 : Math.min(...points.map(({ row }) => xValue(row)))
  const maximum = axis === 'ratio' ? 1 : Math.max(...points.map(({ row }) => xValue(row)))
  const xPosition = (row: CacheSubrunPlan) => maximum === minimum ? 50 : 2 + (xValue(row) - minimum) / (maximum - minimum) * 96
  return <section className="space-y-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-5 shadow-[var(--shadow-sm)]" aria-label={zh ? '矩阵散点图' : 'Matrix scatter plot'}>
    <h2 className="type-label-xs">{zh ? '矩阵散点图' : 'Matrix scatter plot'}</h2>
    <p className="type-body-xs text-[var(--text-muted)]">{zh ? '仅显示当前页，固定其他维度和重复序号；缺失点不补零、不连线。' : 'Current page only; other dimensions and repetition are fixed. Missing points are neither filled nor connected.'}</p>
    <label className="text-sm">{zh ? '横轴' : 'Horizontal axis'} <select className={PANEL_SELECT} value={axis} onChange={(event) => { setAxis(event.target.value as 'ratio' | 'load'); setGroup('') }}><option value="ratio">{zh ? '目标比例' : 'Target ratio'}</option><option value="load">{zh ? '负载' : 'Load'}</option></select></label>{' '}
    {active && <label className="text-sm">{axis === 'ratio' ? (zh ? '负载 / 重复' : 'Load / repetition') : (zh ? '目标比例 / 模式 / 重复' : 'Target ratio / mode / repetition')} <select className={PANEL_SELECT} value={active} onChange={(event) => setGroup(event.target.value)}>
      {groups.map((value) => { const row = rows.find((item) => groupKey(item) === value)!
        return <option value={value} key={value}>{axis === 'ratio' ? loadKey(row) : `${row.target_ratio * 100}% / ${row.load_mode}`} / {row.repetition + 1}</option> })}</select></label>}
    {' '}<label className="text-sm">{zh ? '指标' : 'Metric'} <select className={PANEL_SELECT} value={metricKey} onChange={(event) => setMetricKey(event.target.value)}>
      <option value="time_to_first_token">TTFT</option><option value="request_throughput">{zh ? '请求吞吐' : 'Request throughput'}</option></select></label>
    {points.length ? <><p className="text-xs">{reference?.metric.source} / {reference?.metric.scope} / {reference?.metric.unit}; n={points.length}</p>
      <svg role="img" aria-label={axis === 'ratio' ? (zh ? '目标比例与指标散点图' : 'Target ratio versus metric scatter plot') : (zh ? '负载与指标散点图' : 'Load versus metric scatter plot')} viewBox="0 0 100 40" className="h-28 w-full" preserveAspectRatio="none">
        {points.map(({ row, metric }) => <circle key={row.subrun_id} cx={xPosition(row)} cy={38 - metric.value! / top * 36} r="1.3">
          <title>{row.subrun_id}: {axis === 'ratio' ? `${row.target_ratio * 100}%` : xValue(row)} → {metric.value} {metric.unit}, n={metric.count ?? '—'}</title></circle>)}
      </svg><p className="text-xs">{axis === 'ratio' ? `0% → 100% · ${zh ? '目标前缀复用' : 'target prefix reuse'}` : `${minimum} → ${maximum} · ${zh ? '负载' : 'load'}`}</p></> : <p>{zh ? '没有可比的已观测指标。' : 'No comparable observed metrics.'}</p>}
  </section>
}
