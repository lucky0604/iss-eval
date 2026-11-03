import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import { getCacheRequestGroups } from '@/api/perf'
import CacheRequestGroupsPanel from './CacheRequestGroupsPanel'

vi.mock('@/api/perf', () => ({ getCacheRequestGroups: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('uses verified server usage groups and retains missing percentiles', async () => {
  vi.mocked(getCacheRequestGroups).mockResolvedValue({ availability: 'available', successful_requests: 3, observed_requests: 2,
    groups: [{ group: 'some_cached_tokens', count: 2, latency: { count: 2, mean: .003, p50: .001, p95: .005, p99: .005 },
      ttft: { count: 0, mean: null, p50: null, p95: null, p99: null } }] })
  await act(async () => { render(<LocaleProvider><CacheRequestGroupsPanel taskId="task" subrunId="run"
    cache={{ availability: 'available', observed_requests: 2 }} /></LocaleProvider>) })
  expect(screen.getByText('Coverage: 2 / 3')).toBeInTheDocument()
  expect(screen.getAllByText('some_cached_tokens / 2')).toHaveLength(2)
  expect(screen.getAllByText('—')).toHaveLength(4)
})
