/**
 * Standalone numeric report for an archive addressed by task id or path.
 *
 * Plan and cache-experiment pages link here so the matrix table, charts and
 * request page never live only inside one archive kind's own page.
 */
import { Link } from 'react-router-dom'
import { useLocale } from '@/contexts/LocaleContext'
import { useScan } from '@/contexts/ReportsContext'
import { useQueryParams } from '@/hooks/useQueryParams'
import PerfReportView from '@/components/perf/PerfReportView'
import Breadcrumb from '@/components/ui/Breadcrumb'

export default function NumericReportPage() {
  const { t } = useLocale()
  const { get } = useQueryParams()
  const { rootPath: ctxRoot } = useScan()

  const taskId = get('task_id') ?? undefined
  const path = get('path') ?? undefined
  const rootPath = get('root_path') ?? ctxRoot

  if (!taskId && !path) {
    return (
      <div className="page-enter flex flex-col gap-4">
        <Breadcrumb items={[{ label: t('perf.report.title') }]} />
        <p className="type-body-sm text-[var(--text-muted)]">{t('perf.report.requests.unavailable')}</p>
        <Link className="text-sm underline" to="/performance">{t('nav.performance')}</Link>
      </div>
    )
  }

  return (
    <div className="page-enter flex flex-col gap-4">
      <Breadcrumb
        items={[
          { label: t('nav.performance'), href: `/performance?root_path=${encodeURIComponent(rootPath)}` },
          { label: t('perf.report.title') },
        ]}
      />
      <PerfReportView location={{ taskId, rootPath, path }} />
    </div>
  )
}
