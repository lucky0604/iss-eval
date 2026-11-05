# AIPerf 0.13.0 local fixtures

These files were captured from AIPerf 0.13.0 on Python 3.12.11 against
`mock_inference_server.py`, on loopback only. They contain synthetic prompts and
no credentials. Loopback ports and temporary paths in exports were replaced
with stable placeholders; numeric measurements and phase records are retained.
They are examples of the upstream format, not representative performance data.

The successful run used the chat endpoint, streaming, a locally generated
WordLevel tokenizer, 1 concurrent request, 1 warmup request, 2 profiling
requests, `--export-level raw` and `--server-metrics-formats json`.
`profile_export_aiperf.json` has schema 1.4, `phase_manifest.json` has schema 1,
and `server_metrics_export.json` has schema 1.1. Request records contain both
phases; filter by `metadata.phase_kind` before calculating profiling statistics.
Metric units are included in each record. Server metric series are node-window
observations, not per-request measurements.

The `missing_usage_*` files were captured with usage omitted and server metrics
disabled. AIPerf still emits a locally calculated `input_sequence_length`, but
does not emit `usage_prompt_tokens`; neither implies a server cache hit.
`custom_requests.jsonl` captures two warmup and three profiling requests from
three sequential single-turn inputs. Warmup consumes the first two entries;
profiling then consumes the third and wraps to the first two. This does not
establish a primer barrier covering all prefixes.

An all-503 run exits nonzero but leaves request error records and no summary.
An alternating-success run exits zero with `is_complete=true`, despite one of
two requests failing. The `http_error_*` and `partial_*` files preserve these
two cases. Completion of the upstream process alone is not a data-quality test.

`multi_server_metrics_export.jsonl` was captured with the inference endpoint
and two additional metrics endpoints. AIPerf deduplicated an explicit metrics
URL identical to the automatically discovered inference endpoint, and each
time-series record retained its endpoint URL. This very short run contains too
few samples to establish complete profiling-window coverage for every node.

The separate `requirements/aiperf-macos-arm64-py312.lock` records the exact
macOS arm64 runtime used for this spike. It is platform-specific and is not a
general deployment lock for Linux; do not install it into EvalScope's runtime.

`native/` is a separate EvalScope baseline captured with the same loopback
server and a synthetic two-question local dataset at concurrency 1 and 2.
It preserves the native JSON and SQLite layout for compatibility tests; its
localhost port and temporary directory in configuration were replaced.
