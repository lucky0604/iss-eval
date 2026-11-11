import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Copy, ExternalLink, RefreshCw, ScrollText, Square } from 'lucide-react'
import type { ResultCapabilities, TaskDetailResponse } from '@/api/types'
import { createTaskApi } from '@/api/task'
import { getTaskDetail } from '@/api/tasks'
import { isDomainError } from '@/api/errors'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Callout from '@/components/ui/Callout'
import Card from '@/components/ui/Card'
import ConfirmDialog from '@/components/ui/ConfirmDialog'
import JsonViewer from '@/components/ui/JsonViewer'
import LogViewer from '@/components/ui/LogViewer'
import Skeleton from '@/components/ui/Skeleton'
import Tabs from '@/components/ui/Tabs'
import { useLocale } from '@/contexts/LocaleContext'
import { useCopy } from '@/hooks/useCopy'
import { usePolling } from '@/hooks/usePolling'
import { useQueryParams } from '@/hooks/useQueryParams'
import {
  RESULT_TAB_ORDER,
  availabilityLabelKey,
  capabilityState,
  engineLabelKey,
  reasonLabelKey,
  resultTabLabelKey,
  testTypeLabelKey,
  type CapabilityName,
} from '@/domain/perf/capabilities'
import { formatTaskTime, statusBadgeVariant, taskTitle } from '@/domain/tasks/presentation'
import { isActiveStatus, progressKind, progressPercent } from '@/domain/tasks/status'
import { effectiveTaskTime, parseTaskTime, timeBasisLabelKey } from '@/domain/tasks/time'

const CAPABILITY_ORDER: CapabilityName[] = [...RESULT_TAB_ORDER, 'configuration', 'html']
const MAX_LOG_CHARS = 200_000
const LOG_PAGE = 500
const DETAIL_TABS = ['overview', 'logs', 'config', 'subruns'] as const
type DetailTab = (typeof DETAIL_TABS)[number]

function detailTab(value: string | undefined): DetailTab {
  return DETAIL_TABS.includes(value as DetailTab) ? value as DetailTab : 'overview'
}

function availabilityVariant(state: string | undefined): 'default' | 'success' | 'warning' | 'danger' {
  if (state === 'available') return 'success'
  if (state === 'pending') return 'default'
  if (state === 'not_configured' || state === 'unsupported') return 'warning'
  return 'danger'
}

