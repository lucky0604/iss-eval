"""Explicit dispatch between the native and external performance engines."""

from typing import Any, Optional

from evalscope.perf.executors.arguments import PerfTaskArguments
from evalscope.perf.executors.models import ExecutionContext
from evalscope.perf.main import run_perf_benchmark


def run_perf_task(arguments: PerfTaskArguments, context: ExecutionContext, input_file: Optional[str] = None) -> Any:
    """Dispatch a typed task, using a server-owned input file for AIPerf."""
    if arguments.engine == 'evalscope':
        assert arguments.native is not None
        return run_perf_benchmark(arguments.native)
    if input_file is None:
        raise ValueError('A server-owned input file is required for AIPerf')
    from evalscope.perf.integrations.aiperf.single_run import run_single

    assert arguments.aiperf is not None
    return run_single(arguments.aiperf, context, input_file)
