import { useState } from 'react'
import type { NodeMetricsAnalysis } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import Button from '@/components/ui/Button'
import NodeSeriesChart, { type NodeSeriesSource } from './NodeSeriesChart'

type Props = { analysis?: NodeMetricsAnalysis | null } & ({ taskId: string; subrunId: string; archive?: never } | { archive: { rootPath: string; path: string }; taskId?: never; subrunId?: never })
const display = (value: number | null | undefined) => value == null ? '—' : String(value)

export default function NodeWindowPanel({ analysis, taskId, subrunId, archive }: Props) {
  const source: NodeSeriesSource = archive ? { kind: 'archive', ...archive } : { kind: 'experiment', taskId: taskId!, subrunId: subrunId! }
  const { locale } = useLocale()
  const zh = locale === 'zh'
  const [selected, setSelected] = useState('')
  const [seriesIndex, setSeriesIndex] = useState('0')
  const [page, setPage] = useState(0)
  const nodes = [...new Set([...(analysis?.series ?? []).map((row) => row.node_id),
    ...(analysis?.cache_ratios ?? []).map((row) => row.node_id), ...(analysis?.scrapes ?? []).map((row) => row.node_id)])]
  const node = nodes.includes(selected) ? selected : nodes[0]
  const rows = (analysis?.series ?? []).filter((row) => row.node_id === node)
  const ratios = (analysis?.cache_ratios ?? []).filter((row) => row.node_id === node)
  const scrape = analysis?.scrapes?.find((row) => row.node_id === node)
  const allGauges = rows.filter((row) => row.metric.kind === 'gauge')
  const gauges = allGauges.slice(0, 200)
  const selectedGauge = gauges[Number(seriesIndex)] ?? gauges[0]
  return <section className="space-y-2 rounded border border-[var(--border)] p-3" aria-label={zh ? '节点窗口指标' : 'Node-window metrics'}>
    <h3 className="font-semibold">{zh ? '节点窗口指标' : 'Node-window metrics'}</h3>
    <p className="text-sm">{zh ? '范围是节点共享流量，不是逐请求 P/D 耗时；部署版本、标签和路由覆盖待核验。' : 'Node windows include shared traffic; they are not per-request P/D timings. Verify deployment version, labels and routing coverage.'}</p>
    <p className="text-sm">{zh ? '状态 / 窗口来源' : 'Availability / window source'}: {analysis?.availability ?? '—'} / {analysis?.window_source ?? '—'}
      {analysis?.window && ` · ${analysis.window.start_ns}–${analysis.window.end_ns} ns`}</p>
    {analysis?.reason && <p className="text-sm">{analysis.reason}</p>}
    {analysis?.quality_reasons?.length ? <p className="text-sm">{analysis.quality_reasons.join(', ')}</p> : null}
    {!node ? <p>{zh ? '没有可用的节点窗口。' : 'No node windows available.'}</p> : <>
      <label className="text-sm">{zh ? '节点' : 'Node'} <select value={node} onChange={(event) => { setSelected(event.target.value); setSeriesIndex('0'); setPage(0) }}>
        {nodes.map((id) => <option key={id} value={id}>{id}</option>)}
      </select></label>
      {scrape && <p className="text-sm">{zh ? '成功抓取 / 失败抓取' : 'Successful / failed scrapes'}: {scrape.observed_successful_scrapes} / {display(scrape.failed_scrapes)}</p>}
      {ratios.map((ratio, index) => <p key={`${ratio.node_id}-${index}`} className="text-sm">
        {zh ? 'vLLM 节点查询 token 命中率' : 'vLLM node lookup-token hit ratio'}: {display(ratio.ratio)}
        {' '}({display(ratio.hit_tokens)} / {display(ratio.lookup_tokens)}, {ratio.availability}, {ratio.reason ?? '—'}, {JSON.stringify(ratio.labels)})
      </p>)}
      {rows.length ? <div className="overflow-x-auto"><table className="w-full min-w-max whitespace-nowrap text-left text-xs [&_th]:px-2 [&_td]:px-2"><thead><tr>
        <th>{zh ? '节点 / 指标' : 'Role / metric'}</th><th>{zh ? '标签' : 'Labels'}</th><th>{zh ? '窗口值' : 'Window value'}</th>
        <th>{zh ? '样本峰值' : 'Sample peak'}</th><th>{zh ? '样本 / 覆盖' : 'Count / coverage'}</th><th>{zh ? '状态 / 边界 / 原因' : 'Status / boundary / reason'}</th>
      </tr></thead><tbody>{rows.slice(page * 50, page * 50 + 50).map((row, index) => {
        const stat = row.statistic
        const hist = row.histogram
        return <tr key={`${row.metric.name}-${JSON.stringify(row.labels)}-${index}`}>
          <td>{row.role} / {row.metric.key}<br />{row.metric.name} ({row.engine} {row.metric.upstream_version})</td>
          <td>{JSON.stringify(row.labels)}</td>
          <td>{hist ? `${display(hist.mean)} ${hist.unit} (mean), P50 ${display(hist.p50)}, P95 ${display(hist.p95)}, P99 ${display(hist.p99)} ≈` : `${display(stat?.value)} ${row.metric.unit}`}</td>
          <td>{display(stat?.sample_peak)}</td><td>{hist ? display(hist.count) : `${display(stat?.sample_count)} / ${display(stat?.coverage)}`}</td>
          <td>{hist ? `${hist.availability} / ${hist.boundary ?? 'unknown'} / ${hist.reason ?? '—'}` : `${stat?.availability ?? 'missing'} / ${stat?.boundary ?? 'unknown'} / ${stat?.reason ?? row.reason ?? '—'}`}</td>
        </tr>
      })}</tbody></table></div> : <p>{zh ? '所选节点没有可用序列。' : 'No series for this node.'}</p>}
      {rows.length > 50 && <div className="flex gap-2 text-sm"><span>{page * 50 + 1}–{Math.min(page * 50 + 50, rows.length)} / {rows.length}</span>
        <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>{zh ? '上一页' : 'Previous'}</Button>
        <Button variant="outline" size="sm" disabled={(page + 1) * 50 >= rows.length} onClick={() => setPage(page + 1)}>{zh ? '下一页' : 'Next'}</Button></div>}
      {selectedGauge && <><label className="text-sm">{zh ? '时间序列' : 'Time series'} <select value={seriesIndex} onChange={(event) => setSeriesIndex(event.target.value)}>
        {gauges.map((row, index) => <option key={`${row.metric.name}-${JSON.stringify(row.labels)}`} value={index}>{row.metric.key} {JSON.stringify(row.labels)}</option>)}
      </select></label><NodeSeriesChart key={`${node}-${seriesIndex}`} source={source} series={selectedGauge}
        maxGap={analysis!.max_gap_ns} start={analysis?.window?.start_ns} end={analysis?.window?.end_ns} /></>}
      {allGauges.length > gauges.length && <p className="text-xs">{zh ? '时间序列选择器显示前 200 项；完整指标见分页表格。' : 'The chart selector shows the first 200 series; all metrics remain in the paged table.'}</p>}
    </>}
  </section>
}
