import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useLocale } from '@/contexts/LocaleContext'
import { useAsyncResource } from '@/hooks/useAsyncResource'
import { useQueryParams } from '@/hooks/useQueryParams'
import {
  getPerfPlanArchive,
  getPerfPlanArchiveAt,
  getPerfPlanRunRequests,
  perfPlanReportUrl,
} from '@/api/perfPlans'
import type { PerfPlanArchiveResponse, PerfPlanRunView } from '@/api/generated/contracts'
import { formatRatioDelta, formatRatioTolerance, formatReuseRatio } from '@/domain/perf/perfPlan'
import Badge from '@/components/ui/Badge'
import Breadcrumb from '@/components/ui/Breadcrumb'
import Card from '@/components/ui/Card'
import ErrorAlert from '@/components/ui/ErrorAlert'
import Skeleton from '@/components/ui/Skeleton'

const PAGE_SIZE = 100

const STATE_VARIANT: Record<string, 'success' | 'danger' | 'warning' | 'default'> = {
  completed: 'success',
  not_started: 'default',
  running: 'default',
  stopped: 'warning',
  error: 'danger',
  lost: 'danger',
  queued: 'default',
  reserved: 'default',
}

function qualityVariant(quality: string | null | undefined): 'success' | 'warning' | 'danger' | 'default' {
  if (quality === 'complete') return 'success'
  if (quality === 'partial') return 'warning'
  if (quality === 'invalid') return 'danger'
  return 'default'
}

function runDimensions(run: PerfPlanRunView): string {
  if (run.workload_kind === 'aiperf_synthetic') {
    const shared = run.shared_system_tokens ?? 'unavailable'
    const unique = run.unique_user_tokens ?? 'unavailable'
    return `${run.declared_total_input_tokens ?? 'unavailable'} (${shared} + ${unique})`
  }
  return String(run.input_tokens ?? 'unavailable')
}

const CACHE_STATUS_KEY: Record<string, string> = {
  verified: 'perf.plan.cacheVerified',
  mismatch: 'perf.plan.cacheMismatch',
  unavailable: 'perf.plan.cacheUnavailable',
  not_applicable: 'perf.plan.cacheNotApplicable',
}

function CacheVerificationCell({ verification }: { verification: PerfPlanRunView['cache_verification'] }) {
  const { t } = useLocale()
  if (!verification) return <span>{t('perf.plan.cacheNotRecorded')}</span>
  const variant: 'success' | 'warning' | 'default' =
    verification.status === 'verified' ? 'success' : verification.status === 'mismatch' ? 'warning' : 'default'
  return (
    <div className="space-y-0.5">
      <Badge variant={variant}>{t(CACHE_STATUS_KEY[verification.status] ?? 'perf.plan.cacheUnavailable')}</Badge>
      <div>
        {formatReuseRatio(verification.declared_ratio)} → {formatReuseRatio(verification.observed_ratio)} (
        {formatRatioDelta(verification.delta)})
      </div>
      <div>
        {formatRatioTolerance(verification.tolerance)} ·{' '}
        {verification.required ? t('perf.plan.cacheRequired') : t('perf.plan.cacheOptional')}
      </div>
    </div>
  )
}