function CapabilityList({ capabilities }: { capabilities: ResultCapabilities | undefined }) {
  const { t } = useLocale()
  return (
    <ul className="space-y-2">
      {CAPABILITY_ORDER.map((name) => {
        const state = capabilityState(capabilities, name)
        return (
          <li key={name} className="flex flex-wrap items-start justify-between gap-2 text-sm">
            <span className="text-[var(--text)]">{t(resultTabLabelKey(name))}</span>
            <span className="flex flex-wrap items-center gap-2 text-right">
              <Badge variant={availabilityVariant(state.availability)}>{t(availabilityLabelKey(state))}</Badge>
              {state.availability !== 'available' && state.reason && (
                <span className="text-xs text-[var(--text-muted)]">{t(reasonLabelKey(state.reason))}</span>
              )}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export default function TaskDetailPage() {
  const { taskId = '' } = useParams<{ taskId: string }>()
  // Remount per task so a poll or log page from the previous task can never
  // settle into the new task's state.
  return <TaskDetailView key={taskId} taskId={taskId} />
}

function TaskDetailView({ taskId }: { taskId: string }) {
  const { t, locale } = useLocale()
  const copy = useCopy()
  const { get, set } = useQueryParams()
  const tab = detailTab(get('tab'))
  const [active, setActive] = useState(true)
  const [confirmStop, setConfirmStop] = useState(false)
  const [stopError, setStopError] = useState('')
  const [logsLoaded, setLogsLoaded] = useState(false)
  const [logText, setLogText] = useState('')
  const [logLine, setLogLine] = useState(0)
  const [logsComplete, setLogsComplete] = useState(false)
  const [follow, setFollow] = useState(true)

  const detailPoll = usePolling<TaskDetailResponse>({
    fn: (signal) => getTaskDetail(taskId, signal),
    enabled: Boolean(taskId) && active,
    interval: 5000,
    onData: (next) => setActive(isActiveStatus(next.status)),
    onError: (_message, error) => {
      // A missing task will not appear by polling; stop rather than retry forever.
      if (isDomainError(error) && error.status === 404) setActive(false)
    },
  })
  const detail = detailPoll.data
  const notFound = Boolean(
    detailPoll.failure && isDomainError(detailPoll.failure) && detailPoll.failure.status === 404,
  )
  const api = useMemo(() => createTaskApi(detail?.kind ?? 'eval'), [detail?.kind])

  usePolling({
    fn: (signal) => api.log(taskId, logLine, LOG_PAGE, signal),
    // Logs are a heavy read: only the logs tab, after an explicit load, polls.
    enabled: Boolean(taskId) && tab === 'logs' && logsLoaded && !logsComplete,
    interval: 3000,
    onData: (data) => {
      if (data.text) {
        setLogText((previous) => {
          const next = previous + data.text
          return next.length > MAX_LOG_CHARS ? next.slice(-MAX_LOG_CHARS) : next
        })
      }
      setLogLine(data.tail_line)
      if (detail && !isActiveStatus(detail.status) && data.tail_line >= data.total_lines) {
        setLogsComplete(true)
      }
    },
  })

  const stop = async () => {
    if (!detail) return
    setConfirmStop(false)
    setStopError('')
    try {
      await api.stop(taskId)
      setActive(true)
      void detailPoll.refresh()
    } catch (error) {
      setStopError(error instanceof Error ? error.message : t('tasks.detail.loadError'))
    }
  }

  if (!taskId) {
    return <Callout variant="danger">{t('tasks.detail.notFound')}</Callout>
  }
  if (notFound) {
    return (
      <div className="space-y-3">
        <Link to="/tasks" className="inline-flex items-center gap-1 text-sm text-[var(--accent)]">
          <ArrowLeft size={14} aria-hidden /> {t('tasks.detail.back')}
        </Link>
        <Callout variant="warning">{t('tasks.detail.notFound')}</Callout>
      </div>
    )
  }
  if (!detail) {
    return detailPoll.error ? (
      <Callout variant="danger">{detailPoll.error}</Callout>
    ) : (
      <Skeleton lines={6} height={32} />
    )
  }

  const kind = progressKind(detail)
  const queued = kind === 'queued'
  const percent = progressPercent(detail)
  const time = effectiveTaskTime(detail)
  const result = detail.result_availability
  const isActive = isActiveStatus(detail.status)
  const configLabel =
    detail.config_completeness === 'full'
      ? t('tasks.detail.configFull')
      : detail.config_completeness === 'summary_only'
        ? t('tasks.detail.configSummary')
        : ''
  const subrunPercent = detail.total_subruns
    ? Math.round(((detail.completed_subruns ?? 0) / detail.total_subruns) * 100)
    : 0

  return (
    <div className="space-y-4">
      <div className="sticky top-[52px] z-10 flex flex-wrap items-start justify-between gap-3 bg-[var(--bg)] pb-2">
        <div className="min-w-0">
          <Link to="/tasks" className="inline-flex items-center gap-1 text-sm text-[var(--accent)]">
            <ArrowLeft size={14} aria-hidden /> {t('tasks.detail.back')}
          </Link>
          <h1 className="mt-1 break-words text-xl font-semibold text-[var(--text)]">{taskTitle(detail)}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <Badge>{t(detail.kind === 'eval' ? 'tasks.detail.kindEval' : 'tasks.detail.kindPerf')}</Badge>
            {detail.kind === 'perf' && <Badge>{t(testTypeLabelKey(detail.test_type))}</Badge>}
            {detail.kind === 'perf' && <Badge>{t(engineLabelKey(detail.engine))}</Badge>}
            <Badge variant={statusBadgeVariant(detail.status)}>{t(`tasks.state.${detail.status}`)}</Badge>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void detailPoll.refresh()}>
            <RefreshCw size={14} aria-hidden /> {t('tasks.detail.refresh')}
          </Button>
          {isActive && (
            <Button variant="outline" size="sm" onClick={() => setConfirmStop(true)}>
              <Square size={13} aria-hidden /> {t(queued ? 'tasks.detail.cancelQueued' : 'tasks.detail.stop')}
            </Button>
          )}
        </div>
      </div>

      {isActive && <p className="text-xs text-[var(--text-muted)]">{t('tasks.detail.liveHint')}</p>}

      <Tabs
        tabs={[
          { key: 'overview', labelKey: 'tasks.detail.tabs.overview', panelId: 'detail-overview' },
          { key: 'logs', labelKey: 'tasks.detail.tabs.logs', panelId: 'detail-logs' },
          { key: 'config', labelKey: 'tasks.detail.tabs.config', panelId: 'detail-config' },
          { key: 'subruns', labelKey: 'tasks.detail.tabs.subruns', panelId: 'detail-subruns' },
        ]}
        activeKey={tab}
        onChange={(key) => set('tab', key === 'overview' ? undefined : key)}
        panels={{
          'detail-overview': (
            <div className="space-y-4">
      <Card title={t('tasks.detail.title')}>
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt className="text-xs uppercase tracking-wider text-[var(--text-muted)]">{t('tasks.detail.taskId')}</dt>
            <dd className="mt-0.5 flex items-center gap-2 break-all font-mono text-xs">
              {detail.task_id}
              <button
                type="button"
                className="rounded px-1.5 py-0.5 text-[var(--accent)]"
                aria-label={t('tasks.detail.copyId')}
                onClick={() => copy.copy(detail.task_id)}
              >
                <Copy size={12} aria-hidden />
              </button>
              {copy.copied && <span className="text-[var(--accent)]">{t('tasks.detail.copied')}</span>}
            </dd>
          </div>
          {detail.model && (
            <div>
              <dt className="text-xs uppercase tracking-wider text-[var(--text-muted)]">{t('tasks.detail.model')}</dt>
              <dd className="mt-0.5 break-all">{detail.model}</dd>
            </div>
          )}
          {detail.endpoint && (
            <div>
              <dt className="text-xs uppercase tracking-wider text-[var(--text-muted)]">{t('tasks.detail.endpoint')}</dt>
              <dd className="mt-0.5 break-all">{detail.endpoint}</dd>
            </div>
          )}
          <div>
            <dt className="text-xs uppercase tracking-wider text-[var(--text-muted)]">{t('tasks.detail.progress')}</dt>
            <dd className="mt-0.5">
              {kind === 'queued'
                ? detail.queue_position
                  ? t('tasks.center.list.queue', { position: detail.queue_position })
                  : t('tasks.state.queued')
                : kind === 'determinate'
                  ? `${Math.round(percent ?? 0)}%`
                  : detail.phase
                    ? t(`tasks.phases.${detail.phase}`)
                    : t(`tasks.state.${detail.status}`)}
              {kind === 'determinate' && (
                <div className="mt-1 h-1.5 w-full max-w-[12rem] overflow-hidden rounded-full bg-[var(--bg-deep)]">
                  <div
                    className="h-full rounded-full bg-[var(--accent)]"
                    style={{ width: `${percent ?? 0}%` }}
                    role="progressbar"
                    aria-valuenow={percent ?? 0}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  />
                </div>
              )}
            </dd>
          </div>
          {detail.quality && (
            <div>
              <dt className="text-xs uppercase tracking-wider text-[var(--text-muted)]">{t('tasks.detail.quality')}</dt>
              <dd className="mt-0.5">{t(`tasks.quality.${detail.quality}`)}</dd>
            </div>
          )}
          {detail.started_at && (
            <div>
              <dt className="text-xs uppercase tracking-wider text-[var(--text-muted)]">{t('tasks.detail.startedAt')}</dt>
              <dd className="mt-0.5">
                <time dateTime={detail.started_at}>{formatTaskTime(parseTaskTime(detail.started_at)?.ms ?? null, locale)}</time>
              </dd>
            </div>
          )}
          {(detail.finished_at || detail.enqueued_at) && (
            <div>
              <dt className="text-xs uppercase tracking-wider text-[var(--text-muted)]">
                {detail.finished_at ? t('tasks.detail.finishedAt') : t('tasks.detail.enqueuedAt')}
              </dt>
              <dd className="mt-0.5" title={t(timeBasisLabelKey(time?.basis))}>
                {formatTaskTime(parseTaskTime(detail.finished_at ?? detail.enqueued_at)?.ms ?? null, locale)}
              </dd>
            </div>
          )}
        </dl>
        {time?.basis === 'legacy' && (
          <p className="mt-2 text-xs text-[var(--text-muted)]">{t(timeBasisLabelKey('legacy'))}</p>
        )}
        {detail.error && (
          <Callout variant="danger" className="mt-3">
            <span className="font-medium">{t('tasks.detail.error')}: </span>
            {detail.error}
          </Callout>
        )}
        {stopError && (
          <Callout variant="danger" className="mt-3">
            {stopError}
          </Callout>
        )}
      </Card>

      <Card title={t('tasks.detail.result')}>
        {result?.availability === 'available' ? (
          <div className="flex flex-wrap items-center gap-2">
            {detail.report_url && (
              <a href={detail.report_url}>
                <Button size="sm">
                  <ExternalLink size={13} aria-hidden /> {t('tasks.detail.openReport')}
                </Button>
              </a>
            )}
            {detail.html_report_url && (
              <a href={detail.html_report_url} target="_blank" rel="noreferrer">
                <Button variant="outline" size="sm">
                  <ExternalLink size={13} aria-hidden /> {t('tasks.detail.openHtml')}
                </Button>
              </a>
            )}
            {detail.test_type === 'cache_experiment' && (
              <Link to={`/perf-experiment/${encodeURIComponent(detail.task_id)}`}>
                <Button variant="outline" size="sm">
                  {t('tasks.detail.openExperiment')}
                </Button>
              </Link>
            )}
          </div>
        ) : result?.availability === 'pending' ? (
          <Callout variant="info">{t('tasks.detail.pendingResult')}</Callout>
        ) : (
          <Callout variant="warning">
            {result?.reason === 'execution_failed'
              ? t('tasks.detail.executionFailed')
              : result?.reason === 'archive_missing'
                ? t('tasks.detail.missingResult')
                : t(reasonLabelKey(result?.reason))}
          </Callout>
        )}
        {result?.availability === 'available' && result.reason && (
          <p className="mt-2 text-xs text-[var(--text-muted)]">{t(reasonLabelKey(result.reason))}</p>
        )}
      </Card>

              <Card title={t('tasks.detail.capabilities')}>
                <CapabilityList capabilities={detail.capabilities} />
              </Card>
            </div>
          ),
          'detail-config': (
            <Card
              title={t('tasks.detail.config')}
              badge={configLabel ? <Badge>{configLabel}</Badge> : undefined}
            >
              {detail.config_snapshot ? (
                <JsonViewer value={detail.config_snapshot} maxHeight={320} />
              ) : (
                <p className="text-sm text-[var(--text-muted)]">{t('tasks.detail.configUnavailable')}</p>
              )}
            </Card>
          ),
          'detail-logs': (
      <Card
        title={t('tasks.detail.logs')}
        badge={
          detail.logs_available ? (
            <Badge>{t(follow ? 'tasks.detail.followLogs' : 'tasks.detail.loadLogs')}</Badge>
          ) : undefined
        }
      >
        {!detail.logs_available ? (
          <p className="text-sm text-[var(--text-muted)]">{t('tasks.detail.noLogs')}</p>
        ) : !logsLoaded ? (
          <Button size="sm" onClick={() => setLogsLoaded(true)}>
            <ScrollText size={13} aria-hidden /> {t('tasks.detail.loadLogs')}
          </Button>
        ) : (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-3 text-xs">
              <label className="flex items-center gap-1.5">
                <input type="checkbox" checked={follow} onChange={(event) => setFollow(event.target.checked)} />
                {t('tasks.detail.followLogs')}
              </label>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setLogsComplete(false)
                  setLogsLoaded(true)
                  void detailPoll.refresh()
                }}
              >
                <RefreshCw size={12} aria-hidden /> {t('tasks.detail.reloadLogs')}
              </Button>
              {logText.length >= MAX_LOG_CHARS && (
                <span className="text-[var(--text-muted)]">{t('tasks.detail.logsTruncated')}</span>
              )}
            </div>
            <LogViewer content={logText} follow={follow} maxHeight="420px" />
          </div>
        )}
      </Card>
          ),
          'detail-subruns': (
            <Card title={t('tasks.detail.tabs.subruns')}>
              {detail.total_subruns != null ? (
                <div className="space-y-3 text-sm">
                  <p className="tabular-nums">
                    {detail.completed_subruns ?? 0} / {detail.total_subruns}
                  </p>
                  <div className="h-1.5 w-full max-w-[16rem] overflow-hidden rounded-full bg-[var(--bg-deep)]">
                    <div
                      className="h-full rounded-full bg-[var(--accent)]"
                      style={{ width: `${subrunPercent}%` }}
                      role="progressbar"
                      aria-valuenow={detail.completed_subruns ?? 0}
                      aria-valuemin={0}
                      aria-valuemax={detail.total_subruns}
                    />
                  </div>
                  {detail.test_type === 'cache_experiment' && (
                    <Link
                      className="inline-flex items-center gap-1 text-[var(--accent)]"
                      to={`/perf-experiment/${encodeURIComponent(detail.task_id)}`}
                    >
                      <ExternalLink size={13} aria-hidden /> {t('tasks.detail.openExperiment')}
                    </Link>
                  )}
                </div>
              ) : (
                <p className="text-sm text-[var(--text-muted)]">{t('tasks.detail.noSubruns')}</p>
              )}
            </Card>
          ),
        }}
      />

      <ConfirmDialog
        open={confirmStop}
        title={t(queued ? 'tasks.detail.cancelQueuedTitle' : 'tasks.detail.confirmStopTitle')}
        message={t(queued ? 'tasks.detail.cancelQueuedHint' : 'tasks.detail.stopHint')}
        confirmLabel={t(queued ? 'tasks.detail.cancelQueued' : 'tasks.detail.confirmStopAction')}
        cancelLabel={t('tasks.library.cancel')}
        danger
        onConfirm={() => void stop()}
        onCancel={() => setConfirmStop(false)}
      />
    </div>
  )
}
