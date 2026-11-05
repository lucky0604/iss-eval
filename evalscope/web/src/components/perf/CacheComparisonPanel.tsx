import { useState } from 'react'
import type { CachePlanConfiguration, CacheScenarioResponse, CacheSubrunPlan } from '@/api/generated/contracts'
import { comparableConfigurations, comparableMetric, comparableRuns, relativeChange, type ComparisonDimension } from '@/domain/perf/cacheComparison'
import { useLocale } from '@/contexts/LocaleContext'
import { formatMetric } from '@/domain/metric'

interface Props {
  baseline?: { plan: CacheSubrunPlan; scenario: CacheScenarioResponse }
  candidate?: { plan: CacheSubrunPlan; scenario: CacheScenarioResponse }
  configurations?: { baseline?: CachePlanConfiguration | null; candidate?: CachePlanConfiguration | null }
}

export default function CacheComparisonPanel({ baseline, candidate, configurations }: Props) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [dimension, setDimension] = useState<ComparisonDimension>('ratio')
  const a = baseline?.scenario.result
  const b = candidate?.scenario.result
  const valid = Boolean(baseline && candidate && a && b && a.quality !== 'invalid' && b.quality !== 'invalid' &&
    a.engine === b.engine && a.executor_version === b.executor_version &&
    (!configurations || comparableConfigurations(configurations.baseline, configurations.candidate)) &&
    comparableRuns(baseline.plan, candidate.plan, dimension, !configurations))
  return <section className="space-y-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-5 shadow-[var(--shadow-sm)]" aria-label={zh ? '显式子运行对比' : 'Explicit subrun comparison'}>
    <h2 className="type-label-xs">{zh ? '显式子运行对比' : 'Explicit subrun comparison'}</h2>
    <p className="type-body-xs text-[var(--text-muted)]">{zh ? '在矩阵中分别选择基准和候选。固定其他维度及重复序号；不合并各轮 P99。' : 'Select baseline and candidate in the matrix. Other dimensions and repetition are fixed; per-run P99 values are not pooled.'}</p>
    <label>{zh ? '变化维度' : 'Changed dimension'} <select value={dimension} onChange={(event) => setDimension(event.target.value as ComparisonDimension)}>
      <option value="ratio">{zh ? '目标复用' : 'Target reuse'}</option><option value="load">{zh ? '负载' : 'Load'}</option>
    </select></label>
    <p className="text-sm">{zh ? '基准' : 'Baseline'}: {baseline?.plan.subrun_id ?? '—'} · {zh ? '候选' : 'Candidate'}: {candidate?.plan.subrun_id ?? '—'}</p>
    {!valid ? <p className="text-sm">{zh ? '请选择仅变化所选维度、重复序号一致且结果有效的两组；跨实验还需匹配已核验的配置。未经校准的来源或范围不计算变化。' : 'Choose two valid runs that differ only in the selected dimension and use the same repetition. Cross-experiment runs also require matching verified configurations; uncalibrated sources or scopes have no delta.'}</p> : <>
      <p className="text-sm">{zh ? '质量' : 'Quality'}: {a?.quality} / {b?.quality}; {zh ? '结果为单轮观测，不是聚合结论。' : 'These are individual repetitions, not an aggregate verdict.'}</p>
      <div className="overflow-x-auto"><table className="w-full min-w-max border-collapse whitespace-nowrap text-left text-xs [&_th]:border-b [&_th]:border-[var(--border)] [&_th]:px-3 [&_th]:py-2 [&_td]:border-b [&_td]:border-[var(--border)] [&_td]:px-3 [&_td]:py-2"><thead><tr>
        <th>{zh ? '指标 / 来源 / 范围' : 'Metric / source / scope'}</th><th>{zh ? '基准 (n)' : 'Baseline (n)'}</th><th>{zh ? '候选 (n)' : 'Candidate (n)'}</th><th>{zh ? '变化' : 'Change'}</th>
      </tr></thead><tbody>{a?.metrics?.map((left) => {
        const matches = b?.metrics?.filter((right) => right.key === left.key) ?? []
        const right = matches.length === 1 ? matches[0] : undefined
        const compatible = a.metrics?.filter((metric) => metric.key === left.key).length === 1 && comparableMetric(left, right)
        const delta = compatible ? relativeChange(left.value!, right!.value!) : null
        return <tr key={`${left.source}-${left.scope}-${left.key}`}><td>{left.key} / {left.source} / {left.scope}</td>
          <td>{left.value ?? '—'} {left.unit} ({left.count ?? '—'})</td><td>{right?.value ?? '—'} {right?.unit ?? ''} ({right?.count ?? '—'})</td>
          <td>{delta == null ? '—' : `${formatMetric(delta * 100, null).primary}%`}</td></tr>
      })}</tbody></table></div>
    </>}
  </section>
}
