import type { CacheSubrunPlan, MetricStatistics } from '@/api/generated/contracts'

export type ComparisonDimension = 'ratio' | 'load'

/** Check explicit fixed dimensions before comparing two repetitions of one experiment. */
export function comparableRuns(a: CacheSubrunPlan, b: CacheSubrunPlan, dimension: ComparisonDimension): boolean {
  if (a.subrun_id === b.subrun_id || a.load_mode !== b.load_mode || a.repetition !== b.repetition ||
      a.seed !== b.seed || a.request_count !== b.request_count || a.output_token_limit !== b.output_token_limit) return false
  const sameLoad = a.load_point.concurrency === b.load_point.concurrency && a.load_point.rate === b.load_point.rate
  return dimension === 'ratio' ? sameLoad && a.target_ratio !== b.target_ratio :
    a.target_ratio === b.target_ratio && !sameLoad
}

/** Return no delta unless metric identity, source, scope and unit agree. */
export function comparableMetric(a: MetricStatistics | undefined, b: MetricStatistics | undefined): boolean {
  return Boolean(a && b && a.availability !== 'unavailable' && b.availability !== 'unavailable' &&
    a.key === b.key && a.source === b.source && a.scope === b.scope && a.unit === b.unit &&
    a.value != null && b.value != null && Number.isFinite(a.value) && Number.isFinite(b.value))
}

/** Zero baseline has no defined relative change. */
export function relativeChange(baseline: number, candidate: number): number | null {
  return baseline === 0 ? null : (candidate - baseline) / Math.abs(baseline)
}
