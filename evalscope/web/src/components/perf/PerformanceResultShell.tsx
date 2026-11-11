/**
 * Shared shell for one performance result, whatever produced it.
 *
 * Native EvalScope runs, AIPerf single runs and cache experiments have their own
 * analysis bodies, but they share one identity, quality and capability
 * vocabulary. Keeping the shell here is what stops AIPerf from growing a second
 * product surface inside the detail pages.
 */
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, Link2 } from 'lucide-react'

import type { AvailabilityState } from '@/api/types'
import Badge from '@/components/ui/Badge'
import Breadcrumb from '@/components/ui/Breadcrumb'
import LabelledField from '@/components/ui/LabelledField'
import { useLocale } from '@/contexts/LocaleContext'
import { availabilityLabelKey, qualityLabelKey, reasonLabelKey, testTypeLabelKey } from '@/domain/perf/capabilities'
import { statusLabelKey } from '@/domain/tasks/status'

const STATUS_VARIANTS: Record<string, 'success' | 'warning' | 'danger' | 'default'> = {
  completed: 'success',
  running: 'warning',
  partial: 'warning',
  stopped: 'default',
  error: 'danger',
  invalid: 'danger',
}

const QUALITY_VARIANTS: Record<string, 'success' | 'warning' | 'danger' | 'default'> = {
  complete: 'success',
  partial: 'warning',
  invalid: 'danger',
}

export interface PerformanceResultMeta {
  label: string
  value: string | null | undefined
}

interface HeaderProps {
  breadcrumbs: Array<{ label: string; href?: string }>
  testType?: string | null
  title: string
  meta: PerformanceResultMeta[]
  detail?: ReactNode
  status?: string | null
  quality?: string | null
  htmlUrl?: string | null
  taskHref?: string | null
  actions?: ReactNode
}

export function PerformanceResultHeader({
  breadcrumbs,
  testType,
  title,
  meta,
  detail,
  status,
  quality,
  htmlUrl,
  taskHref,
  actions,
}: HeaderProps) {
  const { t } = useLocale()
  return (
    <div className="flex flex-col gap-3">
      <Breadcrumb items={breadcrumbs} />
      <div className="flex flex-wrap items-start justify-between gap-4 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-5">
        <div className="flex min-w-0 flex-col gap-1.5">
          <span className="type-caption-mono text-[var(--text-muted)]">{t(testTypeLabelKey(testType))}</span>
          <h1 className="type-title-md break-words text-[var(--text)]">{title}</h1>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {meta.filter((item) => item.value).map((item) => (
              <LabelledField key={item.label} label={item.label} value={item.value ?? ''} />
            ))}
          </div>
          {(status || quality !== undefined) && (
            <div className="flex flex-wrap items-center gap-2">
              {status && (
                <span className="inline-flex items-center gap-1.5">
                  <span className="type-label-xs text-[var(--text-muted)]">{t('perf.result.statusLabel')}</span>
                  <Badge variant={STATUS_VARIANTS[status] ?? 'default'}>{t(statusLabelKey(status))}</Badge>
                </span>
              )}
              {quality !== undefined && (
                <span className="inline-flex items-center gap-1.5">
                  <span className="type-label-xs text-[var(--text-muted)]">{t('perf.result.qualityLabel')}</span>
                  <Badge variant={QUALITY_VARIANTS[quality ?? ''] ?? 'default'}>{t(qualityLabelKey(quality))}</Badge>
                </span>
              )}
            </div>
          )}
          {detail}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {taskHref && (
            <Link
              to={taskHref}
              className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-md)] px-3 py-1.5 text-sm text-[var(--text-muted)] transition-colors hover:bg-[var(--bg-card2)] hover:text-[var(--text)]"
            >
              <Link2 size={14} />
              {t('perf.result.taskLink')}
            </Link>
          )}
          {htmlUrl && (
            <a
              href={htmlUrl}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-md)] px-3 py-1.5 text-sm text-[var(--text-muted)] transition-colors hover:bg-[var(--bg-card2)] hover:text-[var(--text)]"
            >
              <ExternalLink size={14} />
              {t('perf.archive.viewFullHtml')}
            </a>
          )}
          {actions}
        </div>
      </div>
    </div>
  )
}

/** Quality reasons stay visible instead of being folded into the status badge. */
export function PerformanceQualityBanner({
  status,
  quality,
  reasons,
}: {
  status?: string | null
  quality?: string | null
  reasons?: string[]
}) {
  const { t } = useLocale()
  if (!reasons?.length && !status && quality === undefined) return null
  return (
    <div
      className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-card2)] px-4 py-3"
      role="status"
    >
      <p className="type-body-xs text-[var(--text-muted)]">{t('perf.result.qualityNote')}</p>
      {reasons && reasons.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="type-label-xs text-[var(--text-muted)]">{t('perf.result.qualityReasons')}</span>
          {reasons.map((reason) => (
            <Badge key={reason} variant="warning">
              {reason}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

/** Explains why a capability-driven view cannot be rendered for this result. */
export function CapabilityNotice({ state, label }: { state: AvailabilityState; label: string }) {
  const { t } = useLocale()
  return (
    <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-5">
      <p className="type-body-sm text-[var(--text)]">{t('perf.result.unavailableSurface')}</p>
      <p className="mt-1 type-body-xs text-[var(--text-muted)]">
        {label}: {t(availabilityLabelKey(state))}
        {state.reason ? ` · ${t(reasonLabelKey(state.reason))}` : ''}
      </p>
    </div>
  )
}
