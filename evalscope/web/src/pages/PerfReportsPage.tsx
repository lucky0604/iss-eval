import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ChevronRight, Copy, Check } from 'lucide-react'
import { useCopy } from '@/hooks/useCopy'
import { useLocale, type Translate } from '@/contexts/LocaleContext'
import { useScan } from '@/contexts/ReportsContext'
import { useAsyncResource } from '@/hooks/useAsyncResource'
import { useBatchDelete } from '@/hooks/useBatchDelete'
import { useScopedState } from '@/hooks/useScopedState'
import {
  getCacheExperimentReportUrl,
  getPerfHistoryReportUrl,
  listPerfEntries,
  deletePerfRun,
  type PerfEntryEngine,
  type PerfEntrySort,
  type PerfEntryTestType,
} from '@/api/perf'
import type { PerformanceResultEntry } from '@/api/types'
import Skeleton from '@/components/ui/Skeleton'
import EmptyStateSystem, { type ResolvedEmptyStateAction } from '@/components/common/EmptyStateSystem'
import SearchInput from '@/components/ui/SearchInput'
import SelectionCheckbox from '@/components/ui/SelectionCheckbox'
import SegmentedControl from '@/components/ui/SegmentedControl'
import ErrorAlert from '@/components/ui/ErrorAlert'
import ConfirmDialog from '@/components/ui/ConfirmDialog'
import SelectionTray from '@/components/reports/SelectionTray'
import { formatMetric } from '@/domain/metric'
import { formatTimestamp } from '@/utils/formatUtils'
import { experimentIdOf, isEntryOpenable, selectionSupport } from '@/domain/perf/entries'
import { engineLabelKey, qualityLabelKey, testTypeLabelKey } from '@/domain/perf/capabilities'
import { statusLabelKey } from '@/domain/tasks/status'

/** Sort options in display order, also the source of the segmented control. */
const SORT_KEYS: readonly PerfEntrySort[] = ['time', 'rps', 'latency']

const TEST_TYPES: readonly Exclude<PerfEntryTestType, 'unknown'>[] = ['single', 'sweep', 'cache_experiment']
const ENGINES: readonly PerfEntryEngine[] = ['evalscope', 'aiperf']

const PAGE_SIZE = 25

/** Stable placeholders so an unresolved read keeps a single identity. */
const EMPTY_ENTRIES: PerformanceResultEntry[] = []

const SELECT_CLASS
  = 'h-10 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-deep)] px-2 text-sm text-[var(--text)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-dim)]'

function entryTitle(entry: PerformanceResultEntry): string {
  return entry.display_name || entry.alias || entry.model || entry.path || '—'
}

/** Copyable endpoint host, so same-model runs on different servers stay distinguishable. */
function EntryEndpoint({ endpoint, t }: { endpoint: string; t: Translate }) {
  const { copy, copied } = useCopy()
  return (
    <span className="inline-flex min-w-0 items-center gap-1">
      <span className="max-w-full truncate font-mono text-[var(--text)]" title={endpoint}>{endpoint}</span>
      <button
        type="button"
        aria-label={`${t(copied ? 'tasks.copied' : 'tasks.copy')}: ${endpoint}`}
        title={t(copied ? 'tasks.copied' : 'tasks.copy')}
        onClick={(event) => { event.stopPropagation(); copy(endpoint) }}
        className="flex min-h-11 min-w-11 items-center justify-center rounded text-[var(--text-muted)] hover:text-[var(--text)]"
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
    </span>
  )
}

function EntryBadges({ entry, t }: { entry: PerformanceResultEntry; t: Translate }) {
  const quality = entry.quality ?? null
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="rounded-full bg-[var(--accent-dim)] px-2 py-0.5 text-xs text-[var(--accent)]">
        {t(testTypeLabelKey(entry.test_type))}
      </span>
      {entry.engine && (
        <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--text-muted)]">
          {t(engineLabelKey(entry.engine))}
          {entry.executor_version ? ` ${entry.executor_version}` : ''}
        </span>
      )}
      {entry.execution_status && (
        <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--text-muted)]">
          {t(statusLabelKey(entry.execution_status))}
        </span>
      )}
      <span
        className={[
          'rounded-full px-2 py-0.5 text-xs',
          quality === 'complete'
            ? 'bg-[var(--success-bg)] text-[var(--success)]'
            : quality === 'invalid'
              ? 'bg-[var(--danger-bg)] text-[var(--danger)]'
              : quality === 'partial'
                ? 'bg-[var(--warning-bg)] text-[var(--yellow)]'
                : 'border border-[var(--border)] text-[var(--text-muted)]',
        ].join(' ')}
      >
        {t('perf.result.qualityLabel')}: {t(qualityLabelKey(quality))}
      </span>
      {entry.unreadable_reason && (
        <span className="rounded-full bg-[var(--danger-bg)] px-2 py-0.5 text-xs text-[var(--danger)]">
          {t('perf.archive.unreadableArchive')}
        </span>
      )}
    </span>
  )
}

