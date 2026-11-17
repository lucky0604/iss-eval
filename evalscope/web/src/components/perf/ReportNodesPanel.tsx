/**
 * Node telemetry already persisted by a completed run, surfaced in the unified
 * report: one numeric row per run/node/metric, never a merged or averaged
 * series. A missing node observation is stated with its reason, not filled in.
 */
import { useLocale } from '@/contexts/LocaleContext'
import type { ReportNodeCacheRatio, ReportNodeMetric, ReportRun, ReportRunNodes } from '@/api/generated/contracts'
import { formatNumber } from '@/domain/perf/reportTable'

const CELL = 'px-2.5 py-1.5 whitespace-nowrap border-b border-[var(--border)]'
const HEAD = 'px-2.5 py-1.5 text-left font-medium text-[var(--text-muted)] border-b border-[var(--border)]'

export interface ReportNodesPanelProps {
  runs: ReportRun[]
  available: boolean
}

function labelsOf(metric: ReportNodeMetric): string {
  return Object.entries(metric.labels ?? {})
    .map(([key, value]) => `${key}=${value}`)
    .join(', ')
}

function windowLine(nodes: ReportRunNodes, t: (key: string, vars?: Record<string, string | number>) => string) {
  const source = nodes.window_source ?? '—'
  return t('perf.report.nodes.window', {
    source,
    timed: nodes.timed_requests ?? 0,
    total: nodes.total_requests ?? 0,
    gap: nodes.max_gap_ns == null ? '—' : formatNumber(nodes.max_gap_ns / 1e9, 2),
  })
}

