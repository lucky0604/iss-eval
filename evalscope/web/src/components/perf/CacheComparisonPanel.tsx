import { useState } from 'react'
import type { CacheScenarioResponse, CacheSubrunPlan } from '@/api/generated/contracts'
import { comparableMetric, comparableRuns, relativeChange, type ComparisonDimension } from '@/domain/perf/cacheComparison'
import { useLocale } from '@/contexts/LocaleContext'
import { formatMetric } from '@/domain/metric'

interface Props {
  baseline?: { plan: CacheSubrunPlan; scenario: CacheScenarioResponse }
  candidate?: { plan: CacheSubrunPlan; scenario: CacheScenarioResponse }
}

export default function CacheComparisonPanel({ baseline, candidate }: Props) {
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [dimension, setDimension] = useState<ComparisonDimension>('ratio')
  const a = baseline?.scenario.result
  const b = candidate?.scenario.result
  const valid = Boolean(baseline && candidate && a && b && a.quality !== 'invalid' && b.quality !== 'invalid' &&
    a.engine === b.engine && a.executor_version === b.executor_version && comparableRuns(baseline.plan, candidate.plan, dimension))
  return <section className="space-y-2 rounded border border-[var(--border)] p-3" aria-label={zh ? '显式子运行对比' : 'Explicit subrun comparison'}>
    <h2 className="font-semibold">{zh ? '显式子运行对比' : 'Explicit subrun comparison'}</h2>
    <p className="text-sm">{zh ? '在矩阵中分别选择基准和候选。固定其他维度及重复序号；不合并各轮 P99。' : 'Select baseline and candidate in the matrix. Other dimensions and repetition are fixed; per-run P99 values are not pooled.'}</p>
    <label>{zh ? '变化维度' : 'Changed dimension'} <select value={dimension} onChange={(event) => setDimension(event.target.value as ComparisonDimension)}>
      <option value="ratio">{zh ? '目标复用' : 'Target reuse'}</option><option value="load">{zh ? '负载' : 'Load'}</option>
    </select></label>
    <p className="text-sm">{zh ? '基准' : 'Baseline'}: {baseline?.plan.subrun_id ?? '—'} · {zh ? '候选' : 'Candidate'}: {candidate?.plan.subrun_id ?? '—'}</p>
    {!valid ? <p className="text-sm">{zh ? '请选择同一实验中仅变化所选维度、重复序号一致且结果有效的两组；未经校准的来源或范围不计算变化。' : 'Choose two valid runs from this experiment that differ only in the selected dimension and use the same repetition. Uncalibrated sources or scopes have no delta.'}</p> : <>
      <p className="text-sm">{zh ? '质量' : 'Quality'}: {a?.quality} / {b?.quality}; {zh ? '结果为单轮观测，不是聚合结论。' : 'These are individual repetitions, not an aggregate verdict.'}</p>
      <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr>
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
