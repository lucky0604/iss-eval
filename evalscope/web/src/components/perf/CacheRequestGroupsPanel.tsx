import { useEffect, useState } from 'react'
import type { CacheObservation, CacheRequestGroupsResponse } from '@/api/generated/contracts'
import { getCacheRequestGroups } from '@/api/perf'
import { useLocale } from '@/contexts/LocaleContext'

interface Props { taskId: string; subrunId: string; cache?: CacheObservation | null }
export default function CacheRequestGroupsPanel({ taskId, subrunId, cache }: Props) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [groups, setGroups] = useState<CacheRequestGroupsResponse | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (cache?.availability !== 'available') return
    const controller = new AbortController()
    let active = true
    getCacheRequestGroups(taskId, subrunId, controller.signal).then((value) => { if (active) setGroups(value) })
      .catch(() => { if (active) setFailed(true) })
    return () => { active = false; controller.abort() }
  }, [taskId, subrunId, cache?.availability])
  return <section className="space-y-2 rounded border border-[var(--border)] p-3" aria-label={zh ? '请求级缓存分组' : 'Request-level cache groups'}>
    <h3 className="font-semibold">{zh ? '请求级缓存分组' : 'Request-level cache groups'}</h3>
    <p className="text-sm">{zh ? '仅使用服务端返回的 cached token；百分位为各组原始请求的精确最近秩，不跨轮合并。' : 'Only server-reported cached tokens are grouped. Percentiles are exact nearest-rank request values, not pooled across repetitions.'}</p>
    {groups?.availability === 'available' ? <><p className="text-sm">{zh ? '覆盖' : 'Coverage'}: {groups.observed_requests} / {groups.successful_requests ?? '—'}</p>
      <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th>{zh ? '分组 / 请求数' : 'Group / requests'}</th><th>{zh ? '指标 (秒) / 样本' : 'Metric (seconds) / samples'}</th><th>Mean</th><th>P50</th><th>P95</th><th>P99</th></tr></thead><tbody>
        {groups.groups?.flatMap((group) => ([['Latency', group.latency], ['TTFT', group.ttft]] as const).map(([label, metric]) => <tr key={`${group.group}-${label}`}>
          <td>{group.group} / {group.count}</td><td>{label} / {metric.count}</td><td>{metric.mean ?? '—'}</td><td>{metric.p50 ?? '—'}</td><td>{metric.p95 ?? '—'}</td><td>{metric.p99 ?? '—'}</td>
        </tr>))}
      </tbody></table></div></> : <p>{failed ? (zh ? '分组读取失败。' : 'Unable to load groups.') : groups?.reason ?? cache?.reason ?? (zh ? '尚无可用请求级缓存数据。' : 'No request-level cache data available yet.')}</p>}
  </section>
}
