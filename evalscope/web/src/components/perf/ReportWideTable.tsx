/**
 * The numeric wide table: one row per run, one number per cell.
 *
 * Cells never render a statistics object. The two-level header carries the
 * unit, the row keeps the tier identity, and a run detail row explains the
 * sample size, population and source behind each percentile.
 */
import { Fragment, useMemo, useState } from 'react'
import { useLocale } from '@/contexts/LocaleContext'
import { ChevronDown, ChevronUp } from 'lucide-react'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Pagination from '@/components/ui/Pagination'
import { cn } from '@/lib/utils'
import type { ReportRun } from '@/api/generated/contracts'
import {
  REPORT_COLUMN_GROUPS,
  REPORT_PRESETS,
  columnsForPreset,
  contiguousGroups,
  formatColumnValue,
  formatNumber,
  loadModeOf,
  loadSortKey,
  loadValue,
  nextSort,
  type ReportColumn,
  type ReportColumnGroup,
  type ReportPreset,
  type ReportRunSort,
} from '@/domain/perf/reportTable'

const ROW_HEIGHT = 'h-10'
const CELL = 'px-2.5 py-1.5 whitespace-nowrap border-b border-[var(--border)]'
const STICKY_ONE = 'sticky left-0 z-10 bg-[var(--bg-card)]'
const STICKY_TWO = 'sticky left-[112px] z-10 bg-[var(--bg-card)]'

export interface ReportWideTableProps {
  runs: ReportRun[]
  total: number
  offset: number
  limit: number
  sort: ReportRunSort
  order: 'asc' | 'desc'
  preset: ReportPreset
  loading?: boolean
  onPreset: (preset: ReportPreset) => void
  onSort: (sort: ReportRunSort, order: 'asc' | 'desc') => void
  onPage: (offset: number) => void
  onOpenRequests?: (run: ReportRun) => void
  canOpenRequests?: (run: ReportRun) => boolean
}

function groupModeLabel(run: ReportRun, t: (key: string) => string): string {
  return t(loadModeOf(run) === 'open' ? 'perf.report.tier.modeOpen' : 'perf.report.tier.modeClosed')
}

function groupHeaderLabel(run: ReportRun, t: (key: string, vars?: Record<string, string | number>) => string): string {
  const dimensions = run.dimensions
  return t('perf.report.groupLabel', {
    workload: dimensions.workload || t('perf.report.tier.unknown'),
    ratio: dimensions.target_ratio === null || dimensions.target_ratio === undefined
      ? '—'
      : Math.round(dimensions.target_ratio * 100),
    osl: dimensions.output_token_limit ?? '—',
    mode: groupModeLabel(run, t),
  })
}

function tierCaption(run: ReportRun, t: (key: string, vars?: Record<string, string | number>) => string): string {
  const dimensions = run.dimensions
  return [
    t('perf.report.tier.osl', { value: dimensions.output_token_limit ?? '—' }),
    t('perf.report.tier.ratio', {
      value: dimensions.target_ratio === null || dimensions.target_ratio === undefined
        ? '—'
        : Math.round(dimensions.target_ratio * 100),
    }),
    groupModeLabel(run, t),
  ]
    .filter(Boolean)
    .join(' · ')
}

function loadCaption(run: ReportRun, t: (key: string, vars?: Record<string, string | number>) => string): string {
  return loadModeOf(run) === 'open'
    ? t('perf.report.tier.loadOpen', { value: loadValue(run) ?? '—' })
    : t('perf.report.tier.loadClosed', { value: loadValue(run) ?? '—' })
}

function sortIndicator(active: boolean, order: 'asc' | 'desc') {
  if (!active) return null
  return order === 'asc' ? <ChevronUp size={12} aria-hidden /> : <ChevronDown size={12} aria-hidden />
}

