/**
 * Six matrix charts over the whole run set: TTFT, TPOT and end-to-end latency
 * (switchable statistic, P95 by default) plus input throughput, output
 * throughput and RPM. Every point is clickable and opens that run's requests.
 */
import { useMemo, useState } from 'react'
import { useLocale } from '@/contexts/LocaleContext'
import { useAsyncResource } from '@/hooks/useAsyncResource'
import Button from '@/components/ui/Button'
import ErrorAlert from '@/components/ui/ErrorAlert'
import Skeleton from '@/components/ui/Skeleton'
import { cn } from '@/lib/utils'
import { getPerfReportCharts, type ReportChartMetric, type ReportChartStatistic, type ReportLocation } from '@/api/perfReports'
import type {
  PerfReportChartsResponse,
  ReportChartPoint,
  ReportChartSeries,
  ReportRunDimensions,
} from '@/api/generated/contracts'
import { formatNumber } from '@/domain/perf/reportTable'

const LATENCY_METRICS: readonly ReportChartMetric[] = ['ttft', 'tpot', 'latency']
const RATE_METRICS: readonly ReportChartMetric[] = ['input_tps', 'output_tps', 'rpm']
const ALL_METRICS: readonly ReportChartMetric[] = [...LATENCY_METRICS, ...RATE_METRICS]
const STATISTICS: readonly ReportChartStatistic[] = ['mean', 'p50', 'p90', 'p95', 'p99']

const METRIC_LABEL: Record<ReportChartMetric, string> = {
  ttft: 'perf.report.charts.ttft',
  tpot: 'perf.report.charts.tpot',
  latency: 'perf.report.charts.e2e',
  input_tps: 'perf.report.charts.input_tps',
  output_tps: 'perf.report.charts.output_tps',
  rpm: 'perf.report.charts.rpm',
}

