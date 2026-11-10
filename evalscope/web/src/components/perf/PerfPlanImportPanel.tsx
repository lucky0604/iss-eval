import { useRef, useState } from 'react'
import { exportPerfPlan, previewPerfPlan, submitPerfPlan, type PlanErrorPayload } from '@/api/perfPlans'
import type { PerfPlanPreviewResponse } from '@/api/generated/contracts'
import { DomainError } from '@/api/errors'
import {
  canSubmitPlan,
  checkPlanFileSize,
  formatTokens,
  matrixRows,
  missingAcknowledgements,
  planExportFileName,
} from '@/domain/perf/perfPlan'
import { useLocale } from '@/contexts/LocaleContext'
import Button from '@/components/ui/Button'
import { FORM_INPUT_CLASS } from '@/components/ui/formStyles'

interface Props {
  disabled?: boolean
  onSubmitted?: (taskId: string) => void
}

/**
 * The submission attempt this panel is currently bound to.
 *
 * One accepted plan keeps one task id: an uncertain network failure must retry
 * the *same* task instead of creating a second model run, and a retry after a
 * definite rejection stays on the same id because the server's reservation is
 * the authority. Only an explicit new submission after success rotates the id.
 */
interface PlanAttempt {
  planHash: string
  taskId: string
  accepted: boolean
}

