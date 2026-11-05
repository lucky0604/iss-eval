#!/usr/bin/env sh
# Prepare runtime directories, report backend wiring, then exec the EvalScope service
# (or a task-queue worker when the first argument is "worker").
# exec keeps PID 1 signal handling intact so ``docker stop`` reaches the process.
set -eu

OUTPUT_DIR=${EVALSCOPE_OUTPUT_DIR:-/data/outputs}
HOST=${EVALSCOPE_SERVICE_HOST:-0.0.0.0}
PORT=${EVALSCOPE_SERVICE_PORT:-9000}
AIPERF_BIN=${EVALSCOPE_AIPERF_EXECUTABLE:-}

# The service validates --outputs as an existing directory.
mkdir -p "$OUTPUT_DIR"
export EVALSCOPE_OUTPUT_DIR="$OUTPUT_DIR"

FRAMEWORK_VERSION=$(evalscope --version 2>/dev/null | tail -n 1 || true)
printf '[entrypoint] EvalScope: %s\n' "${FRAMEWORK_VERSION:-unknown}"

if [ -z "$AIPERF_BIN" ]; then
    printf '[entrypoint] WARNING: EVALSCOPE_AIPERF_EXECUTABLE is empty; the AIPerf engine will be reported as unavailable.\n' >&2
elif [ ! -x "$AIPERF_BIN" ]; then
    printf '[entrypoint] WARNING: AIPerf executable is missing or not executable: %s\n' "$AIPERF_BIN" >&2
else
    AIPERF_VERSION=$("$AIPERF_BIN" --version 2>&1 | tail -n 1 || true)
    case "$AIPERF_VERSION" in
        *0.13.0*)
            printf '[entrypoint] AIPerf: %s (%s)\n' "$AIPERF_BIN" "$AIPERF_VERSION"
            ;;
        *)
            printf '[entrypoint] WARNING: EvalScope supports AIPerf 0.13.0, found: %s\n' "${AIPERF_VERSION:-unknown}" >&2
            ;;
    esac
fi

printf '[entrypoint] Outputs: %s\n' "$OUTPUT_DIR"

# Worker mode: execute queued jobs instead of serving HTTP. Jobs run in
# subprocesses and need the same output directory and AIPerf executable.
if [ "${1:-}" = "worker" ]; then
    shift
    printf '[entrypoint] Queue backend: %s\n' "${EVALSCOPE_QUEUE_BACKEND:-inprocess}"
    exec evalscope worker "$@"
fi

printf '[entrypoint] Dashboard: http://%s:%s/dashboard\n' "$HOST" "$PORT"

# Extra arguments are appended to the service command line (e.g. --debug).
exec evalscope service --host "$HOST" --port "$PORT" --outputs "$OUTPUT_DIR" "$@"
