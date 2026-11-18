/**
 * Format milliseconds: displays as "Xms" when < 1000, otherwise "X.Xs".
 * Returns empty string for null/undefined.
 */
export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/**
 * Format a byte count with binary units: `512 B`, `2.0 KiB`, `4.0 MiB`, `1.50 GiB`.
 */
export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MiB`
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GiB`
}

/** How much of an ISO timestamp to keep. */
export type TimestampPrecision = 'minutes' | 'seconds'

/**
 * Render an ISO timestamp as a space-separated local-looking string.
 *
 * `minutes` yields `YYYY-MM-DD HH:MM` for list and table cells, `seconds` yields
 * `YYYY-MM-DD HH:MM:SS` where the exact instant matters (run identity, tooltips).
 * An absent timestamp renders as the empty string so callers can pick their own
 * placeholder.
 */
export function formatTimestamp(
  ts: string | null | undefined,
  precision: TimestampPrecision = 'minutes',
): string {
  if (!ts) return ''
  return ts.replace('T', ' ').slice(0, precision === 'seconds' ? 19 : 16)
}
