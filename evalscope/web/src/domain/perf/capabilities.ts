/**
 * Capability-driven performance result UI.
 *
 * The backend resolves every result surface (overview, sweep, requests, node
 * metrics, cache experiment, configuration, html) from the archive itself. The
 * frontend must never guess from the engine label: `engine === 'aiperf'` alone
 * cannot tell a single run from a cache experiment, and a task can finish with
 * a partial archive. Tabs are therefore a function of the capability payload.
 */
import type { AvailabilityState, ResultCapabilities } from '@/api/types'

export type CapabilityName =
  | 'overview'
  | 'sweep'
  | 'requests'
  | 'node_metrics'
  | 'cache_experiment'
  | 'configuration'
  | 'html'

/** Tab order in the result view; `html` and `configuration` are not result tabs. */
export const RESULT_TAB_ORDER: readonly CapabilityName[] = [
  'overview',
  'cache_experiment',
  'sweep',
  'requests',
  'node_metrics',
]

type Availability = NonNullable<AvailabilityState['availability']>

/**
 * Keys are written out rather than assembled from the token so the i18n
 * reachability check can see them; the backend contract still decides which
 * tokens are legal.
 */
const AVAILABILITY_KEYS: Record<Availability, string> = {
  available: 'perf.result.availability.available',
  pending: 'perf.result.availability.pending',
  not_configured: 'perf.result.availability.not_configured',
  unsupported: 'perf.result.availability.unsupported',
  unavailable: 'perf.result.availability.unavailable',
  unknown: 'perf.result.availability.unknown',
}

const REASON_KEYS: Record<string, string> = {
  task_not_finished: 'perf.result.reason.task_not_finished',
  archive_missing: 'perf.result.reason.archive_missing',
  archive_unreadable: 'perf.result.reason.archive_unreadable',
  config_not_persisted: 'perf.result.reason.config_not_persisted',
  html_not_generated: 'perf.result.reason.html_not_generated',
  no_requests: 'perf.result.reason.no_requests',
  no_node_metrics: 'perf.result.reason.no_node_metrics',
  not_supported: 'perf.result.reason.not_supported',
  single_run: 'perf.result.reason.single_run',
  execution_failed: 'perf.result.reason.execution_failed',
  unknown: 'perf.result.reason.unknown',
}

const TEST_TYPE_KEYS: Record<string, string> = {
  single: 'tasks.testType.single',
  sweep: 'tasks.testType.sweep',
  cache_experiment: 'tasks.testType.cache_experiment',
  unknown: 'tasks.testType.unknown',
}

const ENGINE_KEYS: Record<string, string> = {
  evalscope: 'tasks.engine.evalscope',
  aiperf: 'tasks.engine.aiperf',
  unknown: 'tasks.engine.unknown',
}

const TAB_KEYS: Record<CapabilityName, string> = {
  overview: 'perf.result.tabs.overview',
  cache_experiment: 'perf.result.tabs.cache_experiment',
  sweep: 'perf.result.tabs.sweep',
  requests: 'perf.result.tabs.requests',
  node_metrics: 'perf.result.tabs.node_metrics',
  configuration: 'perf.result.tabs.configuration',
  html: 'perf.result.tabs.html',
}

export function capabilityState(
  capabilities: ResultCapabilities | null | undefined,
  name: CapabilityName,
): AvailabilityState {
  return capabilities?.[name] ?? { availability: 'unknown' }
}

export function isCapabilityAvailable(
  capabilities: ResultCapabilities | null | undefined,
  name: CapabilityName,
): boolean {
  return capabilityState(capabilities, name).availability === 'available'
}

/**
 * The result tabs to render, in product order.
 *
 * `configuration` is a section rather than a tab; `html` is an action. Only
 * surfaces the backend marks available or pending become tabs, so an empty
 * requests tab never appears for a format that cannot produce one.
 */
export function resultTabs(capabilities: ResultCapabilities | null | undefined): CapabilityName[] {
  return RESULT_TAB_ORDER.filter((name) => {
    const availability = capabilityState(capabilities, name).availability
    return availability === 'available' || availability === 'pending'
  })
}

/** Translation key for an availability value. */
export function availabilityLabelKey(state: AvailabilityState | null | undefined): string {
  const availability = state?.availability
  return AVAILABILITY_KEYS[availability ?? 'unknown'] ?? AVAILABILITY_KEYS.unknown
}

/** Translation key for a backend reason token; unknown tokens stay explicit. */
export function reasonLabelKey(reason: string | null | undefined): string {
  return REASON_KEYS[reason ?? 'unknown'] ?? REASON_KEYS.unknown
}

/** Translation key for a result's data quality; absent quality is "not provided". */
export function qualityLabelKey(quality: string | null | undefined): string {
  const keys: Record<string, string> = {
    complete: 'perf.result.quality_complete',
    partial: 'perf.result.quality_partial',
    invalid: 'perf.result.quality_invalid',
  }
  return (quality && keys[quality]) || 'perf.result.quality_unavailable'
}

/** Translation key for a performance test type. */
export function testTypeLabelKey(testType: string | null | undefined): string {
  return TEST_TYPE_KEYS[testType ?? 'unknown'] ?? TEST_TYPE_KEYS.unknown
}

/** Translation key for the execution backend (secondary metadata only). */
export function engineLabelKey(engine: string | null | undefined): string {
  return ENGINE_KEYS[engine ?? 'unknown'] ?? ENGINE_KEYS.unknown
}

/** Translation key for a result tab label. */
export function resultTabLabelKey(name: CapabilityName): string {
  return TAB_KEYS[name]
}

/** Translation key for one capability name, for the availability list. */
export function capabilityLabelKey(name: CapabilityName): string {
  return TAB_KEYS[name]
}
