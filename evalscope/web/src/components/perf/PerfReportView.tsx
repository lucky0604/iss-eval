/**
 * The shared numeric performance report: header, numeric wide table, matrix
 * charts and full request details, all read from one immutable snapshot.
 *
 * The same component serves the legacy report page, portable plans and cache
 * experiments, so the Web view, the HTML/PDF exports and the CSV files never
 * describe different data.
 */
import { useMemo, useState, type ReactNode } from 'react'
import { useLocale } from '@/contexts/LocaleContext'
import { useAsyncResource } from '@/hooks/useAsyncResource'
import Badge from '@/components/ui/Badge'
import Skeleton from '@/components/ui/Skeleton'
import ErrorAlert from '@/components/ui/ErrorAlert'
import Tabs from '@/components/ui/Tabs'
import ReportWideTable from '@/components/perf/ReportWideTable'
import ReportCharts from '@/components/perf/ReportCharts'
import ReportRequestsPanel from '@/components/perf/ReportRequestsPanel'
import ReportNodesPanel from '@/components/perf/ReportNodesPanel'
import ExportStatusLine from '@/components/perf/ExportStatusLine'
import { usePerfExport } from '@/hooks/usePerfExport'
import {
  getPerfExportCapabilities,
  getPerfReportRuns,
  getPerfReportSummary,
  type PerfExportKind,
  type ReportLocation,
} from '@/api/perfReports'
import type { ReportRun, ReportRunDimensions } from '@/api/generated/contracts'
import type { ReportPreset, ReportRunSort } from '@/domain/perf/reportTable'
import { formatTimestamp } from '@/utils/formatUtils'

const LIMIT = 100
const SELECTOR_PAGE = 500
const SELECTOR_MAX_PAGES = 20

type ViewTab = 'table' | 'charts' | 'nodes' | 'requests'

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="type-label-xs text-[var(--text-muted)]">{label}</dt>
      <dd className="type-body-xs text-[var(--text)]">{value}</dd>
    </div>
  )
}

export interface PerfReportViewProps {
  location: ReportLocation
}