const SERIES_COLORS = ['#3b82f6', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#06b6d4', '#d946ef', '#84cc16']
const WIDTH = 480
const HEIGHT = 200
const PAD = { left: 48, right: 12, top: 12, bottom: 30 }

/** Display scale and unit per metric; latency is seconds in the snapshot. */
const METRIC_SCALE: Record<ReportChartMetric, { scale: number; unitKey: string; decimals: number }> = {
  ttft: { scale: 1000, unitKey: 'perf.report.unit.ms', decimals: 1 },
  tpot: { scale: 1000, unitKey: 'perf.report.unit.ms', decimals: 2 },
  latency: { scale: 1, unitKey: 'perf.report.unit.s', decimals: 3 },
  input_tps: { scale: 1, unitKey: 'perf.report.unit.tok_s', decimals: 1 },
  output_tps: { scale: 1, unitKey: 'perf.report.unit.tok_s', decimals: 1 },
  rpm: { scale: 1, unitKey: 'perf.report.unit.rpm', decimals: 1 },
}

type ChartsBundle = Record<ReportChartMetric, PerfReportChartsResponse>

function loadOf(point: ReportChartPoint): number | null {
  const dimensions = point.dimensions
  const value = dimensions.load_mode === 'open_loop' ? dimensions.rate : dimensions.concurrency
  return typeof value === 'number' ? value : null
}

/** A measured point: a finite value at a known load. */
function isMeasured(point: ReportChartPoint): boolean {
  return (
    point.availability !== 'unavailable' &&
    typeof point.value === 'number' &&
    Number.isFinite(point.value) &&
    loadOf(point) !== null
  )
}

/**
 * Every point of the series, measured or not: a missing measurement stays in
 * the list so the line breaks there instead of crossing the gap.
 */
function chartPoints(series: ReportChartSeries): ReportChartPoint[] {
  return (series.points ?? []).filter((point) => loadOf(point) !== null)
}

interface ChartEntry {
  key: string
  label: string
  isl: number | null
  color: string
  open: boolean
  /** Points in load order, including missing measurements as line breaks. */
  points: ReportChartPoint[]
}

function facetLabel(dimensions: ReportRunDimensions): string {
  return [
    dimensions.deployment_mode,
    dimensions.workload || '—',
    dimensions.message_structure,
    dimensions.input_tokens == null ? null : `ISL ${dimensions.input_tokens}`,
    dimensions.output_token_limit == null ? null : `OSL ${dimensions.output_token_limit}`,
    dimensions.target_ratio == null ? null : `${Math.round(dimensions.target_ratio * 100)}%`,
  ]
    .filter(Boolean)
    .join(' · ')
}

function seriesLabel(dimensions: ReportRunDimensions): string {
  return [
    dimensions.workload || '—',
    dimensions.input_tokens == null ? null : `ISL ${dimensions.input_tokens}`,
    dimensions.output_token_limit == null ? null : `OSL ${dimensions.output_token_limit}`,
    dimensions.target_ratio == null ? null : `${Math.round(dimensions.target_ratio * 100)}%`,
    dimensions.load_mode === 'open_loop' ? 'open-loop' : 'closed-loop',
  ]
    .filter(Boolean)
    .join(' · ')
}

function LineChart({
  entries,
  metric,
  onOpenRun,
}: {
  entries: ChartEntry[]
  metric: ReportChartMetric
  onOpenRun: (runKey: string) => void
}) {
  const { t } = useLocale()
  const display = METRIC_SCALE[metric]

  // Each series becomes one or more contiguous segments; an unmeasured point
  // ends its segment so a gap is never drawn as a measured straight line.
  const ordered = useMemo(
    () => entries
      .filter((entry) => entry.points.length > 0)
      .map((entry) => {
        const points = [...entry.points].sort((a, b) => (loadOf(a) ?? 0) - (loadOf(b) ?? 0))
        const segments: ReportChartPoint[][] = []
        let current: ReportChartPoint[] = []
        for (const point of points) {
          if (isMeasured(point)) current.push(point)
          else if (current.length > 0) {
            segments.push(current)
            current = []
          }
        }
        if (current.length > 0) segments.push(current)
        return { ...entry, points, segments }
      }),
    [entries],
  )

  const measured = ordered.flatMap((entry) => entry.points.filter(isMeasured))
  if (measured.length === 0) {
    return <p className="py-8 text-center type-body-xs text-[var(--text-muted)]">{t('perf.report.charts.empty')}</p>
  }

  const xValues = measured.map((point) => loadOf(point) ?? 0)
  const yValues = measured.map((point) => (point.value ?? 0) * display.scale)
  const xMin = Math.min(...xValues)
  const xMax = Math.max(...xValues)
  const yMin = Math.min(0, ...yValues)
  const yMax = Math.max(1e-9, ...yValues)
  const innerWidth = WIDTH - PAD.left - PAD.right
  const innerHeight = HEIGHT - PAD.top - PAD.bottom
  const x = (value: number) => PAD.left + (xMax === xMin ? innerWidth / 2 : (innerWidth * (value - xMin)) / (xMax - xMin))
  const y = (value: number) => PAD.top + innerHeight - (innerHeight * (value - yMin)) / (yMax - yMin)

  const yTicks = [yMin, (yMin + yMax) / 2, yMax]
  const xTickValues = [...new Set(xValues)]
  const modes = new Set(ordered.map((entry) => (entry.open ? 'req/s' : 'C')))
  const axis = [...modes].join(' / ')

  return (
    <div className="flex flex-col gap-1">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-auto w-full"
        role="img"
        aria-label={`${t(METRIC_LABEL[metric])} · ${t(display.unitKey)}`}
      >
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(tick)} y2={y(tick)} stroke="var(--border)" strokeWidth={1} />
            <text x={PAD.left - 6} y={y(tick) + 3} textAnchor="end" fontSize={9} fill="var(--text-muted)">
              {formatNumber(tick, display.decimals)}
            </text>
          </g>
        ))}
        {xTickValues.map((tick) => (
          <text key={tick} x={x(tick)} y={HEIGHT - PAD.bottom + 14} textAnchor="middle" fontSize={9} fill="var(--text-muted)">
            {formatNumber(tick, 0)}
          </text>
        ))}
        <text x={PAD.left + innerWidth / 2} y={HEIGHT - 2} textAnchor="middle" fontSize={9} fill="var(--text-muted)">
          {axis}
        </text>
        {ordered.map((entry) => (
          <g key={entry.key}>
            {entry.segments.map((segment, index) => (
              <polyline
                key={index}
                fill="none"
                stroke={entry.color}
                strokeWidth={1.6}
                strokeDasharray={entry.open ? '4 3' : undefined}
                points={segment
                  .map((point) => `${x(loadOf(point) ?? 0)},${y((point.value ?? 0) * display.scale)}`)
                  .join(' ')}
              />
            ))}
            {entry.points.filter(isMeasured).map((point) => {
              const value = (point.value ?? 0) * display.scale
              return (
                <g
                  key={point.run_key}
                  role="button"
                  tabIndex={0}
                  aria-label={t('perf.report.charts.openRun', { run: point.run_id })}
                  className="cursor-pointer"
                  onClick={() => onOpenRun(point.run_key)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onOpenRun(point.run_key)
                    }
                  }}
                >
                  <circle cx={x(loadOf(point) ?? 0)} cy={y(value)} r={3} fill={entry.color}>
                    <title>{t('perf.report.charts.point', { run: point.run_id, value: formatNumber(value, display.decimals) })}</title>
                  </circle>
                </g>
              )
            })}
          </g>
        ))}
      </svg>
      <ul className="flex max-h-20 flex-wrap gap-x-3 gap-y-1 overflow-y-auto type-body-xs text-[var(--text-muted)]">
        {ordered.map((entry) => (
          <li key={entry.key} className="flex items-center gap-1" title={entry.label}>
            <span className="inline-block h-0.5 w-3" style={{ backgroundColor: entry.color }} />
            <span className="truncate">{entry.label}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ChartCard({
  metric,
  response,
  statistic,
  facet,
  onOpenRun,
}: {
  metric: ReportChartMetric
  response: PerfReportChartsResponse | undefined
  statistic: ReportChartStatistic
  facet: string
  onOpenRun: (runKey: string) => void
}) {
  const { t } = useLocale()
  const display = METRIC_SCALE[metric]
  const entries = useMemo(() => {
    const source = response?.series ?? []
    const filtered = source.filter((item) => !facet || item.facet === facet)
    const isls = [...new Set(filtered.map((item) => item.points?.[0]?.dimensions.input_tokens ?? null))]
      .sort((a, b) => (a ?? -1) - (b ?? -1))
    // Repetitions are independent efforts: separate series, never one line.
    const repetitions = new Set(filtered.map((item) => item.repetition ?? -1))
    return filtered.map((item) => {
      const dimensions = item.points?.[0]?.dimensions
      const isl = dimensions?.input_tokens ?? null
      const repeat =
        repetitions.size > 1 && item.repetition != null
          ? ` · ${t('perf.report.charts.repeat', { n: item.repetition + 1 })}`
          : ''
      return {
        key: `${item.facet}|${item.load_mode ?? ''}|${item.repetition ?? ''}`,
        label: dimensions ? `${seriesLabel(dimensions)}${repeat}` : item.facet,
        isl,
        color: SERIES_COLORS[Math.max(0, isls.indexOf(isl)) % SERIES_COLORS.length],
        open: (item.load_mode ?? dimensions?.load_mode) === 'open_loop',
        points: chartPoints(item),
      }
    })
  }, [response, facet, t])
  return (
    <figure className="flex flex-col gap-1 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-3">
      <figcaption className="flex items-baseline justify-between gap-2">
        <span className="type-label-xs text-[var(--text)]">{t(METRIC_LABEL[metric])}</span>
        <span className="type-body-xs text-[var(--text-muted)]">
          {LATENCY_METRICS.includes(metric) ? `${t(`perf.report.col.${statistic}`)} · ` : ''}
          {t(display.unitKey)}
        </span>
      </figcaption>
      {response ? (
        <LineChart entries={entries} metric={metric} onOpenRun={onOpenRun} />
      ) : (
        <Skeleton lines={3} height={48} />
      )}
    </figure>
  )
}

export interface ReportChartsProps {
  location: ReportLocation
  available: boolean
  onOpenRun: (runKey: string) => void
}

export default function ReportCharts({ location, available, onOpenRun }: ReportChartsProps) {
  const { t } = useLocale()
  const [statistic, setStatistic] = useState<ReportChartStatistic>('p95')
  const [facet, setFacet] = useState('')

  const resource = useAsyncResource(
    async (signal) => {
      const responses = await Promise.all(
        ALL_METRICS.map((metric) =>
          getPerfReportCharts(
            location,
            { metric, statistic: RATE_METRICS.includes(metric) ? 'mean' : statistic },
            signal,
          ),
        ),
      )
      const bundle = {} as ChartsBundle
      ALL_METRICS.forEach((metric, index) => {
        bundle[metric] = responses[index]
      })
      return bundle
    },
    [location.taskId, location.rootPath, location.path, statistic, available],
    { enabled: available, fallbackMessage: t('common.loadError') },
  )

  // Facets are exactly the scenarios that may be compared: deployment,
  // workload, structure, ISL, OSL and prefix ratio, each with a readable label.
  const facets = useMemo(() => {
    const seen = new Map<string, string>()
    for (const metric of ALL_METRICS) {
      for (const series of resource.data?.[metric]?.series ?? []) {
        if (!seen.has(series.facet)) seen.set(series.facet, facetLabel(series.facet_dimensions))
      }
    }
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  }, [resource.data])

  if (!available) {
    return (
      <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
        <h2 className="type-label-xs text-[var(--text)]">{t('perf.report.charts.title')}</h2>
        <p className="mt-1 type-body-xs text-[var(--text-muted)]">{t('perf.report.requests.unavailable')}</p>
      </section>
    )
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="type-label-xs text-[var(--text)]">{t('perf.report.charts.title')}</h2>
        <div className="flex flex-wrap items-center gap-2">
          <span className="type-body-xs text-[var(--text-muted)]">{t('perf.report.charts.statistic')}</span>
          {STATISTICS.map((item) => (
            <Button
              key={item}
              size="sm"
              variant={statistic === item ? 'primary' : 'ghost'}
              onClick={() => setStatistic(item)}
            >
              {t(`perf.report.col.${item === 'mean' ? 'mean' : item}`)}
            </Button>
          ))}
          {facets.length > 1 && (
            <label className="ml-2 type-body-xs text-[var(--text-muted)]">
              {t('perf.report.charts.facet')}{' '}
              <select
                className="rounded-[var(--radius-sm)] border border-[var(--border-md)] bg-[var(--bg-card)] px-2 py-1 text-xs text-[var(--text)]"
                value={facet}
                onChange={(event) => setFacet(event.target.value)}
              >
                <option value="">{t('perf.report.charts.allFacets')}</option>
                {facets.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>

      {resource.error && <ErrorAlert>{resource.error}</ErrorAlert>}

      <div className={cn('grid gap-3 sm:grid-cols-1 lg:grid-cols-3')}>
        {ALL_METRICS.map((metric) => (
          <ChartCard
            key={metric}
            metric={metric}
            response={resource.data?.[metric]}
            statistic={statistic}
            facet={facet}
            onOpenRun={onOpenRun}
          />
        ))}
      </div>
      <p className="type-body-xs text-[var(--text-muted)]">{t('perf.report.sortHint')}</p>
    </section>
  )
}
