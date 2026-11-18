import { useState, type FormEvent } from 'react'
import { importPerfArtifact } from '@/api/perf'
import type { PerfImportResponse } from '@/api/generated/contracts'
import { useLocale } from '@/contexts/LocaleContext'
import Button from '@/components/ui/Button'
import Field from '@/components/ui/Field'
import { FORM_INPUT_CLASS } from '@/components/ui/formStyles'

/** Import a server-local AIPerf artifact directory and make it a report archive. */
export default function PerfArtifactImportPanel({ onImported }: { onImported: () => void }) {
  const { t } = useLocale()
  const [rawDir, setRawDir] = useState('')
  const [model, setModel] = useState('')
  const [loadMode, setLoadMode] = useState<'closed_loop' | 'open_loop'>('closed_loop')
  const [concurrency, setConcurrency] = useState('1')
  const [rate, setRate] = useState('1')
  const [outputTokens, setOutputTokens] = useState('1024')
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
        model: model.trim(),
        load_mode: loadMode,
        ...(loadMode === 'closed_loop' ? { concurrency: Number(concurrency) } : { rate: Number(rate) }),
        output_token_limit: Number(outputTokens),
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

  return (
    <details className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-4">
      <summary className="cursor-pointer text-sm font-semibold">{t('perf.archive.importTitle')}</summary>
      <form className="mt-3 space-y-3" onSubmit={(event) => void submit(event)}>
        <p className="text-xs text-[var(--text-muted)]">{t('perf.archive.importHint')}</p>
        <Field id="perf-import-raw-dir" name="rawDir" labelKey="perf.archive.importRawDir">
          {(aria) => (
            <input {...aria} required className={FORM_INPUT_CLASS} value={rawDir}
              placeholder="/data/hejing/.../aiperf_results_*/exports/conc_64_*"
              onChange={(event) => setRawDir(event.target.value)} />
          )}
        </Field>
        <div className="grid gap-3 md:grid-cols-2">
          <Field id="perf-import-model" name="model" labelKey="perf.archive.importModel">
            {(aria) => (
              <input {...aria} required className={FORM_INPUT_CLASS} value={model}
                onChange={(event) => setModel(event.target.value)} />
            )}
          </Field>
          <Field id="perf-import-load-mode" name="loadMode" labelKey="perf.archive.importLoadMode">
            {(aria) => (
              <select {...aria} className={FORM_INPUT_CLASS} value={loadMode}
                onChange={(event) => setLoadMode(event.target.value as 'closed_loop' | 'open_loop')}>
                <option value="closed_loop">{t('perf.archive.closedLoop')}</option>
                <option value="open_loop">{t('tasks.aiperf.open')}</option>
              </select>
            )}
          </Field>
          {loadMode === 'closed_loop'
            ? <Field id="perf-import-concurrency" name="concurrency" labelKey="perf.archive.importConcurrency">
              {(aria) => (
                <input {...aria} required type="number" min={1} className={FORM_INPUT_CLASS} value={concurrency}
                  onChange={(event) => setConcurrency(event.target.value)} />
              )}
            </Field>
            : <Field id="perf-import-rate" name="rate" labelKey="perf.archive.importRate">
              {(aria) => (
                <input {...aria} required type="number" min={0.001} step="any" className={FORM_INPUT_CLASS} value={rate}
                  onChange={(event) => setRate(event.target.value)} />
              )}
            </Field>}
          <Field id="perf-import-output-tokens" name="outputTokens" labelKey="perf.archive.importOutputTokens">
            {(aria) => (
              <input {...aria} required type="number" min={1} className={FORM_INPUT_CLASS} value={outputTokens}
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
        {result && <p role="status" className="text-sm">{t('perf.archive.importSuccess', {
          quality: result.quality, count: result.profiling_requests,
        })}: <code>{result.path}</code></p>}
        {error && <p role="alert" className="text-sm text-[var(--danger)]">{t('perf.archive.importFailed')}: {error}</p>}
      </form>
    </details>
  )
}