/** One planned run's request sample, loaded only when the row is expanded. */
function RunRequests({ rootPath, path, runId }: { rootPath: string; path: string; runId: string }) {
  const { t } = useLocale()
  const [open, setOpen] = useState(false)
  const resource = useAsyncResource(
    (signal) => getPerfPlanRunRequests(rootPath, path, runId, { offset: 0, limit: PAGE_SIZE, signal }),
    [rootPath, path, runId, open],
    { enabled: open, fallbackMessage: t('perf.plan.requestsFailed') },
  )
  if (!open) {
    return (
      <button type="button" className="text-xs underline" onClick={() => setOpen(true)}>
        {t('perf.plan.showRequests')}
      </button>
    )
  }
  if (resource.loading && !resource.data) return <Skeleton className="h-4 w-40" />
  if (resource.error) return <ErrorAlert>{resource.error}</ErrorAlert>
  const rows = resource.data?.requests ?? []
  if (rows.length === 0) return <p className="text-xs text-[var(--text-muted)]">{t('perf.plan.noRequests')}</p>
  return (
    <div className="overflow-x-auto">
      <p className="text-xs text-[var(--text-muted)]">
        {t('perf.plan.requestsShown', { shown: rows.length, total: resource.data?.total ?? rows.length })}
      </p>
      <table className="w-full text-left text-xs">
        <thead>
          <tr>
            <th>#</th>
            <th>{t('perf.plan.phase')}</th>
            <th>{t('perf.plan.requestStatus')}</th>
            <th>{t('perf.plan.latency')}</th>
            <th>TTFT</th>
            <th>{t('perf.plan.inputTokens')}</th>
            <th>{t('perf.plan.outputTokens')}</th>
            <th>{t('perf.plan.cachedTokens')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((record) => (
            <tr key={`${record.phase}-${record.index}`}>
              <td>{record.index}</td>
              <td>{record.phase}</td>
              <td>{record.status}</td>
              <td>{record.latency_seconds ?? 'unavailable'}</td>
              <td>{record.ttft_seconds ?? 'unavailable'}</td>
              <td>
                {record.prompt_tokens ?? 'unavailable'} ({record.prompt_tokens_source ?? 'unavailable'})
              </td>
              <td>
                {record.output_tokens ?? 'unavailable'} ({record.output_tokens_source ?? 'unavailable'})
              </td>
              <td>{record.cached_tokens ?? 'unavailable'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Readable report of one portable plan.
 *
 * The requested matrix comes from the normalized plan, the effective identity
 * from the archive and each run's state from the manifest, so a partial run set
 * stays visible instead of being replaced by the first run's numbers. Declared
 * deployment facts are labelled as declared.
 */
export default function PerfPlanPage() {
  const { t } = useLocale()
  const params = useParams()
  const query = useQueryParams()
  const taskId = params.taskId
  const rootPath = query.get('root_path')
  const path = query.get('path')
  const [offset, setOffset] = useState(0)

  const resource = useAsyncResource<PerfPlanArchiveResponse>(
    (signal) =>
      taskId && !path
        ? getPerfPlanArchive(taskId, signal)
        : getPerfPlanArchiveAt(rootPath ?? '', path ?? '', { offset, limit: PAGE_SIZE, signal }),
    [taskId, rootPath, path, offset],
    { enabled: Boolean(taskId || (rootPath && path)), fallbackMessage: t('perf.plan.loadFailed') },
  )

  const archive = resource.data
  const runs = archive?.runs ?? []
  const qualityReasons = archive?.quality_reasons ?? []
  const reportLink = archive && rootPath && path ? perfPlanReportUrl(rootPath, path) : null

  return (
    <div className="space-y-4 p-4">
      <Breadcrumb items={[{ label: t('perf.plan.title'), href: '/performance' }]} />
      <h1 className="text-lg font-semibold">{t('perf.plan.heading')}</h1>
      {resource.loading && !archive && <Skeleton className="h-32 w-full" />}
      {resource.error && <ErrorAlert>{resource.error}</ErrorAlert>}
      {archive && (
        <>
          <Card title={t('perf.plan.summary')}>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs md:grid-cols-4">
              <div>
                <dt className="text-[var(--text-muted)]">{t('perf.plan.state')}</dt>
                <dd>
                  <Badge variant={STATE_VARIANT[archive.state ?? 'queued'] ?? 'default'}>{archive.state}</Badge>
                  {archive.state_reason ? ` ${archive.state_reason}` : ''}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--text-muted)]">{t('perf.plan.quality')}</dt>
                <dd>
                  <Badge variant={qualityVariant(archive.quality)}>{archive.quality ?? 'unavailable'}</Badge>
                </dd>
              </div>
              <div>
                <dt className="text-[var(--text-muted)]">{t('perf.plan.runsCompleted')}</dt>
                <dd>
                  {archive.completed_runs ?? 0}/{archive.total_runs ?? 0}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--text-muted)]">{t('perf.plan.phase')}</dt>
                <dd>
                  {archive.phase ?? t('perf.plan.idle')}
                  {archive.current_run ? ` · ${archive.current_run}` : ''}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--text-muted)]">{t('perf.plan.model')}</dt>
                <dd>{archive.model || 'unavailable'}</dd>
              </div>
              <div>
                <dt className="text-[var(--text-muted)]">{t('perf.plan.workload')}</dt>
                <dd>{archive.workload_semantics ?? 'unavailable'}</dd>
              </div>
              <div>
                <dt className="text-[var(--text-muted)]">{t('perf.plan.planHash')}</dt>
                <dd className="truncate font-mono">{archive.plan_hash?.slice(0, 16) ?? 'unavailable'}</dd>
              </div>
              <div>
                <dt className="text-[var(--text-muted)]">{t('perf.plan.compiler')}</dt>
                <dd>
                  {archive.contract_version ?? '?'} / {archive.compiler_version ?? '?'}
                </dd>
              </div>
            </dl>
            <p className="mt-2 text-xs text-[var(--text-muted)]">{t('perf.plan.basisNote')}</p>
            {qualityReasons.length > 0 && (
              <p className="mt-1 text-xs">{t('perf.plan.qualityReasons')}: {qualityReasons.join(', ')}</p>
            )}
            {reportLink && (
              <a className="mt-2 inline-block text-xs underline" href={reportLink} target="_blank" rel="noreferrer">
                {t('perf.plan.htmlReport')}
              </a>
            )}
          </Card>

          <Card title={t('perf.plan.identity')}>
            {archive.effective ? (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs md:grid-cols-3">
                <div>
                  <dt className="text-[var(--text-muted)]">{t('perf.plan.tokenizerContent')}</dt>
                  <dd className="truncate font-mono">{archive.effective.tokenizer?.content_hash?.slice(0, 16) ?? 'unknown'}</dd>
                </div>
                <div>
                  <dt className="text-[var(--text-muted)]">{t('perf.plan.templateContent')}</dt>
                  <dd className="truncate font-mono">{archive.effective.tokenizer?.template_hash?.slice(0, 16) ?? 'unknown'}</dd>
                </div>
                <div>
                  <dt className="text-[var(--text-muted)]">AIPerf</dt>
                  <dd>{archive.effective.aiperf_version ?? 'unknown'}</dd>
                </div>
                <div>
                  <dt className="text-[var(--text-muted)]">{t('perf.plan.evidence')}</dt>
                  <dd>{archive.effective.deployment_evidence}</dd>
                </div>
                <div>
                  <dt className="text-[var(--text-muted)]">{t('perf.plan.identityVersion')}</dt>
                  <dd>{archive.effective.identity_version}</dd>
                </div>
              </dl>
            ) : (
              <p className="text-xs text-[var(--text-muted)]">{t('perf.plan.noIdentity')}</p>
            )}
            {archive.warmup && (
              <p className="mt-2 text-xs">
                {t('perf.plan.warmup')}: {archive.warmup.status} · {archive.warmup.completed_requests}/
                {archive.warmup.request_count} · C={archive.warmup.concurrency} ·{' '}
                {archive.warmup.total_input_tokens} → {archive.warmup.output_token_limit}
                {archive.warmup.reason ? ` · ${archive.warmup.reason}` : ''}
              </p>
            )}
          </Card>

          <Card title={t('perf.plan.matrix')}>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>{t('perf.plan.run')}</th>
                    <th>ISL / shared + unique</th>
                    <th>OSL</th>
                    <th>C</th>
                    <th>N</th>
                    <th>{t('perf.plan.primer')}</th>
                    <th>{t('perf.plan.repetition')}</th>
                    <th>{t('perf.plan.runStatus')}</th>
                    <th>{t('perf.plan.quality')}</th>
                    <th>{t('perf.plan.cacheRatio')}</th>
                    <th>{t('perf.plan.reason')}</th>
                    <th>{t('perf.plan.requests')}</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.run_id}>
                      <td>{run.order}</td>
                      <td className="font-mono">{run.run_id}</td>
                      <td>{runDimensions(run)}</td>
                      <td>{run.output_token_limit}</td>
                      <td>{run.concurrency}</td>
                      <td>{run.formal_requests}</td>
                      <td>{run.primer_requests}</td>
                      <td>{run.repetition}</td>
                      <td>
                        <Badge variant={STATE_VARIANT[run.status ?? 'not_started'] ?? 'default'}>{run.status}</Badge>
                      </td>
                      <td>{run.quality ?? 'unavailable'}</td>
                      <td>
                        <CacheVerificationCell verification={run.cache_verification} />
                      </td>
                      <td>{run.reason ?? '—'}</td>
                      <td>
                        {run.requests_available && rootPath && path ? (
                          <RunRequests rootPath={rootPath} path={path} runId={run.run_id} />
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-[var(--text-muted)]">{t('perf.plan.cacheNote')}</p>
            {(archive.total_runs ?? 0) > runs.length + (archive.offset ?? 0) && (
              <button type="button" className="mt-2 text-xs underline" onClick={() => setOffset(offset + PAGE_SIZE)}>
                {t('perf.plan.nextPage')}
              </button>
            )}
          </Card>
        </>
      )}
    </div>
  )
}
