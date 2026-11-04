#!/usr/bin/env sh
# Start the local EvalScope service for AIPerf runs.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

FRAMEWORK_BIN=${EVALSCOPE_FRAMEWORK_BIN:-"$REPO_ROOT/.venv/bin/evalscope"}
AIPERF_BIN=${EVALSCOPE_AIPERF_EXECUTABLE:-"$HOME/.venvs/aiperf/bin/aiperf"}
OUTPUT_DIR=${EVALSCOPE_OUTPUT_DIR:-"$REPO_ROOT/outputs"}
HOST=${EVALSCOPE_SERVICE_HOST:-127.0.0.1}
PORT=${EVALSCOPE_SERVICE_PORT:-9000}

if [ ! -x "$FRAMEWORK_BIN" ]; then
    echo "error: EvalScope executable not found: $FRAMEWORK_BIN" >&2
    echo "Create the framework environment first:" >&2
    echo "  python3.12 -m venv .venv" >&2
    echo "  .venv/bin/pip install -e '.[service]'" >&2
    exit 1
fi

if [ ! -x "$AIPERF_BIN" ]; then
    echo "error: AIPerf executable not found: $AIPERF_BIN" >&2
    echo "Create the runtime environment or set EVALSCOPE_AIPERF_EXECUTABLE." >&2
    exit 1
fi

if ! AIPERF_VERSION=$("$AIPERF_BIN" --version 2>&1); then
    echo "error: failed to run $AIPERF_BIN --version" >&2
    exit 1
fi
AIPERF_VERSION=$(printf '%s\n' "$AIPERF_VERSION" | tail -n 1)
case "$AIPERF_VERSION" in
    *0.13.0*) ;;
    *)
        echo "error: EvalScope supports AIPerf 0.13.0, found: $AIPERF_VERSION" >&2
        exit 1
        ;;
esac

if [ ! -f "$REPO_ROOT/evalscope/web/dist/index.html" ]; then
    echo "error: the web dashboard is not built." >&2
    echo "Build it once with: make web-build" >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
export EVALSCOPE_AIPERF_EXECUTABLE="$AIPERF_BIN"
export EVALSCOPE_OUTPUT_DIR="$OUTPUT_DIR"

echo "AIPerf:    $AIPERF_BIN ($AIPERF_VERSION)"
echo "Outputs:   $OUTPUT_DIR"
echo "Dashboard: http://$HOST:$PORT/"
echo "Press Ctrl+C to stop."

exec "$FRAMEWORK_BIN" service --host "$HOST" --port "$PORT" --outputs "$OUTPUT_DIR"
