import { useState } from 'react'
import type { NodeMetricsAnalysis, NodeMetricWindow } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import NodeSeriesChart, { type NodeSeriesSource } from './NodeSeriesChart'

type Props = { analysis?: NodeMetricsAnalysis | null } & (
  | { taskId: string; subrunId: string; rootPath?: string; archive?: never }
  | { archive: { rootPath: string; path: string }; taskId?: never; subrunId?: never; rootPath?: never }
)

const PRIMARY_KEYS = ['gpu_utilization', 'gpu_memory_used', 'gpu_power_usage', 'gpu_temperature']
const display = (value: number | null | undefined) => value == null ? '—' : String(value)

export default function GpuTelemetryPanel({ analysis, taskId, subrunId, rootPath, archive }: Props) {
  const source: NodeSeriesSource = archive
    ? { kind: 'archive', ...archive }
    : { kind: 'experiment', taskId: taskId!, subrunId: subrunId!, rootPath }
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [selected, setSelected] = useState('')
  const rows = (analysis?.series ?? []).filter((row) => row.role === 'gpu')
  if (rows.length === 0) return null
  const nodes = [...new Set(rows.map((row) => row.node_id))]
  const node = nodes.includes(selected) ? selected : nodes[0]
  const nodeRows = rows.filter((row) => row.node_id === node)
  const primary = PRIMARY_KEYS.map((key) => nodeRows.find((row) => row.metric.key === key)).filter(
    (row): row is NodeMetricWindow => row !== undefined,
  )
  const secondary = nodeRows.filter((row) => !PRIMARY_KEYS.includes(row.metric.key))
  return (
    <section className="space-y-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-5 shadow-[var(--shadow-sm)]" aria-label={zh ? 'GPU 遥测' : 'GPU telemetry'}>
      <h3 className="type-label-xs">{zh ? 'GPU 遥测' : 'GPU telemetry'}</h3>
      <p className="type-body-xs text-[var(--text-muted)]">
        {zh
          ? '来自 AIPerf GPU telemetry 或 DCGM Prometheus 快照，按设备时间线聚合，不代表逐请求归因。'
          : 'From AIPerf GPU telemetry or DCGM Prometheus snapshots, aggregated per device timeline and not attributed to individual requests.'}
      </p>
      <label className="text-sm">{zh ? 'GPU 节点' : 'GPU node'} <select value={node} onChange={(event) => setSelected(event.target.value)}>
        {nodes.map((id) => <option key={id} value={id}>{id}</option>)}
      </select></label>
      <p className="text-sm">{zh ? '序列数' : 'Series'}: {nodeRows.length} · {zh ? '窗口来源' : 'Window source'}: {analysis?.window_source ?? '—'}</p>
      {primary.length > 0 && <div className="grid gap-4 md:grid-cols-2">{primary.map((row) => (
        <div key={`${row.metric.key}-${JSON.stringify(row.labels)}`} className="rounded-[var(--radius-sm)] border border-[var(--border)] p-3">
          <NodeSeriesChart source={source} series={row} maxGap={analysis!.max_gap_ns}
            start={analysis?.window?.start_ns} end={analysis?.window?.end_ns} />
        </div>
      ))}</div>}
      {secondary.length > 0 && <div className="overflow-x-auto"><table className="w-full min-w-max border-collapse whitespace-nowrap text-left text-xs [&_th]:border-b [&_th]:border-[var(--border)] [&_th]:px-3 [&_th]:py-2 [&_td]:border-b [&_td]:border-[var(--border)] [&_td]:px-3 [&_td]:py-2"><thead><tr>
        <th>{zh ? 'GPU 指标' : 'GPU metric'}</th><th>{zh ? '标签' : 'Labels'}</th><th>{zh ? '窗口值' : 'Window value'}</th><th>{zh ? '状态 / 原因' : 'Status / reason'}</th>
      </tr></thead><tbody>{secondary.map((row, index) => (
        <tr key={`${row.metric.key}-${JSON.stringify(row.labels)}-${index}`}>
          <td>{row.metric.key}<br />{row.metric.name}</td>
          <td>{JSON.stringify(row.labels)}</td>
          <td>{row.histogram ? `${display(row.histogram.mean)} ${row.histogram.unit}` : `${display(row.statistic?.value)} ${row.metric.unit}`}</td>
          <td>{row.histogram ? `${row.histogram.availability} / ${row.histogram.reason ?? '—'}` : `${row.statistic?.availability ?? 'missing'} / ${row.statistic?.reason ?? row.reason ?? '—'}`}</td>
        </tr>
      ))}</tbody></table></div>}
    </section>
  )
}
