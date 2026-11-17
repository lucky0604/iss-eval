/**
 * Column model for the numeric performance wide table.
 *
 * One row is one planned run, one cell is one number (or an explicit missing
 * placeholder) — never a statistics object. Column keys, units and decimal
 * places live here so the Web table, the CSV/PDF exports and their tests all
 * read the same definition instead of re-inventing headers.
 */
import type { ReportRun, ReportRunDimensions } from '@/api/generated/contracts'

export type ReportPreset = 'core' | 'latency' | 'throughput' | 'token' | 'all'
export type ReportColumnGroup = 'dimension' | 'ttft' | 'tpot' | 'e2e' | 'throughput' | 'token' | 'result'

/** Server-accepted sort keys (`RUN_SORT_KEYS` on the service). */
export type ReportRunSort =
  | 'comparison'
  | 'order'
  | 'input_tokens'
  | 'output_token_limit'
  | 'target_ratio'
  | 'concurrency'
  | 'rate'
  | 'ttft_mean'
  | 'ttft_p90'
  | 'ttft_p95'
  | 'ttft_p99'
  | 'tpot_mean'
  | 'tpot_p90'
  | 'tpot_p95'
  | 'tpot_p99'
  | 'latency_mean'
  | 'latency_p90'
  | 'latency_p95'
  | 'latency_p99'
  | 'requests_per_minute'
  | 'success_rate'

export const REPORT_PRESETS: readonly ReportPreset[] = ['core', 'latency', 'throughput', 'token', 'all']

export const REPORT_COLUMN_GROUPS: readonly { key: ReportColumnGroup; labelKey: string }[] = [
  { key: 'dimension', labelKey: 'perf.report.group.dimension' },
  { key: 'ttft', labelKey: 'perf.report.group.ttft' },
  { key: 'tpot', labelKey: 'perf.report.group.tpot' },
  { key: 'e2e', labelKey: 'perf.report.group.e2e' },
  { key: 'throughput', labelKey: 'perf.report.group.throughput' },
  { key: 'token', labelKey: 'perf.report.group.token' },
  { key: 'result', labelKey: 'perf.report.group.result' },
]

export interface ReportColumn {
  key: string
  group: ReportColumnGroup
  labelKey: string
  /** Short unit shown under the header, e.g. `ms` or `tok/s`. */
  unitKey?: string
  presets: readonly ReportPreset[]
  numeric: boolean
  /** Multiplier applied for display only (seconds → ms, fraction → %). */
  scale?: number
  decimals?: number
  sortKey?: ReportRunSort
  value: (run: ReportRun) => number | null
  /** Exact text for a tooltip; defaults to the rendered cell. */
  exact?: (run: ReportRun) => string
  /** Hide a column that carries no information for the current rows. */
  hidden?: (runs: readonly ReportRun[]) => boolean
}

const missing = () => null

function ttft(run: ReportRun, key: 'mean' | 'p50' | 'p90' | 'p95' | 'p99' | 'min' | 'max' | 'std') {
  return run.ttft[key] ?? null
}

function tpot(run: ReportRun, key: 'mean' | 'p50' | 'p90' | 'p95' | 'p99' | 'min' | 'max' | 'std') {
  return run.tpot[key] ?? null
}

function e2e(run: ReportRun, key: 'mean' | 'p50' | 'p90' | 'p95' | 'p99' | 'min' | 'max' | 'std') {
  return run.e2e[key] ?? null
}

/** Statistics the service can order rows by (`RUN_SORT_KEYS`). */
function sortableStatistic(statistic: string): boolean {
  return statistic === 'mean' || statistic === 'p90' || statistic === 'p95' || statistic === 'p99'
}

function latencyColumn(
  statistic: 'mean' | 'p50' | 'p90' | 'p95' | 'p99' | 'min' | 'max' | 'std',
  presets: readonly ReportPreset[],
): ReportColumn {
  return {
    key: `ttft_${statistic}`,
    group: 'ttft',
    labelKey: `perf.report.col.${statistic}`,
    unitKey: 'perf.report.unit.ms',
    presets,
    numeric: true,
    scale: 1000,
    decimals: 2,
    sortKey: sortableStatistic(statistic) ? (`ttft_${statistic}` as ReportRunSort) : undefined,
    value: (run) => ttft(run, statistic),
  }
}

