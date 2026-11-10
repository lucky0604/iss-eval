import { useCallback, useEffect, useRef, useState } from 'react'
import { isDomainError } from '@/api/errors'

interface UsePollingOptions<T> {
  fn: (signal: AbortSignal) => Promise<T>
  interval?: number
  enabled?: boolean
  onData?: (data: T) => void
  onError?: (message: string, error: Error) => void
}

export function usePolling<T>({ fn, interval = 5000, enabled = false, onData, onError }: UsePollingOptions<T>) {
  const [data, setData] = useState<T | null>(null)
  const [failure, setFailure] = useState<Error | null>(null)
  // Counts completed polls so callers can tell a stale failure from a fresh one.
  const [revision, setRevision] = useState(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)
  const requestRef = useRef(0)
  // Every poll owns a signal, so unmount and a superseding tick can cancel the
  // request instead of leaving it to settle into a discarded state.
  const inflightRef = useRef<AbortController | null>(null)

  // Use refs so that changes to fn / onData do NOT restart the polling cycle.
  // Written in an effect (not during render) to satisfy react-hooks/refs.
  const fnRef = useRef(fn)
  const onDataRef = useRef(onData)
  const onErrorRef = useRef(onError)
  useEffect(() => {
    fnRef.current = fn
    onDataRef.current = onData
    onErrorRef.current = onError
  })

  const poll = useCallback(async () => {
    const requestId = ++requestRef.current
    inflightRef.current?.abort()
    const controller = new AbortController()
    inflightRef.current = controller
    try {
      const result = await fnRef.current(controller.signal)
      if (!mountedRef.current || requestId !== requestRef.current) return
      setData(result)
      setFailure(null)
      setRevision((value) => value + 1)
      onDataRef.current?.(result)
    } catch (e) {
      if (controller.signal.aborted || (isDomainError(e) && e.kind === 'aborted')) return
      if (!mountedRef.current || requestId !== requestRef.current) return
      const message = e instanceof Error ? e.message : String(e)
      const failure = e instanceof Error ? e : new Error(message)
      setFailure(failure)
      setRevision((value) => value + 1)
      onErrorRef.current?.(message, failure)
    }
  }, [])

  useEffect(() => {
    requestRef.current += 1
    mountedRef.current = true
    if (!enabled) {
      if (timerRef.current) clearTimeout(timerRef.current)
      return
    }
    let cancelled = false
    // A hidden tab keeps the schedule but skips the request: status and log
    // polling must not keep reading archives for a page nobody can see.
    const tick = async () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        if (!cancelled && mountedRef.current) {
          timerRef.current = setTimeout(tick, interval)
        }
        return
      }
      await poll()
      if (!cancelled && mountedRef.current) {
        timerRef.current = setTimeout(tick, interval)
      }
    }
    const onVisibility = () => {
      if (cancelled || !mountedRef.current || document.visibilityState !== 'visible') return
      if (timerRef.current) clearTimeout(timerRef.current)
      void tick()
    }
    document.addEventListener('visibilitychange', onVisibility)
    tick()
    return () => {
      cancelled = true
      mountedRef.current = false
      inflightRef.current?.abort()
      document.removeEventListener('visibilitychange', onVisibility)
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [enabled, interval, poll])

  return { data, error: failure?.message ?? null, failure, refresh: poll, revision }
}
