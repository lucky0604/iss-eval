import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ArrowRight, Clock, Plus, RefreshCw } from 'lucide-react'
import type { TaskListResponse, TaskSummaryItem, TaskSummaryResponse } from '@/api/types'
import { getTaskSummary, listTasks } from '@/api/tasks'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Callout from '@/components/ui/Callout'
import Card from '@/components/ui/Card'
import SearchInput from '@/components/ui/SearchInput'
import Select from '@/components/ui/Select'
import Skeleton from '@/components/ui/Skeleton'
import { useLocale } from '@/contexts/LocaleContext'
import {
  defaultTaskFilters,
  parseTaskFilters,
  taskFiltersKey,
  updateTaskParams,
  type TaskListFilters,
  type TaskView,
} from '@/domain/tasks/query'
import { progressKind, progressPercent } from '@/domain/tasks/status'
import { effectiveTaskTime, timeBasisLabelKey } from '@/domain/tasks/time'
import { formatTaskTime, statusBadgeVariant, taskTitle } from '@/domain/tasks/presentation'
import { availabilityLabelKey, engineLabelKey, testTypeLabelKey } from '@/domain/perf/capabilities'

const POLL_INTERVAL = 5000

interface SummaryRange {
  from: string
  to: string
  /** `today` when no explicit range was chosen, so the card says what it counts. */
  basis: 'today' | 'range'
}

function summaryRange(filters: TaskListFilters): SummaryRange {
  if (filters.from || filters.to) {
    return { from: filters.from, to: filters.to, basis: 'range' }
  }
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const end = new Date(start)
  end.setDate(end.getDate() + 1)
  return { from: start.toISOString(), to: end.toISOString(), basis: 'today' }
}