function tpotColumn(
  statistic: 'mean' | 'p50' | 'p90' | 'p95' | 'p99' | 'min' | 'max' | 'std',
  presets: readonly ReportPreset[],
): ReportColumn {
  return {
    key: `tpot_${statistic}`,
    group: 'tpot',
    labelKey: `perf.report.col.${statistic}`,
    unitKey: 'perf.report.unit.ms_per_token',
    presets,
    numeric: true,
    scale: 1000,
    decimals: 2,
    sortKey: sortableStatistic(statistic) ? (`tpot_${statistic}` as ReportRunSort) : undefined,
    value: (run) => tpot(run, statistic),
  }
}

function e2eColumn(
  statistic: 'mean' | 'p50' | 'p90' | 'p95' | 'p99' | 'min' | 'max' | 'std',
  presets: readonly ReportPreset[],
): ReportColumn {
  return {
    key: `e2e_${statistic}`,
    group: 'e2e',
    labelKey: `perf.report.col.${statistic}`,
    unitKey: 'perf.report.unit.s',
    presets,
    numeric: true,
    decimals: 3,
    sortKey: sortableStatistic(statistic) ? (`latency_${statistic}` as ReportRunSort) : undefined,
    value: (run) => e2e(run, statistic),
  }
}

/** The eight latency columns required by default, in display order. */
export const REPORT_COLUMNS: readonly ReportColumn[] = [
  {
    key: 'isl',
    group: 'dimension',
    labelKey: 'perf.report.col.input_tokens',
    unitKey: 'perf.report.unit.tokens',
    presets: ['core', 'latency', 'throughput', 'token', 'all'],
    numeric: true,
    decimals: 0,
    sortKey: 'input_tokens',
    value: (run) => run.dimensions.input_tokens ?? null,
    exact: (run) => `${run.dimensions.input_tokens ?? '—'} tokens`,
  },
  {
    key: 'load',
    group: 'dimension',
    labelKey: 'perf.report.col.load',
    presets: ['core', 'latency', 'throughput', 'token', 'all'],
    numeric: true,
    decimals: 0,
    value: (run) => loadValue(run),
    exact: (run) => (run.dimensions.load_mode === 'open_loop' ? `${loadValue(run) ?? '—'} req/s` : `${loadValue(run) ?? '—'} concurrency`),
  },
  {
    key: 'repetition',
    group: 'dimension',
    labelKey: 'perf.report.col.repetition',
    presets: ['core', 'latency', 'throughput', 'token', 'all'],
    numeric: true,
    decimals: 0,
    value: (run) => run.dimensions.repetition ?? null,
    hidden: (runs) => runs.every((run) => !run.dimensions.repetition),
  },
  latencyColumn('mean', ['core', 'latency', 'all']),
  latencyColumn('p90', ['core', 'latency', 'all']),
  latencyColumn('p95', ['core', 'latency', 'all']),
  latencyColumn('p99', ['core', 'latency', 'all']),
  latencyColumn('p50', ['latency', 'all']),
  latencyColumn('min', ['latency', 'all']),
  latencyColumn('max', ['latency', 'all']),
  latencyColumn('std', ['latency', 'all']),
  tpotColumn('mean', ['core', 'latency', 'all']),
  tpotColumn('p90', ['core', 'latency', 'all']),
  tpotColumn('p95', ['core', 'latency', 'all']),
  tpotColumn('p99', ['core', 'latency', 'all']),
  tpotColumn('p50', ['latency', 'all']),
  tpotColumn('min', ['latency', 'all']),
  tpotColumn('max', ['latency', 'all']),
  tpotColumn('std', ['latency', 'all']),
  e2eColumn('mean', ['core', 'throughput', 'all']),
  e2eColumn('p99', ['core', 'all']),
  e2eColumn('p50', ['latency', 'all']),
  e2eColumn('p90', ['all']),
  e2eColumn('p95', ['all']),
  {
    key: 'input_tps',
    group: 'throughput',
    labelKey: 'perf.report.col.input_tps',
    unitKey: 'perf.report.unit.tok_s',
    presets: ['core', 'throughput', 'all'],
    numeric: true,
    decimals: 2,
    value: (run) => run.rates.input_tokens_per_second ?? null,
  },
  {
    key: 'output_tps',
    group: 'throughput',
    labelKey: 'perf.report.col.output_tps',
    unitKey: 'perf.report.unit.tok_s',
    presets: ['core', 'throughput', 'all'],
    numeric: true,
    decimals: 2,
    value: (run) => run.rates.output_tokens_per_second ?? null,
  },
  {
    key: 'rpm',
    group: 'throughput',
    labelKey: 'perf.report.col.rpm',
    unitKey: 'perf.report.unit.rpm',
    presets: ['core', 'throughput', 'all'],
    numeric: true,
    decimals: 1,
    sortKey: 'requests_per_minute',
    value: (run) => run.rates.requests_per_minute ?? null,
  },
  {
    key: 'window_seconds',
    group: 'throughput',
    labelKey: 'perf.report.col.window',
    unitKey: 'perf.report.unit.s',
    presets: ['throughput', 'all'],
    numeric: true,
    decimals: 2,
    value: (run) => run.observation.seconds ?? null,
  },
  {
    key: 'observed_concurrency',
    group: 'throughput',
    labelKey: 'perf.report.col.observed_concurrency',
    presets: ['throughput', 'all'],
    numeric: true,
    decimals: 2,
    value: (run) => run.observed_concurrency ?? null,
  },
  {
    key: 'input_tokens_total',
    group: 'token',
    labelKey: 'perf.report.col.input_tokens_total',
    unitKey: 'perf.report.unit.tokens',
    presets: ['token', 'all'],
    numeric: true,
    decimals: 0,
    value: (run) => run.tokens.input_tokens ?? null,
  },
  {
    key: 'output_tokens_total',
    group: 'token',
    labelKey: 'perf.report.col.output_tokens_total',
    unitKey: 'perf.report.unit.tokens',
    presets: ['token', 'all'],
    numeric: true,
    decimals: 0,
    value: (run) => run.tokens.output_tokens ?? null,
  },
  {
    key: 'cached_tokens',
    group: 'token',
    labelKey: 'perf.report.col.cached_tokens',
    unitKey: 'perf.report.unit.tokens',
    presets: ['token', 'all'],
    numeric: true,
    decimals: 0,
    value: (run) => run.tokens.cached_tokens ?? null,
  },
  {
    key: 'input_token_coverage',
    group: 'token',
    labelKey: 'perf.report.col.input_coverage',
    presets: ['token', 'all'],
    numeric: true,
    decimals: 0,
    value: (run) => run.tokens.input_requests ?? null,
    exact: (run) =>
      `${run.tokens.input_requests ?? 0}/${run.tokens.successful_requests ?? 0} · ${run.tokens.input_source ?? '—'}`,
  },
  {
    key: 'output_token_coverage',
    group: 'token',
    labelKey: 'perf.report.col.output_coverage',
    presets: ['token', 'all'],
    numeric: true,
    decimals: 0,
    value: (run) => run.tokens.output_requests ?? null,
    exact: (run) =>
      `${run.tokens.output_requests ?? 0}/${run.tokens.successful_requests ?? 0} · ${run.tokens.output_source ?? '—'}`,
  },
  {
    key: 'output_token_limit',
    group: 'token',
    labelKey: 'perf.report.col.output_token_limit',
    unitKey: 'perf.report.unit.tokens',
    presets: ['token', 'all'],
    numeric: true,
    decimals: 0,
    sortKey: 'output_token_limit',
    value: (run) => run.dimensions.output_token_limit ?? null,
  },
  {
    key: 'success_total',
    group: 'result',
    labelKey: 'perf.report.col.requests',
    presets: ['core', 'latency', 'throughput', 'all'],
    numeric: false,
    value: missing,
    exact: (run) => `${successCount(run)}/${totalCount(run)}`,
  },
  {
    key: 'success_rate',
    group: 'result',
    labelKey: 'perf.report.col.success_rate',
    unitKey: 'perf.report.unit.percent',
    presets: ['core', 'latency', 'throughput', 'all'],
    numeric: true,
    scale: 100,
    decimals: 1,
    sortKey: 'success_rate',
    value: successRate,
  },
  {
    key: 'status',
    group: 'result',
    labelKey: 'perf.report.col.status',
    presets: ['core', 'latency', 'throughput', 'token', 'all'],
    numeric: false,
    value: missing,
  },
]