function createPlanTaskId(): string {
  return `perf_plan_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function errorDiagnostics(error: unknown): PlanErrorPayload | null {
  if (!(error instanceof DomainError)) return null
  const details = (error as DomainError & { details?: PlanErrorPayload }).details
  return details && typeof details === 'object' ? details : null
}

/**
 * Import a portable perf-plan file, preview its compiled matrix, then submit it.
 *
 * The textarea holds the original file text; it is never re-serialized locally
 * so a duplicate key or an unknown field reaches the server unchanged.
 */
export default function PerfPlanImportPanel({ disabled = false, onSubmitted }: Props) {
  const { t } = useLocale()
  const [text, setText] = useState('')
  const [fileName, setFileName] = useState('')
  const [preview, setPreview] = useState<PerfPlanPreviewResponse | null>(null)
  const [acknowledged, setAcknowledged] = useState<string[]>([])
  const [message, setMessage] = useState('')
  const [diagnostics, setDiagnostics] = useState<PlanErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState<PlanAttempt | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)

  const submitState = canSubmitPlan(preview, acknowledged)
  const rows = preview ? matrixRows(preview) : []

  const reset = () => {
    setText('')
    setFileName('')
    setPreview(null)
    setAcknowledged([])
    setMessage('')
    setDiagnostics(null)
    setAttempt(null)
    if (fileInput.current) fileInput.current.value = ''
  }

  const loadFile = async (file: File | undefined) => {
    if (!file) return
    setPreview(null)
    setAcknowledged([])
    setDiagnostics(null)
    const sizeError = checkPlanFileSize(file.size)
    if (sizeError) {
      setMessage(sizeError)
      setText('')
      return
    }
    const content = await file.text()
    setText(content)
    setFileName(file.name)
    setMessage(t('tasks.aiperf.planImport.loaded', { bytes: new TextEncoder().encode(content).length }))
  }

  const runPreview = async () => {
    setBusy(true)
    setDiagnostics(null)
    setMessage('')
    try {
      const result = await previewPerfPlan(text, { acknowledged })
      setPreview(result)
      setAcknowledged((current) => current.filter((code) => (result.requires_acknowledgement ?? []).includes(code)))
    } catch (error) {
      setPreview(null)
      setDiagnostics(errorDiagnostics(error))
      setMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  const runExport = async () => {
    setBusy(true)
    try {
      const exported = await exportPerfPlan(text)
      const link = document.createElement('a')
      link.href = URL.createObjectURL(new Blob([exported.content], { type: 'application/json' }))
      link.download = planExportFileName(preview?.normalized.name)
      link.click()
      URL.revokeObjectURL(link.href)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  /** Reuse the attempt for this plan unless it was already accepted. */
  const planAttempt = (): PlanAttempt => {
    if (attempt && attempt.planHash === preview?.plan_hash && !attempt.accepted) return attempt
    return { planHash: preview?.plan_hash ?? '', taskId: createPlanTaskId(), accepted: false }
  }

  const runSubmit = async () => {
    if (!preview) return
    const current = planAttempt()
    setAttempt(current)
    setBusy(true)
    setDiagnostics(null)
    try {
      const accepted = await submitPerfPlan(text, current.taskId, {
        acknowledged,
        expectedPlanHash: preview.plan_hash,
      })
      setAttempt({ ...current, accepted: true })
      if (accepted.duplicate) {
        setMessage(t('tasks.aiperf.planImport.duplicate', { state: accepted.state }))
      } else {
        setMessage(t('tasks.aiperf.planImport.accepted', { state: accepted.state }))
      }
      onSubmitted?.(accepted.task_id || current.taskId)
    } catch (error) {
      // A definite 4xx rejection is the user's to fix; an unknown outcome keeps
      // the same id so the retry cannot start a second model run.
      const definite = error instanceof DomainError && error.kind === 'http-4xx'
      if (definite) setAttempt(null)
      setDiagnostics(errorDiagnostics(error))
      setMessage(
        definite
          ? (error instanceof Error ? error.message : String(error))
          : t('tasks.aiperf.planImport.uncertain'),
      )
    } finally {
      setBusy(false)
    }
  }

  const toggleAck = (code: string) => {
    setAcknowledged((current) =>
      current.includes(code) ? current.filter((item) => item !== code) : [...current, code],
    )
  }

  const blockedReason = (() => {
    if (submitState.allowed) return 'tasks.aiperf.planImport.ready'
    if (submitState.reason === 'engineUnavailable') return 'tasks.aiperf.planImport.engineUnavailable'
    if (submitState.reason === 'blocked') return 'tasks.aiperf.planImport.blocked'
    if (submitState.reason === 'missingAcknowledgement') return 'tasks.aiperf.planImport.missingAck'
    return 'tasks.aiperf.planImport.noPreview'
  })()

  return (
    <details className="rounded border border-[var(--border)] p-3">
      <summary className="cursor-pointer text-sm font-medium">{t('tasks.aiperf.planImport.title')}</summary>
      <div className="mt-3 space-y-3">
        <p className="text-xs text-[var(--text-muted)]">{t('tasks.aiperf.planImport.hint')}</p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            aria-label={t('tasks.aiperf.planImport.chooseFile')}
            disabled={disabled || busy}
            onChange={(event) => void loadFile(event.target.files?.[0])}
          />
          <Button type="button" variant="outline" disabled={disabled || busy || !text} onClick={() => void runPreview()}>
            {t('tasks.aiperf.planImport.preview')}
          </Button>
          <Button type="button" variant="outline" disabled={disabled || busy || !preview} onClick={() => void runExport()}>
            {t('tasks.aiperf.planImport.export')}
          </Button>
          <Button type="button" disabled={disabled || busy || !submitState.allowed} onClick={() => void runSubmit()}>
            {t('tasks.aiperf.planImport.submit')}
          </Button>
          <Button type="button" variant="outline" disabled={busy || !text} onClick={reset}>
            {t('tasks.aiperf.planImport.clear')}
          </Button>
        </div>
        {fileName && <p className="text-xs text-[var(--text-muted)]">{fileName}</p>}
        <textarea
          className={FORM_INPUT_CLASS}
          rows={6}
          spellCheck={false}
          aria-label={t('tasks.aiperf.planImport.paste')}
          value={text}
          placeholder={t('tasks.aiperf.planImport.paste')}
          disabled={disabled || busy}
          onChange={(event) => {
            setText(event.target.value)
            setPreview(null)
          }}
        />
        <p className="text-sm" role="status">
          {message || (text ? blockedReason : t('tasks.aiperf.planImport.empty'))}
        </p>

        {preview && (
          <>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs md:grid-cols-4">
              <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.planImport.sourceHash')}</dt>
                <dd className="truncate font-mono">{preview.source_hash.slice(0, 12)}</dd></div>
              <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.planImport.planHash')}</dt>
                <dd className="truncate font-mono">{preview.plan_hash.slice(0, 12)}</dd></div>
              <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.planImport.budget')}</dt>
                <dd>{formatTokens(preview.budgets.formal_requests)} + {formatTokens(preview.budgets.warmup_requests)} + {formatTokens(preview.budgets.primer_requests)}</dd></div>
              <div><dt className="text-[var(--text-muted)]">{t('tasks.aiperf.planImport.runs', { shown: rows.length, total: preview.total_runs })}</dt>
                <dd>{preview.workload_semantics}</dd></div>
            </dl>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr><th>#</th><th>run</th><th>{t('tasks.aiperf.planImport.matrix')}</th><th>concurrency</th>
                    <th>{t('tasks.aiperf.planImport.budget')}</th><th>primer</th><th>ratio</th></tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.runId}>
                      <td>{row.order}</td><td className="font-mono">{row.runId}</td><td>{row.input} · {row.structure}</td>
                      <td>{row.concurrency}</td><td>{row.requests}</td><td>{row.primerRequests}</td><td>{row.ratio}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div>
              <h4 className="text-sm font-medium">{t('tasks.aiperf.planImport.diagnostics')}</h4>
              {(preview.diagnostics ?? []).length === 0 ? <p className="text-xs">{t('tasks.aiperf.planImport.noDiagnostics')}</p> : (
                <ul className="space-y-1 text-xs">
                  {(preview.diagnostics ?? []).map((item, index) => (
                    <li key={`${item.code}-${index}`}>
                      <span className="font-mono">[{item.severity}] {item.code}</span>
                      {item.case_id ? ` (${item.case_id})` : ''} — {item.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {(preview.requires_acknowledgement ?? []).length > 0 && (
              <fieldset className="space-y-1">
                <legend className="text-sm font-medium">{t('tasks.aiperf.planImport.acknowledgements')}</legend>
                {(preview.requires_acknowledgement ?? []).map((code) => (
                  <label key={code} className="flex items-center gap-2 text-xs">
                    <input type="checkbox" checked={acknowledged.includes(code)} onChange={() => toggleAck(code)} />
                    <span className="font-mono">{code}</span>
                  </label>
                ))}
                {missingAcknowledgements(preview.requires_acknowledgement ?? [], acknowledged).length > 0 && (
                  <p className="text-xs text-[var(--danger)]">{t('tasks.aiperf.planImport.missingAck')}</p>
                )}
              </fieldset>
            )}

            <div>
              <h4 className="text-sm font-medium">{t('tasks.aiperf.planImport.alignment')}</h4>
              <table className="w-full text-left text-xs">
                <thead>
                  <tr>
                    <th>{t('tasks.aiperf.planImport.dimension')}</th>
                    <th>{t('tasks.aiperf.planImport.status')}</th>
                    <th>{t('tasks.aiperf.planImport.diagnostics')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(preview.alignment.dimensions ?? []).map((dimension) => (
                    <tr key={dimension.dimension}>
                      <td className="font-mono">{dimension.dimension}</td>
                      <td>{dimension.status}</td>
                      <td>{(dimension.reasons ?? []).join('; ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {diagnostics?.diagnostics && diagnostics.diagnostics.length > 0 && (
          <ul className="space-y-1 text-xs">
            {diagnostics.diagnostics.map((item, index) => (
              <li key={`${item.code}-${index}`}>
                <span className="font-mono">[{item.severity}] {item.code}</span> — {item.message}
              </li>
            ))}
          </ul>
        )}
      </div>
    </details>
  )
}
