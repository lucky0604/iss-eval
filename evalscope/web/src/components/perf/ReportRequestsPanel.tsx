/**
 * Per-request details for one run of the report, paged and filtered on the
 * server across the whole normalized store — never only the first page.
 */
import { useMemo, useState } from 'react'
import { useLocale } from '@/contexts/LocaleContext'
import { useAsyncResource } from '@/hooks/useAsyncResource'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Pagination from '@/components/ui/Pagination'
import Skeleton from '@/components/ui/Skeleton'
import ErrorAlert from '@/components/ui/ErrorAlert'
import ExportStatusLine from '@/components/perf/ExportStatusLine'
import { usePerfExport } from '@/hooks/usePerfExport'
import { cn } from '@/lib/utils'
import { getPerfReportRequests, type ReportLocation } from '@/api/perfReports'
import type { ReportRequestRow, ReportRun } from '@/api/generated/contracts'
import { formatNumber } from '@/domain/perf/reportTable'

const PAGE_SIZE = 50
type StatusFilter = 'all' | 'success' | 'failed'
type RequestSort = 'index' | 'latency' | 'ttft' | 'tpot' | 'prompt_tokens' | 'output_tokens'

const SORTS: Array<{ key: RequestSort; labelKey: string }> = [
  { key: 'latency', labelKey: 'perf.report.requests.sortLatency' },
  { key: 'ttft', labelKey: 'perf.report.requests.sortTtft' },
  { key: 'tpot', labelKey: 'perf.report.requests.sortTpot' },
  { key: 'prompt_tokens', labelKey: 'perf.report.requests.sortPrompt' },
  { key: 'output_tokens', labelKey: 'perf.report.requests.sortOutput' },
]

const TH = 'px-2.5 py-1.5 border-b border-[var(--border)] type-table-xs text-right text-[var(--text-muted)] whitespace-nowrap'
const TD = 'px-2.5 py-1.5 border-b border-[var(--border)] type-body-xs tabular-nums text-right whitespace-nowrap'
const SELECT = 'rounded-[var(--radius-sm)] border border-[var(--border-md)] bg-[var(--bg-card)] px-2 py-1 text-xs text-[var(--text)]'
const INPUT = 'rounded-[var(--radius-sm)] border border-[var(--border-md)] bg-[var(--bg-card)] px-2 py-1 text-xs text-[var(--text)]'

interface Props {
  location: ReportLocation
  runs: ReportRun[]
  selectedRunKey?: string | null
  onSelectRun?: (runKey: string) => void
  available: boolean
}

function milliseconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  return formatNumber(seconds * 1000, 2)
}

function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return formatNumber(value, 3)
}

function tokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return formatNumber(value, 0)
}