/** Columns of one preset, keeping the canonical display order. */
export function columnsForPreset(preset: ReportPreset, runs: readonly ReportRun[] = []): ReportColumn[] {
  return REPORT_COLUMNS.filter((column) => column.presets.includes(preset) && !column.hidden?.(runs))
}

export function isCoreLatencyColumn(column: ReportColumn): boolean {
  return column.group === 'ttft' || column.group === 'tpot'
}

/** Closed-loop runs count concurrency; open-loop runs count a target rate. */
export function loadModeOf(run: ReportRun): 'open' | 'closed' {
  return run.dimensions.load_mode === 'open_loop' ? 'open' : 'closed'
}

export function loadValue(run: ReportRun): number | null {
  if (loadModeOf(run) === 'open') return run.dimensions.rate ?? null
  return run.dimensions.concurrency ?? null
}

/** The server sort key a "load" header click should use for these rows. */
export function loadSortKey(runs: readonly ReportRun[]): ReportRunSort {
  return runs.length > 0 && runs.every((run) => loadModeOf(run) === 'open') ? 'rate' : 'concurrency'
}

export function successCount(run: ReportRun): number {
  return run.counts.success ?? 0
}

export function totalCount(run: ReportRun): number {
  return run.counts.total ?? run.counts.planned_requests ?? 0
}

