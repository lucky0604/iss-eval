import { useEffect, useState } from 'react'
import { getLivePerfMetrics } from '@/api/perf'
import type { PerfLiveResponse } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'

interface Props { taskId: string; active: boolean }
interface Point { subrun: string; node: string; series: string; at: number; value: number }

export default function PerfLiveMetricsPanel({ taskId, active }: Props) {
  const { t } = useLocale()
  const [snapshot, setSnapshot] = useState<PerfLiveResponse | null>(null)
  const [failed, setFailed] = useState(false)
  const [now, setNow] = useState(0)
  const [selected, setSelected] = useState('')
  const [points, setPoints] = useState<Point[]>([])

  useEffect(() => {
    if (!active) return
    let timer: ReturnType<typeof setTimeout> | undefined
    let deadline: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | undefined
    let cancelled = false
    const poll = async () => {
      controller = new AbortController()
      try {
        const next = await Promise.race([
          getLivePerfMetrics(taskId, controller.signal),
          new Promise<never>((_, reject) => { deadline = setTimeout(() => { controller?.abort(); reject(new Error('timeout')) }, 8000) }),
        ])
        if (cancelled) return
        setSnapshot(next)
        setFailed(false)
        setNow(Date.now())
        setPoints((previous) => {
          const subrun = next.current_subrun ?? ''
          const result = previous.filter((point) => point.subrun === subrun)
          for (const node of next.nodes ?? []) {
            const gauge = node.gauges?.find((item) => item.key === 'waiting_requests')
            if (!gauge || node.sampled_at_ns == null) continue
            const series = JSON.stringify([gauge.name, gauge.labels])
            if (!result.some((point) => point.node === node.node_id && point.series === series && point.at === node.sampled_at_ns))
              result.push({ subrun, node: node.node_id, series, at: node.sampled_at_ns, value: gauge.value })
          }
          return result.slice(-200)
        })
      } catch {
        if (!cancelled) { setFailed(true); setNow(Date.now()) }
      } finally {
        if (deadline) clearTimeout(deadline)
        if (!cancelled) timer = setTimeout(poll, 5000)
      }
    }
    void poll()
    return () => { cancelled = true; controller?.abort(); if (timer) clearTimeout(timer); if (deadline) clearTimeout(deadline) }
  }, [active, taskId])

  const nodes = snapshot?.nodes ?? []
  const node = nodes.find((item) => item.node_id === selected) ?? nodes[0]
  const ageMs = node?.sampled_at_ns != null ? now - node.sampled_at_ns / 1_000_000 : null
  const stale = failed || node?.status === 'unavailable' || (ageMs !== null && ageMs > Math.max(15_000, (snapshot?.expected_interval_seconds ?? 1) * 3000))
  const queue = node?.gauges?.find((item) => item.key === 'waiting_requests')
  const series = queue ? JSON.stringify([queue.name, queue.labels]) : ''
  const trend = points.filter((point) => point.node === node?.node_id && point.series === series).slice(-60)
  const max = Math.max(1, ...trend.map((point) => point.value))
  const graph = trend.map((point, index) => `${index * 100 / Math.max(1, trend.length - 1)},${40 - point.value * 36 / max}`).join(' ')

  return <section className="space-y-2 rounded border border-[var(--border)] p-3" aria-label={t('tasks.live.title')}>
    <h3 className="font-semibold">{t('tasks.live.title')}</h3>
    <p className="text-xs text-[var(--text-muted)]">{t('tasks.live.source')}</p>
    {snapshot?.current_subrun && <p className="text-sm">{t('tasks.live.scenario')}: {snapshot.current_subrun} · {t('tasks.live.target')}: {snapshot.target_ratio == null ? '—' : `${snapshot.target_ratio * 100}%`} · {snapshot.load_mode === 'closed_loop' ? `${t('tasks.live.concurrency')}: ${snapshot.concurrency ?? '—'}` : `${t('tasks.live.rate')}: ${snapshot.rate ?? '—'}`} · {t('tasks.live.repetition')}: {snapshot.repetition ?? '—'}</p>}
    <p className="text-sm">{t('tasks.live.requestCounts')}: —</p>
    {failed && <p role="status" className="text-sm text-[var(--yellow)]">{t('tasks.live.stale')}</p>}
    {!node ? <p className="text-sm">{t('tasks.live.waiting')}</p> : <>
      <label className="block text-sm">{t('tasks.live.node')} <select value={node.node_id} onChange={(event) => setSelected(event.target.value)}>
        {nodes.map((item) => <option key={item.node_id} value={item.node_id}>{item.node_id} ({item.role}, {item.engine})</option>)}
      </select></label>
      <p className="text-sm">{stale ? t('tasks.live.stale') : node.sampled_at_ns == null ? t('tasks.live.waiting') : t('tasks.live.current')}
        {node.sampled_at_ns != null && ` · ${new Date(node.sampled_at_ns / 1_000_000).toLocaleString()}`}</p>
      {node.truncated && <p className="text-sm">{t('tasks.live.truncated')}</p>}
      {trend.length > 1 && <div><p className="text-xs">{t('tasks.live.queueTrend')}: {queue?.name} {JSON.stringify(queue?.labels)}</p>
        <svg role="img" aria-label={t('tasks.live.queueTrend')} viewBox="0 0 100 40" className="h-16 w-full" preserveAspectRatio="none"><polyline points={graph} fill="none" stroke="currentColor" strokeWidth="1" /></svg></div>}
      {node.gauges?.length ? <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th>{t('tasks.live.metric')}</th><th>{t('tasks.live.labels')}</th><th>{t('tasks.live.value')}</th></tr></thead><tbody>
        {node.gauges.map((gauge, index) => <tr key={`${gauge.name}-${JSON.stringify(gauge.labels)}-${index}`}><td>{gauge.name}</td><td>{JSON.stringify(gauge.labels)}</td><td>{gauge.value} {gauge.unit}</td></tr>)}
      </tbody></table></div> : <p className="text-sm">{t('tasks.live.noMetrics')}</p>}
    </>}
  </section>
}
