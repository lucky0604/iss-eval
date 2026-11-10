import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll, vi } from 'vitest'
import fc from 'fast-check'

// Deterministic test environment.
//
// Unit and component tests must produce consistent results
// across repeated runs. That requires eliminating the three common sources of
// non-determinism: the system clock, the random-number generator and network
// access. This setup file locks all three down for the whole test suite.

// Fixed system time. Any code that reads `Date.now()` or constructs `new Date()`
// during tests observes this instant instead of the wall clock.
const FIXED_SYSTEM_TIME = new Date('2026-07-01T00:00:00.000Z')

// Fixed fast-check seed and iteration count. Every property test replays the
// same input sequence on every machine and every run.
const FAST_CHECK_SEED = 42
const FAST_CHECK_NUM_RUNS = 100

// Node 26 exposes its own file-backed `localStorage` global, which is shared by
// every test file in the run. Replacing it with a per-environment in-memory
// Storage keeps browser-storage state isolated between test files (the jsdom
// window is the test global, so the app and the tests read the same object).
class MemoryStorage implements Storage {
  private readonly data = new Map<string, string>()

  get length(): number {
    return this.data.size
  }

  clear(): void {
    this.data.clear()
  }

  getItem(key: string): string | null {
    return this.data.has(key) ? this.data.get(key)! : null
  }

  key(index: number): string | null {
    return [...this.data.keys()][index] ?? null
  }

  removeItem(key: string): void {
    this.data.delete(key)
  }

  setItem(key: string, value: string): void {
    this.data.set(key, String(value))
  }
}


fc.configureGlobal({ seed: FAST_CHECK_SEED, numRuns: FAST_CHECK_NUM_RUNS })

// Guard against accidental network access from tests. Any attempt to call
// `fetch` fails loudly so that a test relying on the network is caught rather
// than silently hitting a real endpoint (or hanging).
const forbiddenFetch: typeof fetch = (input) => {
  const target = typeof input === 'string' ? input : String((input as Request).url ?? input)
  return Promise.reject(
    new Error(
      `Network access is disabled in tests. Attempted fetch to: ${target}. ` +
        'Use deterministic fixtures instead.',
    ),
  )
}

beforeAll(() => {
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: new MemoryStorage() })
  // Fake timers with a fixed "now" so timers and timestamps are deterministic.
  vi.useFakeTimers()
  vi.setSystemTime(FIXED_SYSTEM_TIME)
  vi.stubGlobal('fetch', forbiddenFetch)
})

afterEach(() => {
  // Clear timers/mocks between tests to avoid cross-test leakage while keeping
  // the fixed system time in place.
  vi.clearAllTimers()
  vi.clearAllMocks()
  vi.setSystemTime(FIXED_SYSTEM_TIME)
})

afterAll(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