export default function PerfReportView({ location }: PerfReportViewProps) {
  const { t, locale } = useLocale()
  const deps = [location.taskId, location.rootPath, location.path]

  const summary = useAsyncResource(
    (signal) => getPerfReportSummary(location, signal),
    deps,
    { fallbackMessage: t('common.loadError') },
  )
  const header = summary.data?.report
  // Every endpoint of this page reads the snapshot the header named, so a
  // table page, a chart and a request page can never describe different states.
  const pinnedLocation = useMemo<ReportLocation>(
    () => ({
      taskId: location.taskId,
      rootPath: location.rootPath,
      path: location.path,
      snapshotId: summary.data?.report.snapshot_id,
    }),
    [location.taskId, location.rootPath, location.path, summary.data?.report.snapshot_id],
  )

  const [tab, setTab] = useState<ViewTab>('table')
  const [preset, setPreset] = useState<ReportPreset>('core')
  const [sort, setSort] = useState<ReportRunSort>('comparison')
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const [offset, setOffset] = useState(0)
  const [group, setGroup] = useState('')
  const [selectedRunKey, setSelectedRunKey] = useState<string | null>(null)


  const runs = useAsyncResource(
    (signal) =>
      getPerfReportRuns(
        pinnedLocation,
        { offset, limit: LIMIT, group: group || undefined, sort, order },
        signal,
      ),
    [...deps, pinnedLocation.snapshotId, offset, group, sort, order, Boolean(header)],
    { enabled: Boolean(header), fallbackMessage: t('common.loadError') },
  )

  // The request panel's run selector must offer every run, not only the page.
  const allRuns = useAsyncResource(
    async (signal) => {
      const rows: ReportRun[] = []
      let next = 0
      for (let page = 0; page < SELECTOR_MAX_PAGES; page += 1) {
        const response = await getPerfReportRuns(
          pinnedLocation,
          { offset: next, limit: SELECTOR_PAGE, sort: 'order' },
          signal,
        )
        rows.push(...response.rows)
        if (rows.length >= response.total || response.rows.length === 0) break
        next += response.rows.length
      }
      return rows
    },
    [...deps, pinnedLocation.snapshotId, Boolean(header)],
    { enabled: Boolean(header), fallbackMessage: t('common.loadError') },
  )

  // The export surface and its PDF capability are read once per page; both
  // hooks run even before the header arrives so hook order stays stable.
  const exportFlow = usePerfExport()
  const exportCapabilities = useAsyncResource(
    (signal) => getPerfExportCapabilities(signal),
    [],
    { enabled: Boolean(header), fallbackMessage: t('common.loadError') },
  )

  if (summary.error) {
    return (
      <div className="flex flex-col gap-3">
        <ErrorAlert>{summary.error}</ErrorAlert>
      </div>
    )
  }

  if (!header) {
    return (
      <div className="flex flex-col gap-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
        <Skeleton width={240} height={18} />
        <Skeleton lines={6} />
      </div>
    )
  }

  const capabilities = header.capabilities
  const supported = (capability: typeof capabilities.requests) =>
    capability.availability === 'available' || capability.availability === 'partial'
  const requestsAvailable = supported(capabilities.requests)
  const nodesAvailable = supported(capabilities.node_metrics)
  const counts = header.counts
  const groupLabel = (dimensions: ReportRunDimensions) =>
    t('perf.report.groupLabel', {
      workload: dimensions.workload || t('perf.report.tier.unknown'),
      ratio: dimensions.target_ratio == null ? '—' : Math.round(dimensions.target_ratio * 100),
      osl: dimensions.output_token_limit ?? '—',
      mode: t(dimensions.load_mode === 'open_loop' ? 'perf.report.tier.modeOpen' : 'perf.report.tier.modeClosed'),
    })

  const openRun = (runKey: string) => {
    setSelectedRunKey(runKey)
    setTab('requests')
  }

  const exportQuery = { group: group || undefined, preset: preset === 'all' ? ('all' as const) : ('core' as const) }
  const exportBusy = exportFlow.phase === 'starting' || exportFlow.phase === 'running'
  const pdfAvailable = exportCapabilities.data?.pdf_available ?? true
  const exportButtons: Array<{ key: PerfExportKind; label: string; disabled?: boolean; title?: string }> = [
    { key: 'html', label: t('perf.report.export.html') },
    {
      key: 'pdf',
      label: t('perf.report.export.pdf'),
      disabled: !pdfAvailable,
      title: pdfAvailable
        ? undefined
        : t('perf.report.export.pdfUnavailable', { message: exportCapabilities.data?.pdf_message ?? '' }),
    },
    { key: 'csv', label: t('perf.report.export.summaryCsv') },
    { key: 'requests', label: t('perf.report.export.allRequests') },
    { key: 'bundle', label: t('perf.report.export.bundle') },
  ]

  const tablePanel = (
    <ReportWideTable
      runs={runs.data?.rows ?? []}
      total={runs.data?.total ?? 0}
      offset={offset}
      limit={LIMIT}
      sort={sort}
      order={order}
      preset={preset}
      loading={runs.loading}
      onPreset={setPreset}
      onSort={(nextSort, nextOrder) => {
        setSort(nextSort)
        setOrder(nextOrder)
        setOffset(0)
      }}
      onPage={setOffset}
      onOpenRequests={(run) => openRun(run.run_key)}
    />
  )

  const panels: Record<string, ReactNode> = {
    'perf-report-table': tablePanel,
    'perf-report-charts': (
      <ReportCharts location={pinnedLocation} available={Boolean(summary.data)} onOpenRun={openRun} />
    ),
    'perf-report-nodes': (
      <ReportNodesPanel runs={allRuns.data ?? []} available={nodesAvailable} />
    ),
    'perf-report-requests': (
      <ReportRequestsPanel
        location={pinnedLocation}
        runs={allRuns.data ?? []}
        selectedRunKey={selectedRunKey}
        onSelectRun={setSelectedRunKey}
        available={requestsAvailable}
      />
    ),
  }

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="type-title-sm text-[var(--text)]">{t('perf.report.title')}</h1>
              <Badge>{header.source_kind}</Badge>
              {header.partial && (
                <Badge variant="warning" title={header.partial_reason ?? undefined}>
                  {t('perf.report.partial')}
                </Badge>
              )}
              {header.quality && header.quality !== 'complete' && (
                <Badge variant={header.quality === 'invalid' ? 'danger' : 'warning'} title={(header.quality_reasons ?? []).join('; ') || undefined}>
                  {header.quality}
                </Badge>
              )}
            </div>
            <p className="type-body-xs text-[var(--text-muted)]">{t('perf.report.subtitle')}</p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <div className="flex flex-wrap items-center justify-end gap-2">
              <span className="type-label-xs text-[var(--text-muted)]">{t('perf.report.export.title')}</span>
              {exportButtons.map((button) => (
                <button
                  key={button.key}
                  type="button"
                  className="rounded-[var(--radius-sm)] border border-[var(--border-md)] px-2.5 py-1 text-xs text-[var(--text-muted)] hover:bg-[var(--bg-card2)] hover:text-[var(--text)] disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={exportBusy || button.disabled}
                  title={button.title}
                  onClick={() =>
                    exportFlow.start({
                      kind: button.key,
                      location,
                      group: exportQuery.group,
                      preset: exportQuery.preset,
                      // The exported document speaks the language the button was clicked in.
                      locale,
                    })
                  }
                >
                  {button.label}
                </button>
              ))}
            </div>
            <p className="type-body-xs text-[var(--text-muted)]">
              {group
                ? t('perf.report.export.scopeGroup', { group: header.groups?.find((item) => item.group_key === group)?.group_key ?? group })
                : t('perf.report.export.scopeAll')}
            </p>
            {exportCapabilities.error && <p className="type-body-xs text-[var(--danger)]">{exportCapabilities.error}</p>}
            <ExportStatusLine state={exportFlow} />
          </div>
        </div>

        <dl className="flex flex-wrap gap-x-6 gap-y-2">
          <MetaItem label={t('perf.report.snapshot')} value={header.snapshot_id} />
          <MetaItem label={t('perf.report.created')} value={formatTimestamp(header.created_at, 'seconds')} />
          <MetaItem label={t('perf.report.sourceKind')} value={header.source_kind} />
          {header.model && <MetaItem label={t('eval.modelName')} value={header.model} />}
          {header.phase && <MetaItem label={t('perf.report.phase')} value={header.phase} />}
          {header.statistics_version && (
            <MetaItem label={t('perf.report.statisticsVersion')} value={header.statistics_version} />
          )}
          <MetaItem
            label={t('perf.report.summary')}
            value={t('perf.report.counts', {
              planned: counts.planned_runs ?? 0,
              observed: counts.observed_requests ?? 0,
              successful: counts.successful_requests ?? 0,
            })}
          />
        </dl>

        {header.groups && header.groups.length > 1 && (
          <label className="flex flex-wrap items-center gap-2 type-body-xs text-[var(--text-muted)]">
            {t('perf.report.groupFilter')}
            <select
              className="w-full min-w-0 rounded-[var(--radius-sm)] border border-[var(--border-md)] bg-[var(--bg-card)] px-2 py-1 text-xs text-[var(--text)] sm:w-auto sm:max-w-[520px]"
              value={group}
              onChange={(event) => {
                setGroup(event.target.value)
                setOffset(0)
              }}
            >
              <option value="">{t('perf.report.allGroups')}</option>
              {header.groups.map((item) => (
                <option key={item.group_key} value={item.group_key}>
                  {groupLabel(item.dimensions)} · {item.run_count}
                </option>
              ))}
            </select>
          </label>
        )}

        {!requestsAvailable && (
          <p className="type-body-xs text-[var(--text-muted)]">{t('perf.report.capability.requests')}</p>
        )}
        {!supported(capabilities.token_usage) && (
          <p className="type-body-xs text-[var(--text-muted)]">{t('perf.report.capability.tokens')}</p>
        )}
        {!supported(capabilities.client_metrics) && (
          <p className="type-body-xs text-[var(--text-muted)]">{t('perf.report.capability.clientMetrics')}</p>
        )}
      </section>

      {runs.error && <ErrorAlert>{runs.error}</ErrorAlert>}

      <Tabs
        tabs={[
          { key: 'table', labelKey: 'perf.report.tabTable', panelId: 'perf-report-table' },
          { key: 'charts', labelKey: 'perf.report.tabCharts', panelId: 'perf-report-charts' },
          ...(nodesAvailable
            ? [{ key: 'nodes', labelKey: 'perf.report.tabNodes', panelId: 'perf-report-nodes' }]
            : []),
          { key: 'requests', labelKey: 'perf.report.tabRequests', panelId: 'perf-report-requests' },
        ]}
        activeKey={tab}
        onChange={(key) => setTab(key as ViewTab)}
        panels={panels}
        keepMounted
      />
    </div>
  )
}
