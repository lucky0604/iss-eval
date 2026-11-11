import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { ReportsProvider } from '@/contexts/ReportsContext'
import { ThemeProvider } from '@/contexts/ThemeContext'
import MainLayout from '@/layouts/MainLayout'
import ErrorBoundary from '@/components/common/ErrorBoundary'
import { lazy, Suspense } from 'react'
import Skeleton from '@/components/ui/Skeleton'

const DashboardPage = lazy(() => import('@/pages/DashboardPage'))
const ReportsPage = lazy(() => import('@/pages/ReportsPage'))
const ReportDetailPage = lazy(() => import('@/pages/ReportDetailPage'))
const ComparePage = lazy(() => import('@/pages/ComparePage'))
const TasksPage = lazy(() => import('@/pages/TasksPage'))
const TaskCenterPage = lazy(() => import('@/pages/TaskCenterPage'))
const TaskDetailPage = lazy(() => import('@/pages/TaskDetailPage'))
const PerfReportsPage = lazy(() => import('@/pages/PerfReportsPage'))
const PerfReportDetailPage = lazy(() => import('@/pages/PerfReportDetailPage'))
const PerfComparePage = lazy(() => import('@/pages/PerfComparePage'))
const PerfPlanPage = lazy(() => import('@/pages/PerfPlanPage'))
const CacheExperimentPage = lazy(() => import('@/pages/CacheExperimentPage'))
const ReportViewerPage = lazy(() => import('@/pages/ReportViewerPage'))
const BenchmarksPage = lazy(() => import('@/pages/BenchmarksPage'))

function AppRoutes() {
  return (
    <Suspense fallback={<Skeleton lines={6} height={16} className="p-6" />}>
      <Routes>
        <Route element={<MainLayout />}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/reports/:runId/:modelId" element={<ReportDetailPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/performance" element={<PerfReportsPage />} />
          <Route path="/perf-report" element={<PerfReportDetailPage />} />
          <Route path="/perf-compare" element={<PerfComparePage />} />
          <Route path="/perf-plan" element={<PerfPlanPage />} />
          <Route path="/perf-plan/:taskId" element={<PerfPlanPage />} />
          <Route path="/perf-experiment/:taskId" element={<CacheExperimentPage />} />
          <Route path="/tasks" element={<TaskCenterPage />} />
          <Route path="/tasks/new" element={<TasksPage />} />
          <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
          {/* Legacy task routes — redirect into the creation page */}
          <Route path="/eval" element={<Navigate to="/tasks/new?tab=eval" replace />} />
          <Route path="/perf" element={<Navigate to="/tasks/new?tab=perf" replace />} />
          <Route path="/benchmarks" element={<BenchmarksPage />} />
          <Route path="/viewer" element={<ReportViewerPage />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <ThemeProvider>
          <LocaleProvider>
            <ReportsProvider>
              <AppRoutes />
            </ReportsProvider>
          </LocaleProvider>
        </ThemeProvider>
      </ErrorBoundary>
    </BrowserRouter>
  )
}
