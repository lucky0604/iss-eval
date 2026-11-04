"""Generic pre-run warmup traffic with an explicit completion barrier."""

import asyncio
import math
import multiprocessing
from typing import List, Literal, Optional

import aiohttp
from pydantic import Field

from evalscope.perf.executors.arguments import AIPerfArguments, WarmupConfiguration
from evalscope.perf.executors.external import Cancellation
from evalscope.perf.executors.urls import normalize_chat_url
from evalscope.perf.experiments.prefix_workload import PrefixWorkload
from evalscope.perf.experiments.stream_probe import request_stream_completion
from evalscope.utils import BaseArgument


class WarmupResult(BaseArgument):
    """Outcome of synthetic warmup traffic, excluded from profiling metrics."""

    status: Literal['completed', 'failed', 'cancelled']
    request_count: int = Field(ge=0)
    completed_requests: int = Field(ge=0)
    failed_requests: int = Field(ge=0)
    concurrency: int = Field(gt=0)
    total_input_tokens: int = Field(gt=0)
    output_token_limit: int = Field(gt=0)
    reason: Optional[str] = None


def _cancelled(cancel_event: Optional[Cancellation]) -> bool:
    parent = multiprocessing.parent_process()
    return bool((cancel_event is not None and cancel_event.is_set()) or (parent is not None and not parent.is_alive()))


async def run_warmup(
    arguments: AIPerfArguments,
    warmup: WarmupConfiguration,
    workload: PrefixWorkload,
    cancel_event: Optional[Cancellation] = None,
    timeout_seconds: float = 600,
) -> WarmupResult:
    """Send all warmup requests with bounded concurrency and no result import."""
    if len(workload.requests) != warmup.request_count:
        raise ValueError('Warmup workload must contain the configured request count')
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError('Warmup timeout must be positive')
    if arguments.headers:
        raise ValueError('Custom inference headers are not supported for warmup')
    base, endpoint = normalize_chat_url(arguments.url)
    headers = {}
    if arguments.api_key is not None:
        headers['Authorization'] = f'Bearer {arguments.api_key.get_secret_value()}'
    semaphore = asyncio.Semaphore(warmup.concurrency)
    results: List[str] = []

    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session:

        async def send(text: str) -> None:
            async with semaphore:
                result = await request_stream_completion(
                    session, base + endpoint, arguments.model, text, warmup.output_token_limit
                )
                results.append(result)

        tasks = [asyncio.create_task(send(item.text)) for item in workload.requests]
        try:
            while not all(task.done() for task in tasks):
                if _cancelled(cancel_event):
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    return WarmupResult(
                        status='cancelled',
                        request_count=warmup.request_count,
                        completed_requests=sum(result == 'completed' for result in results),
                        failed_requests=sum(result != 'completed' for result in results),
                        concurrency=warmup.concurrency,
                        total_input_tokens=warmup.total_input_tokens,
                        output_token_limit=warmup.output_token_limit,
                        reason='cancelled',
                    )
                await asyncio.wait(tasks, timeout=0.05, return_when=asyncio.FIRST_COMPLETED)
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    failed = sum(result != 'completed' for result in results)
    return WarmupResult(
        status='completed' if failed == 0 else 'failed',
        request_count=warmup.request_count,
        completed_requests=len(results) - failed,
        failed_requests=failed,
        concurrency=warmup.concurrency,
        total_input_tokens=warmup.total_input_tokens,
        output_token_limit=warmup.output_token_limit,
        reason=None if failed == 0 else next(result for result in results if result != 'completed'),
    )
