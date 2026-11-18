import { useState, type FormEvent } from 'react'
import { importPerfArtifact } from '@/api/perf'
import type { PerfImportResponse } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import Button from '@/components/ui/Button'
import Field from '@/components/ui/Field'
import { FORM_INPUT_CLASS } from '@/components/ui/formStyles'

type LoadMode = '' | 'closed_loop' | 'open_loop'

/** Import one AIPerf run, or a whole batch directory, without re-running the load test. */
export default function PerfArtifactImportPanel({ onImported }: { onImported: () => void }) {
  const { t } = useLocale()
  const [rawDir, setRawDir] = useState('')
  const [model, setModel] = useState('')
  const [loadMode, setLoadMode] = useState<LoadMode>('')
  const [concurrency, setConcurrency] = useState('')
  const [rate, setRate] = useState('')
  const [outputTokens, setOutputTokens] = useState('')
  const [taskId, setTaskId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<PerfImportResponse | null>(null)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const response = await importPerfArtifact({
        raw_dir: rawDir.trim(),
        ...(model.trim() ? { model: model.trim() } : {}),
        ...(loadMode ? { load_mode: loadMode } : {}),
        ...(loadMode === 'closed_loop' && concurrency.trim() ? { concurrency: Number(concurrency) } : {}),
        ...(loadMode === 'open_loop' && rate.trim() ? { rate: Number(rate) } : {}),
        ...(outputTokens.trim() ? { output_token_limit: Number(outputTokens) } : {}),
        ...(taskId.trim() ? { task_id: taskId.trim() } : {}),
      })
      setResult(response)
      onImported()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const imported = result?.imported ?? []
  const failed = result?.failed ?? []

  return (
    <details className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
      <summary className="cursor-pointer text-sm font-semibold">{t('perf.archive.importTitle')}</summary>
      <form className="mt-3 space-y-3" onSubmit={(event) => void submit(event)}>
        <p className="text-xs text-[var(--text-muted)]">{t('perf.archive.importHint')}</p>
        <Field id="perf-import-raw-dir" name="rawDir" labelKey="perf.archive.importRawDir">
          {(aria) => (
            <input {...aria} required className={FORM_INPUT_CLASS} value={rawDir}
              placeholder="/data/jobs/.../aiperf_results_20261113_030736"
              onChange={(event) => setRawDir(event.target.value)} />
          )}
        </Field>
        <div className="grid gap-3 md:grid-cols-2">
          <Field id="perf-import-model" name="model" labelKey="perf.archive.importModel">
            {(aria) => (
              <input {...aria} className={FORM_INPUT_CLASS} value={model}
                onChange={(event) => setModel(event.target.value)} />
            )}
          </Field>
          <Field id="perf-import-load-mode" name="loadMode" labelKey="perf.archive.importLoadMode">
            {(aria) => (
              <select {...aria} className={FORM_INPUT_CLASS} value={loadMode}
                onChange={(event) => setLoadMode(event.target.value as LoadMode)}>
                <option value="">{t('perf.archive.importAuto')}</option>
                <option value="closed_loop">{t('perf.archive.closedLoop')}</option>
                <option value="open_loop">{t('tasks.aiperf.open')}</option>
              </select>
            )}
          </Field>
          {loadMode === 'closed_loop' &&
            <Field id="perf-import-concurrency" name="concurrency" labelKey="perf.archive.importConcurrency">
              {(aria) => (
                <input {...aria} type="number" min={1} className={FORM_INPUT_CLASS} value={concurrency}
                  onChange={(event) => setConcurrency(event.target.value)} />
              )}
            </Field>}
          {loadMode === 'open_loop' &&
            <Field id="perf-import-rate" name="rate" labelKey="perf.archive.importRate">
              {(aria) => (
                <input {...aria} type="number" min={0.001} step="any" className={FORM_INPUT_CLASS} value={rate}
                  onChange={(event) => setRate(event.target.value)} />
              )}
            </Field>}
          <Field id="perf-import-output-tokens" name="outputTokens" labelKey="perf.archive.importOutputTokens">
            {(aria) => (
              <input {...aria} type="number" min={1} className={FORM_INPUT_CLASS} value={outputTokens}
                onChange={(event) => setOutputTokens(event.target.value)} />
            )}
          </Field>
          <Field id="perf-import-task-id" name="taskId" labelKey="perf.archive.importTaskId">
            {(aria) => (
              <input {...aria} className={FORM_INPUT_CLASS} value={taskId}
                onChange={(event) => setTaskId(event.target.value)} />
            )}
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="outline" disabled={busy}>{t('perf.archive.importSubmit')}</Button>
          {busy && <span className="text-xs text-[var(--text-muted)]">{t('common.loading')}</span>}
        </div>
        {result && <div role="status" className="space-y-1 text-sm">
          <p>{t('perf.archive.importSuccess', { count: imported.length, failed: failed.length })}</p>
          <ul className="space-y-1 text-xs">
            {imported.map((run) => (
              <li key={run.task_id}>
                <code>{run.path}</code>
                <span className="text-[var(--text-muted)]">
                  {' · '}{run.model}{' · '}{run.concurrency ?? run.rate ?? '-'}{' · '}{run.quality}
                  {run.inferred_fields?.length
                    ? ` · ${t('perf.archive.importInferred')}: ${run.inferred_fields.join(', ')}`
                    : ''}
                </span>
              </li>
            ))}
            {failed.map((item) => (
              <li key={`${item.source_dir}:${item.error}`} className="text-[var(--danger)]">
                {item.source_dir}: {item.error}
              </li>
            ))}
          </ul>
        </div>}
        {error && <p role="alert" className="text-sm text-[var(--danger)]">{t('perf.archive.importFailed')}: {error}</p>}
      </form>
    </details>
  )
}