export default function ReportRequestsPanel({
  location,
  runs,
  selectedRunKey,
  onSelectRun,
  available,
}: Props) {
  const { t, locale } = useLocale()
  const [status, setStatus] = useState<StatusFilter>('all')
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<RequestSort>('index')
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const [offset, setOffset] = useState(0)
  const exportFlow = usePerfExport()
  const exportBusy = exportFlow.phase === 'starting' || exportFlow.phase === 'running'

  const availableRuns = useMemo(() => runs.filter((run) => run.availability === 'available'), [runs])
  const activeRun = availableRuns.find((run) => run.run_key === selectedRunKey) ?? availableRuns[0] ?? null


  const resource = useAsyncResource(
    (signal) =>
      getPerfReportRequests(
        location,
        {
          runKey: activeRun?.run_key ?? '',
          offset,
          limit: PAGE_SIZE,
          status: status === 'all' ? undefined : status,
          search: search || undefined,
          sort,
          order,
        },
        signal,
      ),
    [location.taskId, location.rootPath, location.path, activeRun?.run_key, offset, status, search, sort, order, available],
    { enabled: available && Boolean(activeRun), fallbackMessage: t('common.loadError') },
  )

  if (!available) {
    return (
      <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
        <h2 className="type-label-xs text-[var(--text)]">{t('perf.report.requests.title')}</h2>
        <p className="mt-1 type-body-xs text-[var(--text-muted)]">{t('perf.report.requests.unavailable')}</p>
      </section>
    )
  }

  if (availableRuns.length === 0) {
    return (
      <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
        <h2 className="type-label-xs text-[var(--text)]">{t('perf.report.requests.title')}</h2>
        <p className="mt-1 type-body-xs text-[var(--text-muted)]">{t('perf.report.requests.noRows')}</p>
      </section>
    )
  }

  const data = resource.data
  const total = data?.total ?? 0
  const page = Math.floor(offset / PAGE_SIZE) + 1
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const toggleSort = (key: RequestSort) => {
    setOffset(0)
    if (sort === key) setOrder(order === 'asc' ? 'desc' : 'asc')
    else {
      setSort(key)
      setOrder('desc')
    }
  }

  const sortIndicator = (key: RequestSort) => (sort === key ? (order === 'asc' ? '↑' : '↓') : '')

  const sortHeader = (key: RequestSort, label: string) => (
    <th key={key} scope="col" className={TH} aria-sort={sort === key ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className="tabular-nums hover:text-[var(--text)]" onClick={() => toggleSort(key)}>
        {label} {sortIndicator(key)}
      </button>
    </th>
  )

  const startRequestExport = (runKey: string) =>
    exportFlow.start({
      kind: 'requests',
      location,
      runKey,
      status: status === 'all' ? undefined : status,
      search: search || undefined,
      phase: 'profiling',
      locale,
    })
  return (
    <section className="flex flex-col gap-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="type-label-xs text-[var(--text)]">{t('perf.report.requests.title')}</h2>
        <div className="flex flex-wrap items-center gap-2">
          <label className="type-body-xs text-[var(--text-muted)]">
            {t('perf.report.requests.run')}{' '}
            <select
              className={SELECT}
              value={activeRun?.run_key ?? ''}
              onChange={(event) => {
                onSelectRun?.(event.target.value)
                setOffset(0)
              }}
            >
              {availableRuns.map((run) => (
                <option key={run.run_key} value={run.run_key}>
                  {run.run_id}
                </option>
              ))}
            </select>
          </label>
          <label className="type-body-xs text-[var(--text-muted)]">
            {t('perf.report.requests.status')}{' '}
            <select
              className={SELECT}
              value={status}
              onChange={(event) => {
                setStatus(event.target.value as StatusFilter)
                setOffset(0)
              }}
            >
              <option value="all">{t('perf.report.requests.statusAll')}</option>
              <option value="success">{t('perf.report.requests.statusSuccess')}</option>
              <option value="failed">{t('perf.report.requests.statusFailed')}</option>
            </select>
          </label>
          <label className="type-body-xs text-[var(--text-muted)]">
            {t('perf.report.requests.sort')}{' '}
            <select
              className={SELECT}
              value={sort}
              onChange={(event) => {
                toggleSort(event.target.value as RequestSort)
                setOffset(0)
              }}
            >
              <option value="index">{t('perf.report.requests.index')}</option>
              {SORTS.map((item) => (
                <option key={item.key} value={item.key}>{t(item.labelKey)}</option>
              ))}
            </select>
          </label>
          <form
            className="flex items-center gap-1"
            onSubmit={(event) => {
              event.preventDefault()
              setSearch(searchDraft.trim())
              setOffset(0)
            }}
          >
            <input
              className={INPUT}
              value={searchDraft}
              placeholder={t('perf.report.requests.search')}
              onChange={(event) => setSearchDraft(event.target.value)}
              aria-label={t('perf.report.requests.search')}
            />
            <Button size="sm" variant="outline" type="submit">{t('common.search')}</Button>
          </form>
        </div>
      </div>

      {resource.error && <ErrorAlert>{resource.error}</ErrorAlert>}
      {resource.loading && !data && <Skeleton lines={4} height={14} />}

      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={t('perf.report.requests.title')}>
        <table className="w-full border-collapse text-[var(--text)]">
          <thead>
            <tr>
              {sortHeader('index', t('perf.report.requests.index'))}
              <th scope="col" className={cn(TH, 'text-left')}>{t('perf.report.requests.status')}</th>
              {sortHeader('prompt_tokens', t('perf.report.requests.prompt'))}
              {sortHeader('output_tokens', t('perf.report.requests.output'))}
              <th scope="col" className={TH}>{t('perf.report.requests.cached')}</th>
              {sortHeader('ttft', t('perf.report.requests.ttft'))}
              {sortHeader('tpot', t('perf.report.requests.tpot'))}
              {sortHeader('latency', t('perf.report.requests.e2e'))}
              <th scope="col" className={cn(TH, 'text-left')}>{t('perf.report.requests.reason')}</th>
            </tr>
          </thead>
          <tbody>
            {(data?.requests ?? []).map((row: ReportRequestRow) => (
              <tr key={`${row.session_id ?? ''}-${row.index}`} className="hover:bg-[var(--bg-card2)]">
                <td className={TD}>{row.index}</td>
                <td className={cn(TD, 'text-left')}>
                  <Badge variant={row.status === 'success' ? 'success' : row.status === 'cancelled' ? 'warning' : 'danger'}>
                    {row.status}
                  </Badge>
                </td>
                <td className={TD}>{tokens(row.prompt_tokens)}</td>
                <td className={TD}>{tokens(row.output_tokens)}</td>
                <td className={TD}>{tokens(row.cached_tokens)}</td>
                <td className={TD} title={row.tpot_source ? `TTFT · ${row.tpot_source}` : undefined}>{milliseconds(row.ttft_seconds)}</td>
                <td className={TD} title={row.tpot_source ?? undefined}>{milliseconds(row.tpot_seconds)}</td>
                <td className={TD}>{seconds(row.latency_seconds)}</td>
                <td className={cn(TD, 'max-w-[240px] truncate text-left text-[var(--text-muted)]')} title={row.status_reason ?? undefined}>
                  {row.status_reason ?? '—'}
                </td>
              </tr>
            ))}
            {data && data.requests.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-4 text-center text-[var(--text-muted)]">
                  {t('perf.report.requests.noRows')}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <ExportStatusLine state={exportFlow} />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="type-body-xs text-[var(--text-muted)]">{t('perf.report.requests.total', { total })}</p>
        <div className="flex items-center gap-2">
          {activeRun && (
            <Button
              size="sm"
              variant="outline"
              disabled={exportBusy}
              onClick={() => startRequestExport(activeRun.run_key)}
            >
              {t('perf.report.requests.download')}
            </Button>
          )}
          <Pagination page={page} totalPages={totalPages} onPageChange={(next) => setOffset((next - 1) * PAGE_SIZE)} />
        </div>
      </div>
    </section>
  )
}
