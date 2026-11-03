# AIPerf prefix-cache experiments

The performance task page supports the existing EvalScope executor and the optional AIPerf executor. EvalScope remains the default. AIPerf 0.13.0 supports streaming OpenAI-compatible chat completions, one inline-prompt run, or a matrix of prefix-reuse targets, load points and repetitions. The tokenizer must be accessible to the service and use the same chat template as the model server.

## Install the isolated runtime

The following lock has been exercised on macOS arm64 with Python 3.12.11. It is separate from the EvalScope environment and does not change EvalScope's Python 3.10–3.12 support:

```bash
python3.12 -m venv /path/to/aiperf-venv
/path/to/aiperf-venv/bin/python -m pip install -r requirements/aiperf-macos-arm64-py312.lock
/path/to/aiperf-venv/bin/aiperf --version
export EVALSCOPE_AIPERF_EXECUTABLE=/path/to/aiperf-venv/bin/aiperf
evalscope service
```

The Linux x86_64 glibc/Python 3.12 binary lock at `requirements/aiperf-linux-x86_64-py312.lock` is cross-resolved but has not been installed or run on Linux. Validate it on the target host before use. The runner requires a supported POSIX process-group implementation (Linux or macOS) and `/bin/ps`. Size process capacity, network access and output storage for the selected request budget; the platform does not enforce a global concurrency quota or retention policy.

The service detects the configured executable and requires version 0.13.0. It does not install dependencies at request time or fall back to another executor. Keep the runtime lock, Python version and source artifacts with your deployment; when upgrading AIPerf, recheck its CLI, output schemas, time boundaries and process cleanup before changing the pinned version.

## Configure and run

Set the model connection, choose **AIPerf**, and select either **Prefix cache experiment** or **Single run**. A single run sends each nonblank line as one request. Its browser draft and templates may contain prompt text; do not enter secrets in prompts on a shared browser. The Web form accepts at most 512 KiB of prompt text; the API accepts at most 10,000 prompts and 4 MiB. A single run has one load point and no cache experiment.

For an experiment, enter percentages such as `0, 80, 90`, total input tokens, prefix pool size, seed, load points, request count and repetitions. Preview displays the planned subruns, profiling requests and primer requests. A template can make a short input/ratio combination impossible; preflight rejects known geometry failures. The actual generation still verifies tokenization and can fail closed if the tokenizer cannot construct the requested inputs. Each shared prefix completes its primer before the associated profiling run starts. A fresh namespace is not proof that the server cache was cleared or that every backend received a primer.

Add independent P and D Prometheus targets, choose each node's role and engine, and optionally provide label filters and a bearer token. Model and Metrics authentication are separate. Metrics bearer tokens are used for the task and are not stored in browser drafts, templates or exports; changing the target origin clears its token. URLs with credentials, query strings or fragments are unsupported. In PD mode, requiring Metrics requires both P and D targets. The optional endpoint probe is read-only; startup checks required targets again. Preview again after changing any field.

## Read the results

The report keeps three quantities separate: **target reuse**, **generated token-prefix reuse** and **server-observed cache use**. Request-level cache use is available only when the server returns cached and prompt token usage for the same successful request. A vLLM node lookup-token ratio is a different node-window measurement. Missing or unsupported data is shown as unavailable, never zero. Request latency groups use exact nearest-rank percentiles over verified server usage; grouping is unavailable above 200,000 observed requests. Repetitions remain separate, and P99 values are not averaged into a pooled P99.

The node panel shows independently attributed vLLM/SGLang series, including available queue, prefill/decode and KV transfer measurements. Gauges, counters and histograms have different window semantics: sample peaks are sampled peaks, histogram percentiles are approximate, and coverage, source, unit, count, labels, bounds and reasons are retained. A node window includes shared traffic and is not a per-request P/D trace. The mappings were checked against vLLM 0.10.2 and SGLang 0.5.2/0.5.23 source; verify your deployed versions, labels, clocks, connector and routing. Template match and routing coverage remain unverified until checked in the actual deployment, so results can remain partial even after a successful process exit.

The live panel displays bounded actual node scrapes; failed or stale samples remain marked stale. When AIPerf emits a 0.13.0 realtime client log tick, the panel separately shows completed records and its rounded requests/s; sent requests become available after the send-complete event. Short runs or missing log ticks remain unavailable. Realtime client logging is enabled at a two-second cadence and adds measurement overhead. The log timestamps use the service host local timezone. Explicit comparisons fix load or target ratio and repetition, require matching metric source, scope, unit and AIPerf version, and can compare explicitly selected runs across experiments only when their credential-free configuration identities match. Separate experiments have independent namespaces and are not paired per request. Repetition ranges show individual values and sample counts for the current matrix page; they do not pool tail percentiles. A zero baseline has no relative percentage change. The standalone HTML streams the full experiment and request records; it can be large.

## Failure, retention and rollback

Cancellation can remain `cancelling` after an acknowledgement. A task is stopped only after cleanup is confirmed; partial reports remain accessible when artifacts are readable. Service restart can read archived results but does not resume scheduling. Keep the task directory, raw AIPerf export, tokenizer/template identity and parser version together according to your retention policy. Never delete an active task's output.

To inspect a reimport without changing a published report, use a new output directory on a copy of the raw files:

```python
from evalscope.perf.results.aiperf_import import import_aiperf_run
from evalscope.perf.results.models import AIPerfImportArguments

result = import_aiperf_run(AIPerfImportArguments(
    raw_dir='/path/to/copied/raw/aiperf',
    normalized_dir='/path/to/new/normalized',
    allow_partial_tail=True,
))
print(result.quality, result.quality_reasons)
```

Node attribution needs the original node references; omitting them leaves node identity unavailable. This operation does not automatically replace a published scenario or recompute its node analysis. Rerunning a test creates a new task and is not a resume.

For rollback, stop active AIPerf tasks and wait for confirmed cleanup, then remove `EVALSCOPE_AIPERF_EXECUTABLE` from the service environment and restart it. Existing reports and raw artifacts remain readable; users can explicitly select EvalScope for new tests. Old v1 browser data is retained during migration, but old clients read their old v1 snapshot rather than subsequent v2 edits.