export function successRate(run: ReportRun): number | null {
  const total = totalCount(run)
  return total > 0 ? successCount(run) / total : null
}

/** Same identity the backend groups rows by; rows of one group share it. */
export function groupKeyOf(dimensions: ReportRunDimensions): string {
  return [
    dimensions.deployment_mode ?? '',
    dimensions.workload ?? '',
    dimensions.message_structure ?? '',
    dimensions.input_tokens ?? '',
    dimensions.output_token_limit ?? '',
    dimensions.target_ratio ?? '',
    dimensions.load_mode ?? '',
  ].join('|')
}

export function rowGroupKey(run: ReportRun): string {
  return groupKeyOf(run.dimensions)
}

export interface ReportRowGroup {
  key: string
  rows: ReportRun[]
  /** True when this page starts inside the group (its header is not on the page). */
  continued: boolean
}

/**
 * Group rows while keeping server order: rows of one group are contiguous only
 * in the default order, which is the only order that renders section headers.
 */
export function contiguousGroups(rows: readonly ReportRun[]): ReportRowGroup[] {
  const groups: ReportRowGroup[] = []
  for (const run of rows) {
    const key = rowGroupKey(run)
    const last = groups[groups.length - 1]
    if (last && last.key === key) last.rows.push(run)
    else groups.push({ key, rows: [run], continued: groups.some((group) => group.key === key) })
  }
  return groups
}

const NUMBER_FORMATTERS = new Map<number, Intl.NumberFormat>()

function formatterFor(decimals: number): Intl.NumberFormat {
  let formatter = NUMBER_FORMATTERS.get(decimals)
  if (!formatter) {
    formatter = new Intl.NumberFormat('en-US', {
      useGrouping: false,
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })
    NUMBER_FORMATTERS.set(decimals, formatter)
  }
  return formatter
}

/**
 * Render numeric width without unit suffixes (the header carries the unit).
 * A missing value is the em-dash placeholder; a measured zero stays `0.00`.
 */
export function formatNumber(value: number | null, decimals: number): string {
  if (value === null || !Number.isFinite(value)) return '—'
  return formatterFor(decimals).format(value)
}

export function formatColumnValue(column: ReportColumn, run: ReportRun): string {
  if (column.key === 'success_total') return `${successCount(run)}/${totalCount(run)}`
  if (column.key === 'status') return run.lifecycle
  const raw = column.value(run)
  if (raw === null || !Number.isFinite(raw)) return '—'
  const scaled = column.scale ? raw * column.scale : raw
  return formatNumber(scaled, column.decimals ?? 0)
}

/** Sort direction the service should use for a column header click. */
export function nextSort(currentSort: ReportRunSort, currentOrder: 'asc' | 'desc', sortKey: ReportRunSort): {
  sort: ReportRunSort
  order: 'asc' | 'desc'
} {
  if (currentSort === sortKey) return { sort: sortKey, order: currentOrder === 'asc' ? 'desc' : 'asc' }
  return { sort: sortKey, order: sortKey === 'order' ? 'asc' : 'desc' }
}
