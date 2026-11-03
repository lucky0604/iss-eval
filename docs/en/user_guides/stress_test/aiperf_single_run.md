# AIPerf Single-Run API

The service can use an isolated AIPerf 0.13.0 installation for one streaming OpenAI-compatible chat benchmark. Set the service environment variable `EVALSCOPE_AIPERF_EXECUTABLE` to the executable's absolute path in that environment. The service does not install or switch executors automatically.

Send `POST /api/v1/perf/invoke` with an `EvalScope-Task-Id` header. Each non-empty `input_prompts` string is sent once, and the count must match `request_count`; a task permits at most 10,000 prompts and 4 MiB of UTF-8 text. The service creates a private 0600 input file inside the task directory. Clients cannot specify its path.

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

A single run supports one concurrency or request-rate point. Custom inference headers, Prometheus authentication, and label filtering are not supported. Set `api_key` for model-service authentication. A task is reported as stopped only after process-group cleanup is confirmed. Raw artifacts remain in the server task directory; a normalized report for this single-run mode is not yet available.
