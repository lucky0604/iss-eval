"""Sequential primer barrier for validated prefix workloads."""

import asyncio
import math
import multiprocessing
from typing import List, Literal, Optional

import aiohttp
from pydantic import Field

from evalscope.perf.executors.arguments import AIPerfArguments
from evalscope.perf.executors.external import Cancellation
from evalscope.perf.executors.urls import normalize_chat_url
from evalscope.perf.experiments.prefix_workload import PrefixWorkload
from evalscope.perf.experiments.stream_probe import request_stream_completion
from evalscope.utils import BaseArgument


class PrimingResult(BaseArgument):
    """Primer completion and routing coverage, separate from cache-hit metrics."""

    status: Literal['completed', 'skipped', 'error', 'cancelled']
    completed_prefix_ids: List[int] = Field(default_factory=list)
    failed_prefix_id: Optional[int] = None
    reason: Optional[str] = None
    routing_coverage: Literal['unknown'] = 'unknown'
    timeout_seconds: float = Field(default=30, gt=0, le=3600, allow_inf_nan=False)


async def prime_prefixes(
    arguments: AIPerfArguments,
    workload: PrefixWorkload,
    cancel_event: Optional[Cancellation] = None,
    timeout_seconds: float = 30,
) -> PrimingResult:
    """Wait for every primer before allowing profiling; stop on first failure."""
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError('Primer timeout must be positive')
    if arguments.headers:
        raise ValueError('Custom inference headers are not supported for primers')
    primer_ids = [item.prefix_id for item in workload.primers]
    if any(item is None for item in primer_ids) or len(set(primer_ids)) != len(primer_ids):
        raise ValueError('Primer prefix IDs must be unique and present')
    if workload.target_ratio and set(primer_ids) != {item.prefix_id for item in workload.requests}:
        raise ValueError('Every profiling prefix must have a primer')
    if not workload.target_ratio and workload.primers:
        raise ValueError('A zero-ratio workload must not contain primers')
    if not workload.primers:
        return PrimingResult(status='skipped', timeout_seconds=timeout_seconds)
    base, endpoint = normalize_chat_url(arguments.url)
    url = base + endpoint
    headers = {}
    if arguments.api_key is not None:
        headers['Authorization'] = f'Bearer {arguments.api_key.get_secret_value()}'
    completed: List[int] = []
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session:
        for primer in workload.primers:
            parent = multiprocessing.parent_process()
            if (cancel_event is not None and cancel_event.is_set()) or (parent is not None and not parent.is_alive()):
                return PrimingResult(
                    status='cancelled',
                    completed_prefix_ids=completed,
                    reason='cancelled',
                    timeout_seconds=timeout_seconds,
                )
            task = asyncio.create_task(request_stream_completion(session, url, arguments.model, primer.text, 1))
            try:
                while not task.done():
                    if (cancel_event is not None and cancel_event.is_set()) or (
                        parent is not None and not parent.is_alive()
                    ):
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        return PrimingResult(
                            status='cancelled',
                            completed_prefix_ids=completed,
                            reason='cancelled',
                            timeout_seconds=timeout_seconds,
                        )
                    await asyncio.wait({task}, timeout=0.05)
                result = task.result()
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            if result != 'completed':
                return PrimingResult(
                    status='error',
                    completed_prefix_ids=completed,
                    failed_prefix_id=primer.prefix_id,
                    reason=result,
                    timeout_seconds=timeout_seconds,
                )
            assert primer.prefix_id is not None
            completed.append(primer.prefix_id)
        if cancel_event is not None and cancel_event.is_set():
            return PrimingResult(
                status='cancelled', completed_prefix_ids=completed, reason='cancelled', timeout_seconds=timeout_seconds
            )
    return PrimingResult(status='completed', completed_prefix_ids=completed, timeout_seconds=timeout_seconds)
