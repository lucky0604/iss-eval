import { useEffect, useState } from 'react'
import type { CacheNodeSeriesResponse, NodeMetricWindow } from '@/api/generated/contracts'
import { getCacheNodeSeries, getPerfNodeSeries } from '@/api/perf'
import { useLocale } from '@/contexts/LocaleContext'

export type NodeSeriesSource =
  | { kind: 'experiment'; taskId: string; subrunId: string; rootPath?: string }
  | { kind: 'archive'; rootPath: string; path: string }
interface Props { source: NodeSeriesSource; series: NodeMetricWindow; maxGap: number; start?: number; end?: number }
const labelsKey = (labels: Record<string, string>) => JSON.stringify(Object.entries(labels).sort(([a], [b]) => a.localeCompare(b)))

export default function NodeSeriesChart({ source, series, maxGap, start, end }: Props) {
  const taskId = source.kind === 'experiment' ? source.taskId : undefined
  const subrunId = source.kind === 'experiment' ? source.subrunId : undefined
  const rootPath = source.rootPath
  const path = source.kind === 'archive' ? source.path : undefined
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [data, setData] = useState<CacheNodeSeriesResponse | null>(null)
  const [failed, setFailed] = useState(false)
  const selectedLabels = labelsKey(series.labels)
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    const filter = { nodeId: series.node_id, metricName: series.metric.name, labels: Object.fromEntries(JSON.parse(selectedLabels) as [string, string][]), start, end, signal: controller.signal }
    const request = rootPath !== undefined && path !== undefined
      ? getPerfNodeSeries(rootPath, path, filter)
      : getCacheNodeSeries(taskId!, subrunId!, filter, rootPath)
    request
      .then((value) => { if (active) { setData(value); setFailed(false) } }).catch(() => { if (active) setFailed(true) })
    return () => { active = false; controller.abort() }
  }, [taskId, subrunId, rootPath, path, series.node_id, series.metric.name, selectedLabels, start, end])
  const samples = (data?.series.samples ?? []).filter((sample) => labelsKey(sample.labels) === selectedLabels &&
    typeof sample.observation.value === 'number' && Number.isFinite(sample.observation.value) &&
    (start == null || sample.timestamp_ns >= start) && (end == null || sample.timestamp_ns <= end))
  const values = samples.map((sample) => ({ at: sample.timestamp_ns, value: Number(sample.observation.value) * (series.metric.scale ?? 1) }))
  const minTime = values[0]?.at ?? 0
  const maxTime = values[values.length - 1]?.at ?? minTime
  const minValue = Math.min(0, ...values.map((item) => item.value))
  const maxValue = Math.max(1, ...values.map((item) => item.value))
  const position = (point: { at: number; value: number }) => ({
    x: 2 + 96 * (point.at - minTime) / Math.max(1, maxTime - minTime),
    y: 38 - 36 * (point.value - minValue) / Math.max(1e-12, maxValue - minValue),
  })
  const segments: typeof values[] = []
  for (const point of values) {
    const last = segments[segments.length - 1]
    if (!last || point.at - last[last.length - 1].at > maxGap) segments.push([point])
    else last.push(point)
  }
  return <div className="space-y-1 text-xs">
    <p>{series.metric.name} · {JSON.stringify(series.labels)} · {series.metric.unit} · {zh ? '节点采样，不跨断采连接' : 'Node samples; gaps are not interpolated'}</p>
    {failed ? <p role="status">{zh ? '序列不可用，保留上表。' : 'Series unavailable; the table remains available.'}</p> : null}
    {data?.series.downsample === 'uniform_index' && <p>{zh ? '等间隔序号抽取原始样本' : 'Uniform index selection of raw samples'}: {data.series.samples.length} / {data.series.total} · {zh ? '不保证包含峰值，间断可能更明显。' : 'Peaks may be omitted and gaps may appear wider.'}</p>}
    {values.length ? <svg role="img" aria-label={zh ? '节点指标时间序列' : 'Node metric time series'} viewBox="0 0 100 40" className="h-24 w-full" preserveAspectRatio="none">
      {segments.filter((segment) => segment.length > 1).map((segment, index) => <polyline key={index} fill="none" stroke="currentColor" strokeWidth=".7" points={segment.map((point) => { const p = position(point); return `${p.x},${p.y}` }).join(' ')} />)}
      {values.map((point, index) => { const p = position(point); return <circle key={index} cx={p.x} cy={p.y} r=".8"><title>{point.at} ns: {point.value} {series.metric.unit}</title></circle> })}
    </svg> : <p>{zh ? '窗口内没有匹配样本。' : 'No matching samples in the window.'}</p>}
  </div>
}
