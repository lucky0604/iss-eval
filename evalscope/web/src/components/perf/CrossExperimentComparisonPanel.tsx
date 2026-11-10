import { useEffect, useState } from 'react'
import { getCacheExperiment, getCacheScenario } from '@/api/perf'
import type { CacheExperimentResponse, CacheScenarioResponse, CacheSubrunPlan } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import CacheComparisonPanel from './CacheComparisonPanel'
import { PANEL_BUTTON, PANEL_INPUT, PANEL_SELECT } from './panelControls'

interface Props {
  experiment: CacheExperimentResponse
  baseline?: { plan: CacheSubrunPlan; scenario: CacheScenarioResponse }
  rootPath?: string
}

export default function CrossExperimentComparisonPanel({ experiment, baseline, rootPath }: Props) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [input, setInput] = useState('')
  const [target, setTarget] = useState('')
  return <section className="space-y-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-5 shadow-[var(--shadow-sm)]">
    <h2 className="type-label-xs">{zh ? '跨实验对比' : 'Cross-experiment comparison'}</h2>
    <p className="type-body-xs text-[var(--text-muted)]">{zh ? '先在上方矩阵选择基准，再输入另一实验的任务 ID 并明确选择候选。各实验使用独立命名空间，结果不是逐请求配对。' : 'Select a baseline above, then enter another experiment task ID and explicitly choose a candidate. Experiments use independent namespaces and are not paired per request.'}</p>
    <form onSubmit={(event) => { event.preventDefault(); setTarget(input.trim()) }}>
      <label>{zh ? '候选实验任务 ID' : 'Candidate experiment task ID'} <input className={PANEL_INPUT} value={input} onChange={(event) => setInput(event.target.value)} /></label>{' '}
      <button className={PANEL_BUTTON} type="submit" disabled={!input.trim()}>{zh ? '加载实验' : 'Load experiment'}</button>
    </form>
    {target && <CandidateExperiment key={target} target={target} experiment={experiment} baseline={baseline} rootPath={rootPath} />}
  </section>
}

function CandidateExperiment({ target, experiment, baseline, rootPath }: Props & { target: string }) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [other, setOther] = useState<CacheExperimentResponse | null>(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState('')
  const [scenario, setScenario] = useState<CacheScenarioResponse | null>(null)
  const [page, setPage] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    getCacheExperiment(target, controller.signal, rootPath).then(setOther).catch((err: unknown) => {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
    })
    return () => controller.abort()
  }, [target, rootPath])
  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    getCacheScenario(target, selected, controller.signal, rootPath).then(setScenario).catch((err: unknown) => {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
    })
    return () => controller.abort()
  }, [target, selected, rootPath])
  const plan = other?.plan.subruns.find((row) => row.subrun_id === selected)
  const candidate = plan && scenario?.subrun_id === selected ? { plan, scenario } : undefined
  const rows = other?.plan.subruns.slice(page, page + 100) ?? []
  return <>
    {error && <p role="alert">{error}</p>}
    {other && <>
      <label>{zh ? '候选子运行' : 'Candidate subrun'} <select className={PANEL_SELECT} value={rows.some((row) => row.subrun_id === selected) ? selected : ''} onChange={(event) => { setSelected(event.target.value); setScenario(null); setError('') }}>
        <option value="">{zh ? '请选择' : 'Select'}</option>
        {rows.map((row) => <option key={row.subrun_id} value={row.subrun_id} disabled={!other.state.subruns.some((state) => state.subrun_id === row.subrun_id && state.scenario)}>
          {row.subrun_id} · {row.target_ratio * 100}% · {row.load_point.concurrency ?? row.load_point.rate} · #{row.repetition + 1}
        </option>)}
      </select></label>{' '}
      <button className={PANEL_BUTTON} disabled={page === 0} onClick={() => setPage(Math.max(0, page - 100))}>{zh ? '上一页' : 'Previous'}</button>{' '}
      <button className={PANEL_BUTTON} disabled={page + 100 >= other.plan.subruns.length} onClick={() => setPage(page + 100)}>{zh ? '下一页' : 'Next'}</button>
      <span> {page + 1}–{Math.min(page + 100, other.plan.subruns.length)} / {other.plan.subruns.length}</span>
      <CacheComparisonPanel baseline={baseline} candidate={candidate} configurations={{ baseline: experiment.plan.configuration, candidate: other.plan.configuration }} />
    </>}
  </>
}
