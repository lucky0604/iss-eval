/**
 * Drive one independent report export: create it, poll its own record and
 * expose the artifacts it published.
 *
 * The export has its own id and lifecycle on the server; nothing here writes to
 * the load-test task, so the page can export while the task is still archived
 * as-is. Polling is cancelled on unmount and stops at every terminal status.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  cancelPerfExport,
  createPerfExport,
  getPerfExportStatus,
  type PerfExportKind,
  type PerfExportRequest,
} from '@/api/perfReports'
import type { PerfExportArtifact } from '@/api/generated/contracts'

const POLL_INTERVAL_MS = 500
const POLL_TIMEOUT_MS = 5 * 60 * 1000

export type PerfExportPhase = 'idle' | 'starting' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface PerfExportState {
  phase: PerfExportPhase
  kind: PerfExportKind | null
  exportId: string | null
  artifacts: PerfExportArtifact[]
  /** What the completed export covered, echoed from its own record. */
  runCount: number | null
  requestCount: number | null
  /** Server-provided failure text; empty when the export merely timed out. */
  error: string
  warnings: string[]
  start: (request: PerfExportRequest) => void
  cancel: () => void
  /** Re-run the last request through the same independent record. */
  retry: () => void
  reset: () => void
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener('abort', () => {
      clearTimeout(timer)
      resolve()
    }, { once: true })
  })
}

export function usePerfExport(): PerfExportState {
  const [phase, setPhase] = useState<PerfExportPhase>('idle')
  const [kind, setKind] = useState<PerfExportKind | null>(null)
  const [exportId, setExportId] = useState<string | null>(null)
  const [artifacts, setArtifacts] = useState<PerfExportArtifact[]>([])
  const [runCount, setRunCount] = useState<number | null>(null)
  const [requestCount, setRequestCount] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])

  const controllerRef = useRef<AbortController | null>(null)
  const disposedRef = useRef(false)
  const lastRequestRef = useRef<PerfExportRequest | null>(null)

  useEffect(() => {
    disposedRef.current = false
    return () => {
      disposedRef.current = true
      controllerRef.current?.abort()
    }
  }, [])

  const start = useCallback((request: PerfExportRequest) => {
    controllerRef.current?.abort()
    lastRequestRef.current = request
    const controller = new AbortController()
    controllerRef.current = controller
    const alive = () => !disposedRef.current && !controller.signal.aborted

    setKind(request.kind)
    setPhase('starting')
    setExportId(null)
    setArtifacts([])
    setRunCount(null)
    setRequestCount(null)
    setError('')
    setWarnings([])

    const run = async () => {
      try {
        const created = await createPerfExport(request, controller.signal)
        if (!alive()) return
        setExportId(created.export.export_id)
        setPhase('running')

        const deadline = Date.now() + POLL_TIMEOUT_MS
        let view = created.export
        while (view.status === 'queued' || view.status === 'running') {
          if (!alive()) return
          if (Date.now() > deadline) {
            setPhase('failed')
            return
          }
          await sleep(POLL_INTERVAL_MS, controller.signal)
          if (!alive()) return
          view = (await getPerfExportStatus(view.export_id, controller.signal)).export
        }
        if (!alive()) return

        setWarnings(view.warnings ?? [])
        if (view.status === 'completed') {
          setArtifacts(view.artifacts ?? [])
          setRunCount(view.run_count ?? null)
          setRequestCount(view.request_count ?? null)
          setPhase('completed')
        } else if (view.status === 'cancelled') {
          setPhase('cancelled')
        } else {
          setError(view.error ?? '')
          setPhase('failed')
        }
      } catch (err) {
        if (!alive()) return
        setError(err instanceof Error ? err.message : String(err))
        setPhase('failed')
      }
    }
    void run()
  }, [])

  const cancel = useCallback(() => {
    const active = exportId
    controllerRef.current?.abort()
    if (!active) {
      setPhase('cancelled')
      return
    }
    setPhase('cancelled')
    void cancelPerfExport(active).catch(() => {
      // The record keeps whatever state it reached; the page asked to stop.
    })
  }, [exportId])

  const retry = useCallback(() => {
    if (lastRequestRef.current) start(lastRequestRef.current)
  }, [start])

  const reset = useCallback(() => {
    controllerRef.current?.abort()
    setPhase('idle')
    setKind(null)
    setExportId(null)
    setArtifacts([])
    setRunCount(null)
    setRequestCount(null)
    setError('')
    setWarnings([])
  }, [])

  return {
    phase, kind, exportId, artifacts, runCount, requestCount, error, warnings, start, cancel, retry, reset,
  }
}