function RunDetailRow({ run, columns, canOpen, onOpenRequests }: {
  run: ReportRun
  columns: ReportColumn[]
  canOpen: boolean
  onOpenRequests?: (run: ReportRun) => void
}) {
  const { t } = useLocale()
  const ttft = run.ttft
  const tpot = run.tpot
  const e2e = run.e2e
  const fields: Array<{ label: string; value: string }> = [
    { label: t('perf.report.rowDetail.samples'), value: `${ttft.count} / ${tpot.count} / ${e2e.count}` },
    { label: t('perf.report.rowDetail.population'), value: ttft.population },
    { label: t('perf.report.rowDetail.source'), value: ttft.source },
    { label: t('perf.report.rowDetail.method'), value: ttft.percentile_method },
    { label: t('perf.report.rowDetail.quality'), value: run.quality ?? '—' },
  ]
  if (run.observation.seconds !== null && run.observation.seconds !== undefined) {
    fields.push({ label: t('perf.report.col.window'), value: `${formatNumber(run.observation.seconds, 2)} s` })
  }
  return (
    <tr className="bg-[var(--bg-card2)]">
      <td colSpan={columns.length} className="px-4 py-3 border-b border-[var(--border)]">
        <div className="flex flex-wrap items-start gap-x-6 gap-y-2">
          <div className="min-w-[180px]">
            <p className="type-label-xs text-[var(--text-muted)]">{t('perf.report.rowDetail.title')}</p>
            <p className="type-body-sm text-[var(--text)]">
              {run.run_id}
              {run.availability === 'unavailable' && ` · ${run.reason ?? t('perf.report.rowDetail.unavailable')}`}
            </p>
          </div>
          <dl className="flex flex-wrap gap-x-5 gap-y-1 type-body-xs">
            {fields.map((field) => (
              <div key={field.label} className="flex items-center gap-1.5">
                <dt className="text-[var(--text-muted)]">{field.label}</dt>
                <dd className="tabular-nums text-[var(--text)]">{field.value}</dd>
              </div>
            ))}
          </dl>
          {run.quality_reasons && run.quality_reasons.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="type-label-xs text-[var(--text-muted)]">{t('perf.report.rowDetail.reasons')}</span>
              {run.quality_reasons.map((reason) => <Badge key={reason} variant="warning">{reason}</Badge>)}
            </div>
          )}
          {canOpen && onOpenRequests && (
            <Button size="sm" variant="outline" onClick={() => onOpenRequests(run)}>
              {t('perf.report.rowDetail.requests')}
            </Button>
          )}
        </div>
      </td>
    </tr>
  )
}

