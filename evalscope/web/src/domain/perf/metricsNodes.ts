/**
 * Field ids of the metrics-node editor's controls.
 *
 * Kept outside the component so both forms (legacy experiment and full matrix)
 * and the submit-time focus order share one id scheme; a second copy is how the
 * `aiperf-` and `matrix-` prefixes would drift apart.
 */
export function metricsNodeFieldIds(idPrefix: string, nodes: readonly { id: string }[]): string[] {
  return nodes.flatMap((node) =>
    (['nodeId', 'url', 'labels', 'bearerToken'] as const).map((name) => `${idPrefix}-node-${node.id}-${name}`),
  )
}