function EntryCard({
  entry,
  selected,
  onToggle,
  onOpen,
  t,
}: {
  entry: PerformanceResultEntry
  selected: boolean
  onToggle: () => void
  onOpen: () => void
  t: Translate
}) {
  const openable = isEntryOpenable(entry)
  const runMetrics = entry.metric_scope === 'run'
  const metrics = [
    { key: 'best_rps', label: t('perf.archive.sort_rps'), value: entry.best_rps },
    { key: 'best_latency', label: t('perf.archive.sort_latency'), value: entry.best_latency },
    { key: 'success_rate', label: t('perf.archive.successColumn'), value: entry.success_rate },
  ]
  return (
    <div
      className={[
        'flex items-center gap-1 px-3 py-2 transition-colors',
        selected ? 'bg-[var(--accent-dim)]' : 'hover:bg-[var(--bg-card2)]',
      ].join(' ')}
    >
      <SelectionCheckbox
        checked={selected}
        onClick={onToggle}
        label={`${t('perf.archive.selectRun')}: ${entryTitle(entry)}`}
        className="shrink-0 cursor-pointer"
      />
      <div
        role={openable ? 'button' : undefined}
        tabIndex={openable ? 0 : undefined}
        aria-disabled={openable ? undefined : true}
        onClick={openable ? onOpen : undefined}
        onKeyDown={(event) => {
          if (openable && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault()
            onOpen()
          }
        }}
        title={openable ? undefined : entry.unreadable_reason ?? t('perf.result.unavailableSurface')}
        className={`grid min-h-16 min-w-0 flex-1 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3 py-2 text-left sm:grid-cols-[minmax(0,1fr)_17rem_auto] ${openable ? 'cursor-pointer' : 'cursor-not-allowed opacity-70'}`}
      >
        <div className="min-w-0 space-y-1.5">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
            <span title={entryTitle(entry)} className="type-body-sm min-w-0 truncate font-semibold text-[var(--text)]">{entryTitle(entry)}</span>
            <time dateTime={entry.timestamp || undefined} title={formatTimestamp(entry.timestamp, 'seconds')} className="shrink-0 text-xs tabular-nums text-[var(--text-muted)]">
              {formatTimestamp(entry.timestamp)}
            </time>
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--text-muted)]">
            {entry.alias && <span className="max-w-48 truncate" title={entry.model}>{entry.model}</span>}
            {entry.endpoint && <EntryEndpoint endpoint={entry.endpoint} t={t} />}
            {entry.dataset && <span className="max-w-32 truncate" title={entry.dataset}>{entry.dataset}</span>}
            {entry.num_runs != null && (
              <span>{entry.num_runs} {t(entry.num_runs === 1 ? 'perf.archive.runSingular' : 'perf.archive.runs')}</span>
            )}
          </div>
          <EntryBadges entry={entry} t={t} />
        </div>
        {runMetrics ? (
          <div className="col-span-2 row-start-2 grid grid-cols-3 gap-3 sm:col-span-1 sm:col-start-2 sm:row-start-1">
            {metrics.map(({ key, label, value }) => (
              <div key={key} className="min-w-0 sm:text-right">
                <span className="block text-xs text-[var(--text-muted)]">{label}</span>
                <span className="mt-1 block whitespace-nowrap text-sm font-medium tabular-nums text-[var(--text)]">
                  {formatMetric(value, undefined).primary}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="col-span-2 row-start-2 max-w-64 text-xs text-[var(--text-muted)] sm:col-span-1 sm:col-start-2 sm:row-start-1 sm:text-right">
            {entry.result_kind === 'experiment'
              ? t('perf.archive.experimentNoHeadline')
              : t('perf.result.unavailableSurface')}
          </p>
        )}
        <ChevronRight size={16} className="col-start-2 row-start-1 shrink-0 text-[var(--text-dim)] sm:col-start-3" />
      </div>
    </div>
  )
}

export default function PerfReportsPage() {
  const { t } = useLocale()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { rootPath, scanToken, setRootPath } = useScan()

  // Sync root_path from URL on mount (e.g. when navigating back from a detail
  // or compare page, which carry the active root in their breadcrumbs).
  useEffect(() => {
    const urlRoot = searchParams.get('root_path')
    if (urlRoot) setRootPath(urlRoot)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // List controls. Filters are local; the root (the data scope) is shared.
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [testType, setTestType] = useState<PerfEntryTestType | ''>('')
  const [engine, setEngine] = useState<PerfEntryEngine | ''>('')
  const [sortBy, setSortBy] = useState<PerfEntrySort>('time')

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300)
    return () => clearTimeout(timer)
  }, [query])

  // A root or rescan describes a different result set: selection and page are
  // abandoned by scope comparison. A filter change resets the page but keeps
  // the selection, which is stored as full entries and survives pagination.
  const scope = `${rootPath}\0${scanToken}`
  const pageScope = `${scope}\0${testType}\0${engine}\0${debouncedQuery}\0${sortBy}`
  const [pageToken, setPageToken] = useScopedState<string>(pageScope, '0')
  const offset = Number(pageToken) || 0
  const [selected, setSelected] = useScopedState<PerformanceResultEntry[]>(scope, EMPTY_ENTRIES)

  const entriesResource = useAsyncResource(
    (signal) => listPerfEntries({
      rootPath, testType: testType || undefined, engine: engine || undefined,
      query: debouncedQuery, sort: sortBy, offset, limit: PAGE_SIZE, signal,
    }),
    [rootPath, scanToken, testType, engine, debouncedQuery, sortBy, offset],
    { enabled: Boolean(rootPath), fallbackMessage: t('common.loadError') },
  )
  const page = entriesResource.data
  const entries = page?.entries ?? EMPTY_ENTRIES
  const loading = entriesResource.loading
  const hasLoaded = entriesResource.data !== undefined || Boolean(entriesResource.error)

  const reload = entriesResource.reload
  const deletion = useBatchDelete<PerformanceResultEntry>({
    items: selected,
    deleteItem: (entry) => deletePerfRun(rootPath, entry.path),
    onSettled: setSelected,
    reload,
    formatError: (msg) => t('reports.deleteFailed', { msg }),
  })

  const error = deletion.error ?? (entriesResource.error || null)
  const support = selectionSupport(selected)

  const toggleSelect = (entry: PerformanceResultEntry) => {
    if (selected.some((item) => item.entry_id === entry.entry_id)) {
      setSelected(selected.filter((item) => item.entry_id !== entry.entry_id))
      return
    }
    setSelected([...selected, entry])
  }

  const openEntry = (entry: PerformanceResultEntry) => {
    if (!entry.detail_path) return
    navigate(entry.detail_path)
  }

  const compareSelected = () => {
    if (!support.canCompare) return
    const paths = selected.map((entry) => entry.path)
    navigate(
      `/perf-compare?paths=${encodeURIComponent(paths.join(';'))}`
        + `&root_path=${encodeURIComponent(rootPath)}`,
    )
  }

  const viewSelectedHtml = () => {
    if (!support.canViewHtml) return
    const entry = selected[0]
    if (entry.result_kind === 'experiment') {
      const taskId = experimentIdOf(entry)
      if (taskId) window.open(getCacheExperimentReportUrl(taskId, rootPath), '_blank')
      return
    }
    window.open(getPerfHistoryReportUrl(rootPath, entry.path), '_blank')
  }

  const pendingDeleteItems = useMemo(
    () => selected.map((entry) => `${entryTitle(entry)} · ${formatTimestamp(entry.timestamp, 'seconds')}`),
    [selected],
  )

  const actionNote = support.deleteBlocked
    ? t(support.deleteBlockedReason === 'read_only' ? 'perf.archive.deleteReadOnly' : 'perf.archive.deleteBlocked')
    : support.compareBlocked
      ? t('perf.archive.compareBlocked')
      : support.viewHtmlBlocked
        ? t('perf.archive.htmlBlocked')
        : undefined

  // In-view recovery: retry re-fetches, clear-filters resets every filter.
  const handleEmptyAction = (action: ResolvedEmptyStateAction) => {
    if (action.navigateTo === '#retry') {
      reload()
      return true
    }
    if (action.navigateTo === '#clear-filters') {
      setQuery('')
      setTestType('')
      setEngine('')
      return true
    }
    return false
  }

  const total = page?.total ?? 0
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + PAGE_SIZE, total)

  return (
    <div className="page-enter mx-auto flex w-full max-w-7xl flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="type-title-md text-[var(--text)]">{t('perf.archive.resultsTitle')}</h1>
          {page && (
            <p className="mt-1 type-body-xs text-[var(--text-muted)]">
              {t('perf.archive.scannedAt', { time: formatTimestamp(page.observed_at, 'seconds') })}
            </p>
          )}
        </div>
      </div>

      {error && (
        <ErrorAlert>{error}</ErrorAlert>
      )}

      {page?.completeness === 'partial' && (
        <p role="status" className="rounded-[var(--radius-sm)] border border-[var(--warning-bg)] bg-[var(--warning-bg)] px-4 py-3 type-body-sm text-[var(--yellow)]">
          {t('perf.archive.partialResults')}
        </p>
      )}

      {loading && !page ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} height={72} className="rounded-[var(--radius)]" />
          ))}
        </div>
      ) : entries.length === 0 && total === 0 ? (
        <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)]">
          <EmptyStateSystem
            reason={error ? 'load-error' : 'no-data'}
            context={{ view: 'performance', retryTo: '#retry' }}
            hint={!error && hasLoaded && !testType && !engine && !debouncedQuery ? t('perf.archive.noRunsHint') : undefined}
            onAction={handleEmptyAction}
          />
        </div>
      ) : (
        <>
          {/* Filters. `engine` is explicitly secondary: the page is one result list. */}
          <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
            <SearchInput
              value={query}
              onChange={setQuery}
              placeholder={t('perf.archive.searchPlaceholder')}
              className="w-full lg:max-w-md lg:flex-1 [&>input]:h-10 [&>input]:py-0"
            />
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                <span className="whitespace-nowrap">{t('perf.archive.typeFilter')}</span>
                <select value={testType} onChange={(event) => setTestType(event.target.value as PerfEntryTestType | '')} className={SELECT_CLASS} aria-label={t('perf.archive.typeFilter')}>
                  <option value="">{t('perf.archive.allTypes')}</option>
                  {TEST_TYPES.map((value) => (
                    <option key={value} value={value}>{t(testTypeLabelKey(value))}</option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                <span className="whitespace-nowrap">{t('perf.archive.engineFilter')}</span>
                <select value={engine} onChange={(event) => setEngine(event.target.value as PerfEntryEngine | '')} className={SELECT_CLASS} aria-label={t('perf.archive.engineFilter')}>
                  <option value="">{t('perf.archive.allEngines')}</option>
                  {ENGINES.map((value) => (
                    <option key={value} value={value}>{t(engineLabelKey(value))}</option>
                  ))}
                </select>
              </label>
              <SegmentedControl
                options={SORT_KEYS.map((k) => ({ value: k, label: t(`perf.archive.sort_${k}`) }))}
                value={sortBy}
                onChange={setSortBy}
                ariaLabel={t('perf.archive.sortLabel')}
                size="sm"
                className="h-10 w-fit"
              />
            </div>
          </div>

          {entries.length > 0 ? (
            <div className="overflow-hidden rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)]">
              <div className="divide-y divide-[var(--border)]">
                {entries.map((entry) => (
                  <EntryCard
                    key={entry.entry_id}
                    entry={entry}
                    selected={selected.some((item) => item.entry_id === entry.entry_id)}
                    onToggle={() => toggleSelect(entry)}
                    onOpen={() => openEntry(entry)}
                    t={t}
                  />
                ))}
              </div>
            </div>
          ) : (
            <EmptyStateSystem
              reason="no-match"
              context={{ view: 'performance', clearFiltersTo: '#clear-filters' }}
              onAction={handleEmptyAction}
            />
          )}

          {total > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="type-body-xs tabular-nums text-[var(--text-muted)]">
                {t('perf.archive.pageRange', { from, to, total })}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={offset === 0}
                  onClick={() => setPageToken(String(Math.max(0, offset - PAGE_SIZE)))}
                  className="min-h-9 rounded-[var(--radius-sm)] border border-[var(--border)] px-3 text-sm text-[var(--text)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t('perf.archive.previousPage')}
                </button>
                <button
                  type="button"
                  disabled={offset + PAGE_SIZE >= total}
                  onClick={() => setPageToken(String(offset + PAGE_SIZE))}
                  className="min-h-9 rounded-[var(--radius-sm)] border border-[var(--border)] px-3 text-sm text-[var(--text)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t('perf.archive.nextPage')}
                </button>
              </div>
            </div>
          )}

          <SelectionTray
            count={support.count}
            canViewHtml={support.canViewHtml}
            compareDisabled={!support.canCompare}
            deleteDisabled={!support.canDelete}
            actionNote={actionNote}
            onViewHtml={viewSelectedHtml}
            onCompare={compareSelected}
            onClear={() => setSelected([])}
            onDelete={deletion.request}
            deleting={deletion.deleting}
          />

          <ConfirmDialog
            open={deletion.confirmOpen}
            danger
            busy={deletion.deleting}
            title={t('reports.deleteConfirmTitle')}
            message={t('reports.deleteConfirm', { n: selected.length })}
            items={pendingDeleteItems}
            confirmLabel={t('reports.delete')}
            cancelLabel={t('common.cancel')}
            onConfirm={deletion.confirm}
            onCancel={deletion.cancel}
          />
        </>
      )}
    </div>
  )
}
