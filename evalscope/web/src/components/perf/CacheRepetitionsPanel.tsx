import { useEffect, useState } from 'react'
import { getCacheRepetitions } from '@/api/perf'
import type { CacheRepetitionItem, CacheSubrunPlan } from '@/api/generated/contracts'
import { comparableMetric } from '@/domain/perf/cacheComparison'
import { useLocale } from '@/contexts/LocaleContext'
import { PANEL_BUTTON, PANEL_SELECT } from './panelControls'

interface Props { taskId: string; planHash: string; rows: CacheSubrunPlan[] }
const groupKey = (row: CacheSubrunPlan) => JSON.stringify([row.target_ratio, row.load_mode, row.load_point.concurrency,
  row.load_point.rate, row.seed, row.request_count, row.output_token_limit])

export default function CacheRepetitionsPanel({ taskId, planHash, rows }: Props) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [group, setGroup] = useState('')
  const [requested, setRequested] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [metricKey, setMetricKey] = useState('time_to_first_token')
  const groups = [...new Map(rows.map((row) => [groupKey(row), row])).entries()]
  const active = groups.find(([key]) => key === group) ?? groups[0]
  const scanKey = active ? JSON.stringify([taskId, planHash, active[0], active[1].subrun_id, metricKey]) : ''
  return <section className="space-y-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-5 shadow-[var(--shadow-sm)]" aria-label={zh ? '重复运行离散度' : 'Repetition dispersion'}>
    <h2 className="type-label-xs">{zh ? '重复运行离散度' : 'Repetition dispersion'}</h2>
    <p className="type-body-xs text-[var(--text-muted)]">{zh ? '选择当前矩阵页中的组，逐页读取整个实验的重复运行；显示每轮样本数和轮次范围，不合并尾延迟。运行中读取的结果可能来自不同时刻。' : 'Choose a group from the current matrix page to load its repetitions across the entire experiment in pages. The range describes per-run values, not pooled tail latency. Running results may be sampled at different times.'}</p>
    {active && <label>{zh ? '目标比例 / 负载' : 'Target ratio / load'} <select className={PANEL_SELECT} value={active[0]} onChange={(event) => setGroup(event.target.value)}>{groups.map(([key, row]) => <option key={key} value={key}>{row.target_ratio * 100}% / {row.load_mode} / {row.load_point.concurrency ?? row.load_point.rate ?? '—'}</option>)}</select></label>}{' '}
    <label>{zh ? '指标' : 'Metric'} <select className={PANEL_SELECT} value={metricKey} onChange={(event) => setMetricKey(event.target.value)}><option value="time_to_first_token">TTFT</option><option value="request_throughput">{zh ? '请求吞吐' : 'Request throughput'}</option></select></label>
    {active && <button className={PANEL_BUTTON} onClick={() => { setRequested(scanKey); setAttempt((value) => value + 1) }}>{zh ? '读取全部重复轮次' : 'Load all repetitions'}</button>}
    {active && requested === scanKey && <RepetitionData key={`${scanKey}:${attempt}`} taskId={taskId} planHash={planHash} reference={active[1].subrun_id} metricKey={metricKey} />}
  </section>
}

function RepetitionData({ taskId, planHash, reference, metricKey }: { taskId: string; planHash: string; reference: string; metricKey: string }) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [state, setState] = useState<{ rows: CacheRepetitionItem[]; total?: number; done: boolean; error?: string }>({ rows: [], done: false })
  const [page, setPage] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      let offset = 0
      let total: number | undefined
      const rows: CacheRepetitionItem[] = []
      try {
        while (total == null || offset < total) {
          const result = await getCacheRepetitions(taskId, reference, metricKey, offset, controller.signal)
          if (controller.signal.aborted) return
          if (result.plan_hash !== planHash || result.offset !== offset || result.total < 0 || result.total > 10000 ||
              result.limit < 1 || result.limit > 100 || offset + result.repetitions.length > result.total ||
              (total != null && result.total !== total) || result.repetitions.length > result.limit ||
              (result.repetitions.length === 0 && offset < result.total)) throw new Error('Inconsistent repetition page')
          total = result.total
          rows.push(...result.repetitions)
          offset += result.repetitions.length
          setState({ rows: [...rows], total, done: offset === total })
        }
      } catch (err) {
        if (!controller.signal.aborted) setState({ rows: [...rows], total, done: false, error: err instanceof Error ? err.message : String(err) })
      }
    }
    void load()
    return () => controller.abort()
  }, [taskId, reference, metricKey, planHash])
  const referenceRow = state.rows.find((row) => row.quality !== 'invalid' && row.metric?.value != null &&
    row.metric.availability !== 'unavailable' && Number.isFinite(row.metric.value))
  const valid = state.rows.filter((row) => referenceRow && row.quality !== 'invalid' && row.engine === referenceRow.engine &&
    row.executor_version === referenceRow.executor_version && comparableMetric(referenceRow.metric ?? undefined, row.metric ?? undefined))
  const validIds = new Set(valid.map((row) => row.subrun_id))
  const values = valid.map((row) => row.metric!.value!)
  return <>
    <p className="text-sm">{state.done ? (zh ? '全部轮次已读取' : 'All repetitions loaded') : (zh ? '读取中或不完整' : 'Loading or incomplete')}: {state.rows.length} / {state.total ?? '—'}</p>
    {state.error && <p role="alert">{state.error}</p>}
    <p className="text-sm">{zh ? '已读取的可比轮数' : 'Comparable repetitions loaded'}: {values.length} / {state.rows.length} · {zh ? '已读取范围' : 'Loaded range'}: {values.length ? `${Math.min(...values)}–${Math.max(...values)} ${referenceRow?.metric?.unit}` : '—'}</p>
    <ul className="text-sm">{state.rows.slice(page * 50, page * 50 + 50).map((row) => <li key={row.subrun_id}>#{row.repetition + 1} · {row.subrun_id}: {row.metric?.value != null ? `${row.metric.value} ${row.metric.unit} (n=${row.metric.count ?? '—'}, ${row.metric.source}, ${row.metric.scope})` : '—'} · {row.quality ?? row.status}{!validIds.has(row.subrun_id) ? ` · ${zh ? '未纳入范围' : 'excluded from range'}` : ''}{row.unavailable_reason ? ` · ${row.unavailable_reason}` : ''}</li>)}</ul>
    {state.rows.length > 50 && <div className="flex gap-2 text-sm"><span>{page * 50 + 1}–{Math.min((page + 1) * 50, state.rows.length)} / {state.rows.length}</span><button className={PANEL_BUTTON} disabled={page === 0} onClick={() => setPage(page - 1)}>{zh ? '上一页' : 'Previous'}</button><button className={PANEL_BUTTON} disabled={(page + 1) * 50 >= state.rows.length} onClick={() => setPage(page + 1)}>{zh ? '下一页' : 'Next'}</button></div>}
  </>
}
