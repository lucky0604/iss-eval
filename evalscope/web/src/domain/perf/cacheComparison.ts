import type { CachePlanConfiguration, CacheSubrunPlan, MetricStatistics } from '@/api/generated/contracts'

export type ComparisonDimension = 'ratio' | 'load'

/** Check explicit fixed dimensions before comparing two repetitions of one experiment. */
export function comparableRuns(a: CacheSubrunPlan, b: CacheSubrunPlan, dimension: ComparisonDimension,
                               sameExperiment = true): boolean {
  if ((sameExperiment && a.subrun_id === b.subrun_id) || a.load_mode !== b.load_mode || a.repetition !== b.repetition ||
      a.seed !== b.seed || a.request_count !== b.request_count || a.output_token_limit !== b.output_token_limit) return false
  const sameLoad = a.load_point.concurrency === b.load_point.concurrency && a.load_point.rate === b.load_point.rate
  return dimension === 'ratio' ? sameLoad && a.target_ratio !== b.target_ratio :
    a.target_ratio === b.target_ratio && !sameLoad
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
    .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(',')}}`
  return JSON.stringify(value) ?? 'undefined'
}

/** Require hash-verified configuration identity before comparing different experiments. */
export function comparableConfigurations(a?: CachePlanConfiguration | null, b?: CachePlanConfiguration | null): boolean {
  if (!a || !b) return false
  const fixed = (value: CachePlanConfiguration) => ({
    model: value.model, url: value.url, tokenizer_id: value.tokenizer_id, template_id: value.template_id,
    stream: value.stream, deployment_mode: value.deployment_mode, telemetry: value.telemetry,
    cache: { ...value.cache, target_ratios: [] },
    load: { ...value.load, points: [], repetitions: 1 },
  })
  return canonical(fixed(a)) === canonical(fixed(b))
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