export default function ReportNodesPanel({ runs, available }: ReportNodesPanelProps) {
  const { t } = useLocale()

  if (!available) {
    return (
      <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
        <h2 className="type-label-xs text-[var(--text)]">{t('perf.report.nodes.title')}</h2>
        <p className="mt-1 type-body-xs text-[var(--text-muted)]">{t('perf.report.nodes.unavailable')}</p>
      </section>
    )
  }

  const observed = runs.filter((run): run is ReportRun & { nodes: ReportRunNodes } => Boolean(run.nodes))
  if (observed.length === 0) {
    return (
      <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
        <h2 className="type-label-xs text-[var(--text)]">{t('perf.report.nodes.title')}</h2>
        <p className="mt-1 type-body-xs text-[var(--text-muted)]">{t('perf.report.nodes.empty')}</p>
      </section>
    )
  }

  const cacheRows = observed.flatMap((run) =>
    (run.nodes.cache_ratios ?? []).map((ratio) => ({ run, ratio })),
  )

  return (
    <section className="flex flex-col gap-4 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
      <div className="flex flex-col gap-1">
        <h2 className="type-label-xs text-[var(--text)]">{t('perf.report.nodes.title')}</h2>
        <p className="type-body-xs text-[var(--text-muted)]">{t('perf.report.nodes.subtitle')}</p>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full text-xs tabular-nums">
          <thead>
            <tr>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.run')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.node')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.role')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.metric')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.labels')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.mean')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.peak')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.samples')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.coverage')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.p50')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.p95')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.p99')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.unit')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.boundary')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.status')}</th>
              <th scope="col" className={HEAD}>{t('perf.report.nodes.reason')}</th>
            </tr>
          </thead>
          <tbody>
            {observed.map((run) => (
              <tr key={run.run_key} className="align-top">
                <td className={CELL} colSpan={16}>
                  <span className="font-mono">{run.run_id}</span>
                  <span className="ml-2 text-[var(--text-muted)]">{windowLine(run.nodes, t)}</span>
                  {(run.nodes.quality_reasons ?? []).length > 0 && (
                    <span className="ml-2 text-[var(--warning-color)]">
                      {(run.nodes.quality_reasons ?? []).join('; ')}
                    </span>
                  )}
                  {run.nodes.reason && run.nodes.availability !== 'available' && (
                    <span className="ml-2 text-[var(--text-muted)]">{run.nodes.reason}</span>
                  )}
                </td>
              </tr>
            ))}
            {observed.flatMap((run) =>
              (run.nodes.metrics ?? []).map((metric) => (
                <tr key={`${run.run_key}|${metric.node_id}|${metric.metric_name}|${labelsOf(metric)}`}>
                  <td className={CELL}>{run.run_id}</td>
                  <td className={CELL}>{metric.node_id}</td>
                  <td className={CELL}>{metric.role}</td>
                  <td className={CELL}>{metric.metric_name}</td>
                  <td className={CELL}>{labelsOf(metric) || '—'}</td>
                  <td className={CELL}>
                    {metric.statistic?.value == null ? '—' : formatNumber(metric.statistic.value, 4)}
                    {metric.histogram?.mean == null ? '' : formatNumber(metric.histogram.mean, 4)}
                  </td>
                  <td className={CELL}>
                    {metric.statistic?.sample_peak == null ? '—' : formatNumber(metric.statistic.sample_peak, 4)}
                  </td>
                  <td className={CELL}>
                    {metric.statistic?.sample_count == null
                      ? (metric.histogram?.count ?? '—')
                      : metric.statistic.sample_count}
                  </td>
                  <td className={CELL}>
                    {metric.statistic?.coverage == null ? '—' : formatNumber(metric.statistic.coverage, 4)}
                  </td>
                  <td className={CELL}>
                    {metric.histogram?.p50 == null ? '—' : formatNumber(metric.histogram.p50, 4)}
                  </td>
                  <td className={CELL}>
                    {metric.histogram?.p95 == null ? '—' : formatNumber(metric.histogram.p95, 4)}
                  </td>
                  <td className={CELL}>
                    {metric.histogram?.p99 == null ? '—' : formatNumber(metric.histogram.p99, 4)}
                  </td>
                  <td className={CELL}>{metric.metric_unit ?? metric.histogram?.unit ?? '—'}</td>
                  <td className={CELL}>
                    {metric.statistic?.boundary ?? metric.histogram?.boundary ?? '—'}
                  </td>
                  <td className={CELL}>
                    {metric.statistic?.availability ?? metric.histogram?.availability ?? '—'}
                  </td>
                  <td className={CELL}>{metric.reason ?? metric.statistic?.reason ?? metric.histogram?.reason ?? '—'}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>

      {cacheRows.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="type-label-xs text-[var(--text)]">{t('perf.report.nodes.cacheTitle')}</h3>
          <p className="type-body-xs text-[var(--text-muted)]">{t('perf.report.nodes.cacheHint')}</p>
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs tabular-nums">
              <thead>
                <tr>
                  <th scope="col" className={HEAD}>{t('perf.report.nodes.run')}</th>
                  <th scope="col" className={HEAD}>{t('perf.report.nodes.node')}</th>
                  <th scope="col" className={HEAD}>{t('perf.report.nodes.role')}</th>
                  <th scope="col" className={HEAD}>{t('perf.report.nodes.cacheHit')}</th>
                  <th scope="col" className={HEAD}>{t('perf.report.nodes.cacheLookup')}</th>
                  <th scope="col" className={HEAD}>{t('perf.report.nodes.cacheRatio')}</th>
                  <th scope="col" className={HEAD}>{t('perf.report.nodes.cacheSemantics')}</th>
                  <th scope="col" className={HEAD}>{t('perf.report.nodes.status')}</th>
                  <th scope="col" className={HEAD}>{t('perf.report.nodes.reason')}</th>
                </tr>
              </thead>
              <tbody>
                {cacheRows.map(({ run, ratio }: { run: ReportRun; ratio: ReportNodeCacheRatio }) => (
                  <tr key={`${run.run_key}|cache|${ratio.node_id}`}>
                    <td className={CELL}>{run.run_id}</td>
                    <td className={CELL}>{ratio.node_id}</td>
                    <td className={CELL}>{ratio.role}</td>
                    <td className={CELL}>{ratio.hit_tokens == null ? '—' : formatNumber(ratio.hit_tokens, 0)}</td>
                    <td className={CELL}>{ratio.lookup_tokens == null ? '—' : formatNumber(ratio.lookup_tokens, 0)}</td>
                    <td className={CELL}>
                      {ratio.ratio == null ? '—' : formatNumber(ratio.ratio * 100, 2)}
                    </td>
                    <td className={CELL}>{ratio.semantics ?? '—'}</td>
                    <td className={CELL}>{ratio.availability}</td>
                    <td className={CELL}>{ratio.reason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}
