import type { Distribution, ReportRun, ReportRunDimensions } from '@/api/generated/contracts'

/** A fully-populated nearest-rank distribution; override only what a test reads. */
export function makeDistribution(values: Partial<Distribution> = {}): Distribution {
  return {
    availability: 'available',
    count: 100,
    percentile_method: 'nearest_rank',
    population: 'successful_profiling_requests',
    source: 'normalized_records',
    unit: 's',
    ...values,
  }
}

/** One planned run of the numeric report with stable, distinguishable values. */
export function makeRun(overrides: Partial<ReportRun> = {}): ReportRun {
  const dimensions: ReportRunDimensions = {
    deployment_mode: 'generation',
    workload: 'generation',
    message_structure: 'single_turn',
    input_tokens: 1024,
    output_token_limit: 128,
    target_ratio: 0,
    load_mode: 'closed_loop',
    concurrency: 8,
    repetition: null,
    ...(overrides.dimensions ?? {}),
  }
  return {
    archive_identity: 'plan:test',
    availability: 'available',
    counts: { planned_requests: 200, total: 200, success: 198, error: 2 },
    e2e: makeDistribution({ mean: 1.9, p50: 1.8, p90: 2.2, p95: 2.4, p99: 2.9 }),
    lifecycle: 'completed',
    observation: { start_ns: 0, end_ns: 60_000_000_000, seconds: 60, timed_requests: 198, total_requests: 200 },
    observed_concurrency: 7.5,
    order: 0,
    quality: 'complete',
    rates: {
      availability: 'available',
      input_tokens_per_second: 1234.5,
      output_tokens_per_second: 456.7,
      requests_per_minute: 12.3,
      requests_per_second: 0.205,
      source: 'measured_records',
      window_seconds: 60,
    },
    run_id: 'isl1024-osl128-c8',
    run_key: 'run-0',
    tokens: {
      cached_requests: 0,
      cached_source: null,
      cached_tokens: 0,
      input_requests: 198,
      input_source: 'server_usage',
      input_tokens: 202_752,
      output_requests: 198,
      output_source: 'server_usage',
      output_tokens: 25_344,
      successful_requests: 198,
    },
    tpot: makeDistribution({ mean: 0.02, p50: 0.018, p90: 0.03, p95: 0.035, p99: 0.04 }),
    ttft: makeDistribution({ mean: 0.12, p50: 0.1, p90: 0.2, p95: 0.25, p99: 0.3 }),
    ...overrides,
    dimensions,
  }
}
