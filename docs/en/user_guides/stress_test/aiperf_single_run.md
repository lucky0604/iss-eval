# AIPerf single-run tasks

The service can use an isolated AIPerf 0.13.0 installation for one streaming OpenAI-compatible chat benchmark. Set the service environment variable `EVALSCOPE_AIPERF_EXECUTABLE` to the executable's absolute path in that environment. The service does not install or switch executors automatically.

On the performance task page, select AIPerf and **Single run**, then enter one prompt per line. The browser may save prompt text in its local draft; the Web form limits prompt text to 512 KiB.

For API use, send `POST /api/v1/perf/invoke` with an `EvalScope-Task-Id` header. Each non-empty `input_prompts` string is sent once, and the count must match `request_count`; a task permits at most 10,000 prompts and 4 MiB of UTF-8 text. The service creates a private 0600 input file inside the task directory. Clients cannot specify its path.

```json
{
  "engine": "aiperf",
  "aiperf": {
    "model": "served-model",
    "url": "http://localhost:8000/v1",
    "tokenizer_path": "/models/tokenizer",
    "input_prompts": ["First prompt", "Second prompt"],
    "load": {
      "mode": "closed_loop",
      "points": [{"concurrency": 2}],
      "request_count": 2,
      "output_token_limit": 128
    }
  }
}
```

A single run supports one concurrency or request-rate point. Set `api_key` for model-service authentication. P/D targets can use independent Prometheus headers and label filters; the Web form supports a bearer token per node. Custom inference headers are unsupported. A task is reported as stopped only after process-group cleanup is confirmed. Raw artifacts and normalized results are retained, and readable runs appear in the performance archive. See [prefix-cache experiments](aiperf_cache.md) for installation, metric semantics, retention and rollback.

The single-run archive includes a **P/D node metrics** tab and a standalone HTML section with available node-window statistics, cache lookup ratios, provenance and missing-data reasons. Node windows include shared traffic and are not per-request stage timings. The Web chart reads bounded raw samples from the configured outputs root; when those samples are unavailable, the window table remains visible.