export default function ReportWideTable({
  runs,
  total,
  offset,
  limit,
  sort,
  order,
  preset,
  loading = false,
  onPreset,
  onSort,
  onPage,
  onOpenRequests,
  canOpenRequests,
}: ReportWideTableProps) {
  const { t } = useLocale()
  const [expanded, setExpanded] = useState<string | null>(null)
  const columns = useMemo(() => columnsForPreset(preset, runs), [preset, runs])

  const grouped = useMemo(() => {
    const groups: Array<{ key: ReportColumnGroup; columns: ReportColumn[] }> = []
    for (const group of REPORT_COLUMN_GROUPS) {
      const members = columns.filter((column) => column.group === group.key)
      if (members.length > 0) groups.push({ key: group.key, columns: members })
    }
    return groups
  }, [columns])

  const groupedRows = useMemo(() => (sort === 'order' ? contiguousGroups(runs) : []), [runs, sort])
  const totalPages = Math.max(1, Math.ceil(total / limit))
  const page = Math.floor(offset / limit) + 1

  const headerFor = (column: ReportColumn) => {
    const sortKey = column.key === 'load' ? loadSortKey(runs) : column.sortKey
    const label = t(column.labelKey)
    const unit = column.unitKey ? <span className="ml-1 font-normal text-[var(--text-muted)]">{t(column.unitKey)}</span> : null
    if (!sortKey) return <span>{label}{unit}</span>
    const active = sort === sortKey
    return (
      <button
        type="button"
        className={cn('inline-flex items-center gap-1 hover:text-[var(--text)]', active && 'text-[var(--text)]')}
        onClick={() => {
          const next = nextSort(sort, order, sortKey)
          onSort(next.sort, next.order)
        }}
      >
        {label}{unit}{sortIndicator(active, order)}
      </button>
    )
  }

  const renderCell = (column: ReportColumn, run: ReportRun, stickyClass = '') => {
    const text = formatColumnValue(column, run)
    const title = column.exact?.(run)
    const isStatus = column.key === 'status'
    return (
      <td
        key={column.key}
        className={cn(
          CELL,
          column.numeric || isStatus ? 'text-right tabular-nums' : 'text-left',
          stickyClass,
          run.availability === 'unavailable' && !isStatus && text === '—' && 'text-[var(--text-muted)]',
        )}
        title={title}
      >
        {isStatus ? (
          <span className={cn('type-body-xs', run.lifecycle === 'error' && 'text-[var(--danger)]')}>
            {t(`perf.report.lifecycle.${run.lifecycle}`)}
          </span>
        ) : (
          text
        )}
      </td>
    )
  }

  const renderRow = (run: ReportRun, flat: boolean) => {
    const open = expanded === run.run_key
    const canOpen = canOpenRequests ? canOpenRequests(run) : run.availability === 'available'
    const firstColumns = columns.slice(0, 1)
    const secondColumns = columns.slice(1, 2)
    const rest = columns.slice(2)
    return (
      <Fragment key={run.run_key}>
        <tr
          className={cn('transition-colors hover:bg-[var(--bg-card2)]', ROW_HEIGHT)}
          tabIndex={0}
          aria-expanded={open}
          aria-label={t(open ? 'perf.report.collapseRow' : 'perf.report.expandRow')}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              setExpanded(open ? null : run.run_key)
            }
          }}
          onClick={() => setExpanded(open ? null : run.run_key)}
        >
          {firstColumns.map((column) => (
            <td
              key={column.key}
              className={cn(CELL, 'text-left tabular-nums', STICKY_ONE)}
              title={column.exact?.(run)}
            >
              <span className="block">{formatColumnValue(column, run)}</span>
              {flat && <span className="block type-body-xs text-[var(--text-muted)]">{tierCaption(run, t)}</span>}
            </td>
          ))}
          {secondColumns.map((column) => (
            <td key={column.key} className={cn(CELL, 'text-left tabular-nums', STICKY_TWO)} title={loadCaption(run, t)}>
              <span className="block">{formatColumnValue(column, run)}</span>
              <span className="block type-body-xs text-[var(--text-muted)]">{loadCaption(run, t)}</span>
            </td>
          ))}
          {rest.map((column) => renderCell(column, run))}
        </tr>
        {open && (
          <RunDetailRow run={run} columns={columns} canOpen={canOpen} onOpenRequests={onOpenRequests} />
        )}
      </Fragment>
    )
  }

  return (
    <div className="flex flex-col gap-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="type-label-xs text-[var(--text-muted)]">{t('perf.report.preset')}</span>
          {REPORT_PRESETS.map((item) => (
            <Button
              key={item}
              size="sm"
              variant={preset === item ? 'primary' : 'ghost'}
              onClick={() => onPreset(item)}
            >
              {t(`perf.report.preset_${item}`)}
            </Button>
          ))}
        </div>
        <p className="type-body-xs text-[var(--text-muted)]">
          {t('perf.report.tableTotal', { filtered: total, total })}
          {' · '}
          {t('perf.report.pageRows', { count: runs.length })}
        </p>
      </div>

      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={t('perf.report.title')}>
        <table className="border-collapse text-[13px] text-[var(--text)]">
          <thead>
            <tr>
              {grouped.map((group) => (
                <th
                  key={group.key}
                  scope="colgroup"
                  colSpan={group.columns.length}
                  className="border-b border-[var(--border)] bg-[var(--bg-card2)] px-2.5 py-1.5 text-center type-table-xs text-[var(--text-muted)]"
                >
                  {t(REPORT_COLUMN_GROUPS.find((entry) => entry.key === group.key)?.labelKey ?? '')}
                </th>
              ))}
            </tr>
            <tr>
              {columns.map((column, index) => {
                const sortKey = column.key === 'load' ? loadSortKey(runs) : column.sortKey
                const sticky = index === 0 ? STICKY_ONE : index === 1 ? STICKY_TWO : ''
                return (
                  <th
                    key={column.key}
                    scope="col"
                    aria-sort={sortKey && sort === sortKey ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                    className={cn(
                      'border-b border-[var(--border)] bg-[var(--bg-card2)] px-2.5 py-1.5 type-table-xs font-medium text-[var(--text-muted)]',
                      column.numeric || column.key === 'status' ? 'text-right' : 'text-left',
                      sticky,
                    )}
                    style={index === 0 ? { minWidth: 112, width: 112 } : undefined}
                  >
                    {headerFor(column)}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {total === 0 && (
              <tr>
                <td colSpan={columns.length} className="px-4 py-6 text-center text-[var(--text-muted)]">
                  {loading ? '…' : t('perf.report.requests.noRows')}
                </td>
              </tr>
            )}
            {sort === 'order'
              ? groupedRows.map((group) => (
                  <Fragment key={group.key}>
                    <tr className="bg-[var(--bg-card2)]">
                      <th
                        scope="colgroup"
                        colSpan={columns.length}
                        className="border-b border-[var(--border)] px-2.5 py-1.5 text-left type-table-xs text-[var(--text)]"
                      >
                        {groupHeaderLabel(group.rows[0], t)}
                        {group.continued && (
                          <span className="ml-1 text-[var(--text-muted)]">{t('perf.report.continued')}</span>
                        )}
                      </th>
                    </tr>
                    {group.rows.map((run) => renderRow(run, false))}
                  </Fragment>
                ))
              : runs.map((run) => renderRow(run, true))}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="type-body-xs text-[var(--text-muted)]">{t('perf.report.sortHint')}</p>
        <Pagination
          page={page}
          totalPages={totalPages}
          onPageChange={(next) => onPage((next - 1) * limit)}
        />
      </div>
    </div>
  )
}
