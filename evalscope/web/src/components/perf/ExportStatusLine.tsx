/**
 * Inline status of the current report export, with the artifacts it published.
 *
 * Downloads are id-addressed links returned by the export record, so a click
 * never re-renders the report and never changes the load-test task.
 */
import { useLocale } from '@/contexts/LocaleContext'
import Button from '@/components/ui/Button'
import type { PerfExportState } from '@/hooks/usePerfExport'

const LINK = 'rounded-[var(--radius-sm)] border border-[var(--border-md)] px-2.5 py-1 text-xs text-[var(--text)] hover:border-[var(--accent)] hover:text-[var(--accent)]'

export default function ExportStatusLine({ state }: { state: PerfExportState }) {
  const { t } = useLocale()
  const { phase, exportId, artifacts, runCount, requestCount, error, warnings } = state

  if (phase === 'idle') return null

  const cancelButton = (exportId || phase === 'starting') && (
    <Button size="sm" variant="ghost" onClick={state.cancel}>
      {t('perf.report.export.cancel')}
    </Button>
  )

  return (
    <div className="flex flex-wrap items-center justify-end gap-2 type-body-xs text-[var(--text-muted)]" aria-live="polite">
      {(phase === 'starting' || phase === 'running') && (
        <>
          <span>{t('perf.report.export.running')}</span>
          {cancelButton}
        </>
      )}

      {phase === 'completed' && (
        <>
          <span>
            {t('perf.report.export.ready', {
              runs: runCount ?? 0,
              requests: requestCount ?? 0,
            })}
          </span>
          {artifacts.map((artifact) => (
            <a key={artifact.name} className={LINK} href={artifact.url} download={artifact.filename}>
              {artifact.filename}
            </a>
          ))}
          <Button size="sm" variant="ghost" onClick={state.reset}>{t('perf.report.export.dismiss')}</Button>
        </>
      )}

      {phase === 'failed' && (
        <>
          <span className="text-[var(--danger)]">
            {error
              ? t('perf.report.export.failed', { message: error })
              : t('perf.report.export.timeout')}
          </span>
          <Button size="sm" variant="outline" onClick={state.retry}>{t('perf.report.export.retry')}</Button>
          <Button size="sm" variant="ghost" onClick={state.reset}>{t('perf.report.export.dismiss')}</Button>
        </>
      )}

      {phase === 'cancelled' && (
        <>
          <span>{t('perf.report.export.cancelled')}</span>
          <Button size="sm" variant="ghost" onClick={state.reset}>{t('perf.report.export.dismiss')}</Button>
        </>
      )}

      {warnings.length > 0 && (
        <span className="w-full text-right text-[var(--warning-text)]" title={warnings.join('\n')}>
          {warnings.join('; ')}
        </span>
      )}
    </div>
  )
}
