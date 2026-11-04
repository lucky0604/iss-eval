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

The Linux x86_64 glibc/Python 3.12 binary lock at `requirements/aiperf-linux-x86_64-py312.lock` was cross-resolved and installed with a clean dependency check under Rosetta-emulated Linux x86_64. Native Linux execution and deployment acceptance remain to be verified on the target host. The runner requires a supported POSIX process-group implementation (Linux or macOS) and `/bin/ps`. Size process capacity, network access and output storage for the selected request budget; the platform does not enforce a global concurrency quota or retention policy.

### Linux server preparation and local regression

Run these commands from the project root. Use two separate Python virtual environments: install service dependencies in the framework environment and the locked runtime in the AIPerf environment. First verify `python3.12`, `/bin/ps`, network access and disk capacity on the target host. Building the Web UI from source also requires Node.js 20.19 or later in the 20.x series, or 22.12 or later. Adjust the environment paths for your deployment. Docker is not required for these checks.

```bash
FRAMEWORK_VENV="$HOME/.venvs/iss-eval-framework"
AIPERF_VENV="$HOME/.venvs/iss-eval-aiperf"
python3.12 -m venv "$FRAMEWORK_VENV"
"$FRAMEWORK_VENV/bin/python" -m pip install -e '.[service]' pytest
python3.12 -m venv "$AIPERF_VENV"
"$AIPERF_VENV/bin/python" -m pip install --only-binary=:all: -r requirements/aiperf-linux-x86_64-py312.lock
"$AIPERF_VENV/bin/python" -m pip check
"$AIPERF_VENV/bin/aiperf" --version
export EVALSCOPE_AIPERF_EXECUTABLE="$AIPERF_VENV/bin/aiperf"
export EVALSCOPE_TEST_AIPERF="$EVALSCOPE_AIPERF_EXECUTABLE"
"$FRAMEWORK_VENV/bin/python" -m pytest tests/perf/test_external_runner_lifecycle.py tests/perf/test_experiment_orchestration.py -q
"$FRAMEWORK_VENV/bin/python" -m pytest tests/service/test_aiperf_single_run.py::test_real_single_run_emits_client_progress_log -q
npm --prefix evalscope/web ci
npm --prefix evalscope/web run build
"$FRAMEWORK_VENV/bin/evalscope" service --host 127.0.0.1 --port 9000
```

These automated tests use local mock services and do not validate real P/D nodes; the five-phase cancellation tests can take time. If the service runs in a container, separately verify PID 1 reaping, signal delivery, file permissions, network access and persistent outputs in the actual deployment. A host virtual-environment result is not container acceptance.

### Real-node acceptance

For both vLLM and SGLang, work in a controlled test window and establish a request budget that will not disrupt shared services:

1. Record each P/D node's engine version, model and tokenizer/template, connector, routing, cache configuration, clock synchronization and Prometheus labels. Verify inference and Metrics authentication separately. Keep credentials out of acceptance records and Git.
2. Configure P and D Metrics endpoints in the Web form and run the read-only probe. Start with a small run and check each node's source, labels and availability. Select `0, 80, 90`, two load points and three repetitions, verify the preview's 18 subruns and request budget, then run the experiment. Run an additional open-loop load point.
3. Retain the task ID, plan hash, per-run request hashes, quality reasons, windows and raw/normalized artifacts. Reconcile generated reuse, server-reported request cache usage and node lookup hits with their own numerators, denominators and coverage. These are different observations; they need not equal the target or improve monotonically.
4. Check P/D queue, stage and available KV transfer values against the corresponding node's raw metric, unit, labels, count and window. Record shared traffic and routing coverage. Keep missing or unverified values unavailable/partial; do not substitute a client rate for a node observation.
5. In a safe test environment, stop runs during priming, profiling and import. Confirm request traffic stops, the process tree is cleaned up and partial reports remain readable. Restart the service and confirm history remains readable without automatic resumption. Check native performance tests, capability evaluation and older reports separately.

Record passed, failed and unrun checks with the deployment versions. Mark an environment accepted only after checking its actual nodes, operating system and deployment method.

The service detects the configured executable and requires version 0.13.0. It does not install dependencies at request time or fall back to another executor. Keep the runtime lock, Python version and source artifacts with your deployment; when upgrading AIPerf, recheck its CLI, output schemas, time boundaries and process cleanup before changing the pinned version.

## Configure and run

