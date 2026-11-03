import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listCacheExperiments } from '@/api/perf'
import type { CacheExperimentListResponse } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import Button from '@/components/ui/Button'

export default function CacheExperimentArchive({ scanToken }: { scanToken: number }) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<CacheExperimentListResponse | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let active = true
    Promise.resolve().then(() => listCacheExperiments(offset)).then((result) => {
      if (active) { setData(result); setFailed(false) }
    }).catch(() => { if (active) setFailed(true) })
    return () => { active = false }
  }, [offset, scanToken])
  if (!data?.total && !failed) return null
  return <section className="space-y-2 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
    <h2 className="font-semibold">{zh ? 'AIPerf 缓存实验（服务输出目录）' : 'AIPerf cache experiments (service outputs)'}</h2>
    {failed && <p role="alert">{zh ? '实验列表加载失败' : 'Failed to load experiments'}</p>}
    {data?.experiments.map((item) => <div key={item.task_id} className="flex flex-wrap gap-3 text-sm">
      <Link className="text-[var(--accent)]" to={`/perf-experiment/${encodeURIComponent(item.task_id)}`}>{item.task_id}</Link>
      <span>{item.status}</span><span>{item.quality ?? '—'}</span><span>{item.completed_subruns}/{item.total_subruns}</span>
    </div>)}
    {data && data.total > data.limit && <div className="flex gap-2">
      <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - data.limit))}>{zh ? '上一页' : 'Previous'}</Button>
      <Button variant="outline" size="sm" disabled={offset + data.limit >= data.total} onClick={() => setOffset(offset + data.limit)}>{zh ? '下一页' : 'Next'}</Button>
    </div>}
  </section>
}
