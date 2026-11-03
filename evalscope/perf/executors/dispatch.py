"""Explicit dispatch between the native and external performance engines."""

from typing import Any

from evalscope.perf.executors.arguments import PerfTaskArguments
from evalscope.perf.executors.models import ExecutionContext
from evalscope.perf.main import run_perf_benchmark


def run_perf_task(arguments: PerfTaskArguments, context: ExecutionContext) -> Any:
    """Run a typed task without changing the native benchmark entry point."""
    if arguments.engine == 'evalscope':
        assert arguments.native is not None
        return run_perf_benchmark(arguments.native)
    raise NotImplementedError('AIPerf executor is not enabled')