Set the model connection, choose **AIPerf**, and select either **Prefix cache experiment** or **Single run**. A single run sends each nonblank line as one request. Its browser draft and templates may contain prompt text; do not enter secrets in prompts on a shared browser. The Web form accepts at most 512 KiB of prompt text; the API accepts at most 10,000 prompts and 4 MiB. A single run has one load point and no cache experiment.

For an experiment, enter percentages such as `0, 80, 90`, total input tokens, prefix pool size, seed, load points, request count and repetitions. Preview displays the planned subruns, profiling requests and primer requests. A template can make a short input/ratio combination impossible; preflight rejects known geometry failures. The actual generation still verifies tokenization and can fail closed if the tokenizer cannot construct the requested inputs. Each shared prefix completes its primer before the associated profiling run starts. A fresh namespace is not proof that the server cache was cleared or that every backend received a primer.

Add independent P and D Prometheus targets, choose each node's role and engine, and optionally provide label filters and a bearer token. Model and Metrics authentication are separate. Metrics bearer tokens are used for the task and are not stored in browser drafts, templates or exports; changing the target origin clears its token. URLs with credentials, query strings or fragments are unsupported. In PD mode, requiring Metrics requires both P and D targets. The optional endpoint probe is read-only; startup checks required targets again. Preview again after changing any field.

## Read the results

The report keeps three quantities separate: **target reuse**, **generated token-prefix reuse** and **server-observed cache use**. Request-level cache use is available only when the server returns cached and prompt token usage for the same successful request. A vLLM node lookup-token ratio is a different node-window measurement. Missing or unsupported data is shown as unavailable, never zero. Request latency groups use exact nearest-rank percentiles over verified server usage; grouping is unavailable above 200,000 observed requests. Repetitions remain separate, and P99 values are not averaged into a pooled P99.

The node panel shows independently attributed vLLM/SGLang series, including available queue, prefill/decode and KV transfer measurements. Gauges, counters and histograms have different window semantics: sample peaks are sampled peaks, histogram percentiles are approximate, and coverage, source, unit, count, labels, bounds and reasons are retained. Time-series charts select at most 500 original points evenly by index after matching the exact node, metric, labels and window; peaks can be omitted and sampling may make gaps appear wider. Requests scan at most one million candidate points and otherwise require a narrower window. Raw samples remain available through the paged API. A node window includes shared traffic and is not a per-request P/D trace. The mappings were checked against vLLM 0.10.2 and SGLang 0.5.2/0.5.23 source; verify your deployed versions, labels, clocks, connector and routing. Template match and routing coverage remain unverified until checked in the actual deployment, so results can remain partial even after a successful process exit.

The live panel displays bounded actual node scrapes; failed or stale samples remain marked stale. When AIPerf emits a 0.13.0 realtime client log tick, the panel separately shows completed records and its rounded requests/s; sent requests become available after the send-complete event. Short runs or missing log ticks remain unavailable. Realtime client logging is enabled at a two-second cadence and adds measurement overhead. The log timestamps use the service host local timezone. Explicit comparisons fix load or target ratio and repetition, require matching metric source, scope, unit and AIPerf version, and can compare explicitly selected runs across experiments only when their credential-free configuration identities match. Separate experiments have independent namespaces and are not paired per request. Choose a group from the current matrix page and select “Load all repetitions” to load repetition values and sample counts across the entire experiment in bounded pages. The range covers only compatible loaded observations and does not pool tail percentiles; a failed or incomplete scan is labelled. Running results can be sampled at different times. A zero baseline has no relative percentage change. The standalone HTML streams the full experiment and request records; it can be large.

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

The local restart drill below launches a real Flask service process with the pinned AIPerf virtual environment and a loopback mock server, stops that process, and starts a second process on the same output root without `EVALSCOPE_AIPERF_EXECUTABLE`. It checks that the archived run remains readable through the listing, detail, run, request, chart and HTML endpoints, that the in-memory active-task registry is empty, that a new AIPerf task returns 503 without sending traffic, and that a legacy flat native payload still completes after the restart. A second case kills the service process while a run is active and checks that request traffic stops, the interrupted task is reported as unknown rather than completed, and no run summary is fabricated. The drill does not cover Linux or container cgroup cleanup, a killed controller outside the service process tree, or the server deployment drill.

```bash
EVALSCOPE_TEST_AIPERF="$AIPERF_VENV/bin/aiperf" "$FRAMEWORK_VENV/bin/python" -m pytest tests/service/test_aiperf_service_restart.py -q
```