function TaskRow({ item }: { item: TaskSummaryItem }) {
  const { t, locale } = useLocale()
  const kind = progressKind(item)
  const percent = progressPercent(item)
  const time = effectiveTaskTime(item)
  const result = item.result_availability
  const active = kind === 'queued' || kind === 'determinate' || kind === 'indeterminate'

  return (
    <li className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-3 transition-colors hover:border-[var(--accent)]/40">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={`/tasks/${encodeURIComponent(item.task_id)}`}
              className="truncate text-sm font-medium text-[var(--text)] hover:text-[var(--accent)]"
              title={taskTitle(item)}
            >
              {taskTitle(item)}
            </Link>
            <Badge>{t(item.kind === 'eval' ? 'tasks.detail.kindEval' : 'tasks.detail.kindPerf')}</Badge>
            {item.kind === 'perf' && (
              <Badge>{t(testTypeLabelKey(item.test_type))}</Badge>
            )}
            {item.engine && <Badge>{t(engineLabelKey(item.engine))}</Badge>}
          </div>
          <p className="mt-1 truncate text-xs text-[var(--text-muted)]">
            <span className="font-mono">{item.task_id}</span>
            {item.endpoint && <span> · {item.endpoint}</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <Badge variant={statusBadgeVariant(item.status)}>{t(`tasks.state.${item.status}`)}</Badge>
          {item.quality === 'partial' && <Badge variant="warning">{t('tasks.quality.partial')}</Badge>}
          <div className="min-w-[7rem]">
            {kind === 'queued' ? (
              <span className="text-[var(--text-muted)]">
                {item.queue_position
                  ? t('tasks.center.list.queue', { position: item.queue_position })
                  : t('tasks.state.queued')}
              </span>
            ) : kind === 'determinate' ? (
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-20 overflow-hidden rounded-full bg-[var(--bg-deep)]">
                  <div
                    className="h-full rounded-full bg-[var(--accent)]"
                    style={{ width: `${percent ?? 0}%` }}
                    role="progressbar"
                    aria-valuenow={percent ?? 0}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  />
                </div>
                <span className="tabular-nums text-[var(--text-muted)]">{Math.round(percent ?? 0)}%</span>
              </div>
            ) : null}
          </div>
          <div>
            {result?.availability === 'available' ? (
              <Badge variant="success">{t(availabilityLabelKey(result))}</Badge>
            ) : result ? (
              <Badge variant={result.availability === 'pending' ? 'default' : 'warning'}>
                {t(availabilityLabelKey(result))}
              </Badge>
            ) : (
              <span className="text-[var(--text-dim)]">{t('tasks.center.list.noResult')}</span>
            )}
          </div>
          <span
            className="flex items-center gap-1 text-[var(--text-muted)]"
            title={t(timeBasisLabelKey(time?.basis))}
          >
            <Clock size={12} aria-hidden />
            {time ? formatTaskTime(time.ms, locale) : t('tasks.center.list.unknownTime')}
          </span>
          <Link
            to={`/tasks/${encodeURIComponent(item.task_id)}`}
            className="coarse-target inline-flex items-center gap-1 rounded px-2 py-1 text-[var(--accent)] hover:bg-[var(--accent-dim)]"
            aria-label={`${t('tasks.center.list.view')}: ${taskTitle(item)}`}
          >
            {t('tasks.center.list.view')} <ArrowRight size={12} aria-hidden />
          </Link>
        </div>
      </div>
      {item.status === 'unknown' && (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-[var(--yellow)]">
          <AlertTriangle size={12} aria-hidden /> {t('tasks.center.list.stale')}
        </p>
      )}
      {active && item.error && (
        <p className="mt-2 break-words text-xs text-[var(--danger)]">{item.error}</p>
      )}
    </li>
  )
}

function SummaryCard({
  label,
  value,
  hint,
  onClick,
  active,
}: {
  label: string
  value: number | null
  hint?: string
  onClick?: () => void
  active?: boolean
}) {
  const body = (
    <>
      <span className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{label}</span>
      <span className="text-2xl font-semibold tabular-nums text-[var(--text)]">{value ?? '—'}</span>
      {hint && <span className="text-xs text-[var(--text-dim)]">{hint}</span>}
    </>
  )
  const className = `flex flex-col gap-1 rounded-[var(--radius)] border p-3 text-left transition-colors ${
    active
      ? 'border-[var(--accent)] bg-[var(--accent-dim)]'
      : 'border-[var(--border)] bg-[var(--bg-card)]'
  }`
  return onClick ? (
    <button type="button" onClick={onClick} className={`${className} coarse-target cursor-pointer`}>
      {body}
    </button>
  ) : (
    <div className={className}>{body}</div>
  )
}

export default function TaskCenterPage() {
  const { t } = useLocale()
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = useMemo(() => parseTaskFilters(searchParams), [searchParams])
  const queryKey = taskFiltersKey(filters)
  const [list, setList] = useState<TaskListResponse | null>(null)
  const [summary, setSummary] = useState<TaskSummaryResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState(filters.q)
  const [syncedQuery, setSyncedQuery] = useState(filters.q)
  const requestRef = useRef(0)

  // Follow URL changes (back/forward, a shared link) without an effect: React
  // allows adjusting derived state during render and re-runs it immediately.
  if (filters.q !== syncedQuery) {
    setSyncedQuery(filters.q)
    setSearch(filters.q)
  }

  const apply = useCallback(
    (patch: Partial<TaskListFilters>) => {
      setSearchParams(updateTaskParams(searchParams, patch), { replace: true })
    },
    [searchParams, setSearchParams],
  )

  // Typing reaches the URL after a short pause. The list, summary and polling
  // all follow the URL, so applying on every keystroke would refetch per
  // character and flood the history stack.
  useEffect(() => {
    const next = search.trim()
    if (next === filters.q) return
    const timer = setTimeout(() => apply({ q: next, offset: 0 }), 300)
    return () => clearTimeout(timer)
  }, [search, filters.q, apply])

  const read = useCallback(
    async (background: boolean, signal: AbortSignal) => {
      const requestId = ++requestRef.current
      if (!background) setLoading(true)
      try {
        const [nextList, nextSummary] = await Promise.all([
          listTasks(filters, signal),
          getTaskSummary(summaryRange(filters), signal),
        ])
        if (requestId !== requestRef.current || signal.aborted) return
        setList(nextList)
        setSummary(nextSummary)
        setError('')
      } catch (e) {
        if (signal.aborted || requestId !== requestRef.current) return
        setError(e instanceof Error ? e.message : t('tasks.center.list.error'))
        if (!background) setList(null)
      } finally {
        if (!signal.aborted && requestId === requestRef.current) setLoading(false)
      }
    },
    // `filters` is fully represented by `queryKey`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [queryKey, t],
  )

  useEffect(() => {
    const controller = new AbortController()
    // Kick the first read from a task, not the effect body, so the effect only
    // subscribes and never renders cascading state synchronously.
    const kickoff = setTimeout(() => void read(true, controller.signal), 0)
    const timer = setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState !== 'hidden') {
        void read(true, controller.signal)
      }
    }, POLL_INTERVAL)
    const onVisible = () => {
      if (document.visibilityState === 'visible') void read(true, controller.signal)
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      controller.abort()
      clearTimeout(kickoff)
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [read])

  const pageStart = list && list.total > 0 ? list.offset + 1 : 0
  const pageEnd = list ? Math.min(list.offset + list.items.length, list.total) : 0
  const range = summaryRange(filters)

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-[var(--text)]">{t('tasks.center.title')}</h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--text-muted)]">{t('tasks.center.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void read(false, new AbortController().signal)}>
            <RefreshCw size={14} aria-hidden /> {t('tasks.center.refresh')}
          </Button>
          <Link to="/tasks/new">
            <Button size="sm">
              <Plus size={14} aria-hidden /> {t('tasks.center.newTask')}
            </Button>
          </Link>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <SummaryCard
          label={t('tasks.center.summary.executing')}
          value={summary?.executing ?? null}
          onClick={() => apply({ view: 'active', offset: 0 })}
          active={filters.view === 'active'}
        />
        <SummaryCard
          label={t('tasks.center.summary.queued')}
          value={summary?.queued ?? null}
          onClick={() => apply({ view: 'active', statuses: ['queued'], offset: 0 })}
          active={filters.view === 'active' && filters.statuses.includes('queued')}
        />
        <SummaryCard
          label={t('tasks.center.summary.attention')}
          value={summary?.attention ?? null}
          hint={t('tasks.center.attentionHint')}
          onClick={() => apply({ view: 'attention', statuses: [], offset: 0 })}
          active={filters.view === 'attention'}
        />
        <SummaryCard
          label={t(
            range.basis === 'today'
              ? 'tasks.center.summary.completedToday'
              : 'tasks.center.summary.completedInRange',
          )}
          value={summary?.completed_in_range ?? null}
        />
      </div>

      {summary && !summary.queue.reachable && (
        <Callout variant="warning">
          {t('tasks.center.list.unavailable')}
          {summary.queue.reason ? ` ${summary.queue.reason}` : ''}
        </Callout>
      )}

      <Card>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Select
            label={t('tasks.center.filters.view')}
            value={filters.view}
            onChange={(value) => apply({ view: value as TaskView, offset: 0 })}
            options={[
              { value: 'all', label: t('tasks.center.filters.all') },
              { value: 'active', label: t('tasks.center.filters.active') },
              { value: 'attention', label: t('tasks.center.filters.attention') },
              { value: 'history', label: t('tasks.center.filters.history') },
            ]}
          />
          <Select
            label={t('tasks.center.filters.kind')}
            value={filters.kind ?? ''}
            onChange={(value) => apply({ kind: (value || undefined) as TaskListFilters['kind'], offset: 0 })}
            options={[
              { value: '', label: t('tasks.center.filters.anyKind') },
              { value: 'eval', label: t('tasks.detail.kindEval') },
              { value: 'perf', label: t('tasks.detail.kindPerf') },
            ]}
          />
          <Select
            label={t('tasks.center.filters.engine')}
            value={filters.engine ?? ''}
            onChange={(value) => apply({ engine: (value || undefined) as TaskListFilters['engine'], offset: 0 })}
            options={[
              { value: '', label: t('tasks.center.filters.anyEngine') },
              { value: 'evalscope', label: t('tasks.engine.evalscope') },
              { value: 'aiperf', label: t('tasks.engine.aiperf') },
            ]}
          />
          <Select
            label={t('tasks.center.filters.sort')}
            value={filters.sort}
            onChange={(value) => apply({ sort: value === 'queue' ? 'queue' : 'recent', offset: 0 })}
            options={[
              { value: 'recent', label: t('tasks.center.filters.sortRecent') },
              { value: 'queue', label: t('tasks.center.filters.sortQueue') },
            ]}
          />
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex min-w-[12rem] flex-1 flex-col gap-1.5">
            <span className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              {t('tasks.center.filters.search')}
            </span>
            <SearchInput
              value={search}
              onChange={setSearch}
              placeholder={t('tasks.center.filters.searchHint')}
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              {t('tasks.center.filters.from')}
            </span>
            <input
              type="date"
              value={filters.from ? filters.from.slice(0, 10) : ''}
              onChange={(event) => {
                const value = event.target.value
                apply({
                  from: value ? new Date(`${value}T00:00:00`).toISOString() : '',
                  offset: 0,
                })
              }}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-deep)] px-3 py-2 text-sm text-[var(--text)]"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              {t('tasks.center.filters.to')}
            </span>
            <input
              type="date"
              value={filters.to ? filters.to.slice(0, 10) : ''}
              onChange={(event) => {
                const value = event.target.value
                const end = value ? new Date(`${value}T00:00:00`) : null
                end?.setDate(end.getDate() + 1)
                end?.setMilliseconds(-1)
                apply({ to: end ? end.toISOString() : '', offset: 0 })
              }}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-deep)] px-3 py-2 text-sm text-[var(--text)]"
            />
          </label>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setSearch('')
              apply({ ...defaultTaskFilters(), view: filters.view })
            }}
          >
            {t('tasks.center.filters.reset')}
          </Button>
          <Button
            size="sm"
            onClick={() => apply({ q: search.trim(), offset: 0 })}
            disabled={search.trim() === filters.q}
          >
            {t('tasks.center.filters.apply')}
          </Button>
        </div>
      </Card>

      {error && (
        <Callout variant="danger">
          {t('tasks.center.list.error')}
          <span className="block text-xs opacity-80">{error}</span>
          <button
            type="button"
            className="mt-2 rounded px-2 py-1 text-xs underline"
            onClick={() => void read(false, new AbortController().signal)}
          >
            {t('tasks.center.list.retry')}
          </button>
        </Callout>
      )}

      {list && (
        <>
          <div className="space-y-2">
            {list.indexing.state === 'unavailable' && (
              <Callout variant="warning">{t('tasks.center.list.unavailable')}</Callout>
            )}
            {list.indexing.state === 'indexing' && (
              <Callout variant="info">{t('tasks.center.list.indexing')}</Callout>
            )}
            {list.completeness === 'partial' && list.indexing.state !== 'unavailable' && (
              <Callout variant="warning">{t('tasks.center.list.partial')}</Callout>
            )}
            {(list.excluded_without_time ?? 0) > 0 && (
              <p className="text-xs text-[var(--text-muted)]">
                {t('tasks.center.list.excluded', { count: list.excluded_without_time ?? 0 })}
              </p>
            )}
          </div>

          {loading && !list.items.length ? (
            <Skeleton lines={6} height={44} />
          ) : list.items.length === 0 ? (
            <Card>
              <p className="py-6 text-center text-sm text-[var(--text-muted)]">
                {t('tasks.center.list.empty')}
              </p>
            </Card>
          ) : (
            <ul className="space-y-2">
              {list.items.map((item) => (
                <TaskRow key={item.task_id} item={item} />
              ))}
            </ul>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--text-muted)]">
            <span>
              {t('tasks.center.list.showing', { from: pageStart, to: pageEnd, total: list.total })}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={filters.offset <= 0}
                onClick={() => apply({ offset: Math.max(0, filters.offset - filters.limit) })}
              >
                {t('tasks.center.list.previous')}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={filters.offset + filters.limit >= list.total}
                onClick={() => apply({ offset: filters.offset + filters.limit })}
              >
                {t('tasks.center.list.next')}
              </Button>
            </div>
          </div>
        </>
      )}

      {loading && !list && <Skeleton lines={6} height={44} />}
      {!list && !loading && !error && (
        <Card>
          <p className="text-sm text-[var(--text-muted)]">{t('tasks.center.list.empty')}</p>
        </Card>
      )}
    </div>
  )
}
