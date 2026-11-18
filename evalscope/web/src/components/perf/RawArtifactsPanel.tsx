import { getPerfRawArtifacts, getPerfRawArtifactUrl } from '@/api/perf'
import { useLocale } from '@/contexts/LocaleContext'
import { useAsyncResource } from '@/hooks/useAsyncResource'
import { formatBytes } from '@/utils/formatUtils'

/** List and download the immutable raw AIPerf files behind one archive. */
export default function RawArtifactsPanel({ rootPath, path }: { rootPath: string; path: string }) {
  const { t } = useLocale()
  const resource = useAsyncResource(
    (signal) => getPerfRawArtifacts(rootPath, path, signal),
    [rootPath, path],
    { fallbackMessage: t('common.loadError') },
  )
  const files = resource.data?.files ?? []

  return (
    <section className="space-y-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-card)] p-5 shadow-[var(--shadow-sm)]">
      <h3 className="type-label-xs">{t('perf.archive.rawArtifacts')}</h3>
      <p className="type-body-xs text-[var(--text-muted)]">{t('perf.archive.rawHint')}</p>
      {resource.loading && <p className="text-sm">{t('common.loading')}</p>}
      {resource.error && <p role="alert" className="text-sm text-[var(--danger)]">{resource.error}</p>}
      {!resource.loading && !resource.error && (files.length === 0
        ? <p className="text-sm">{t('perf.archive.rawEmpty')}</p>
        : <div className="overflow-x-auto"><table className="w-full min-w-max border-collapse whitespace-nowrap text-left text-xs [&_th]:border-b [&_th]:border-[var(--border)] [&_th]:px-3 [&_th]:py-2 [&_td]:border-b [&_td]:border-[var(--border)] [&_td]:px-3 [&_td]:py-2">
          <thead><tr>
            <th>{t('perf.archive.rawFile')}</th><th>{t('perf.archive.rawSize')}</th>
          </tr></thead>
          <tbody>{files.map((file) => <tr key={file.name}>
            <td><a className="underline" href={getPerfRawArtifactUrl(rootPath, path, file.name)} download>{file.name}</a></td>
            <td className="tabular-nums">{formatBytes(file.size)}</td>
          </tr>)}</tbody>
        </table></div>)}
    </section>
  )
}
