"""Read-only, credential-isolated Prometheus capability checks."""

import asyncio
import time
from typing import List, Literal, Sequence

import aiohttp
from pydantic import Field

from evalscope.perf.executors.arguments import TelemetryTarget
from evalscope.perf.integrations.aiperf.metrics import MAPPINGS
from evalscope.perf.integrations.aiperf.metrics_relay import _fetch
from evalscope.utils import BaseArgument


class NodePreflight(BaseArgument):
    """A single endpoint's reachability and documented matching families."""

    node_id: str
    role: Literal['prefill', 'decode']
    engine: Literal['vllm', 'sglang']
    status: Literal['available', 'missing', 'unreachable', 'invalid']
    metric_names: List[str] = Field(default_factory=list)
    elapsed_ms: float = Field(ge=0)
    reason: str | None = None
    verification: Literal['deployment_unverified'] = 'deployment_unverified'


async def _probe_one(target: TelemetryTarget, semaphore: asyncio.Semaphore) -> NodePreflight:
    from prometheus_client.parser import text_string_to_metric_families

    started = time.monotonic()
    known = {(item.name, item.kind) for item in MAPPINGS if item.engine == target.engine}
    try:
        async with semaphore:
            body, _ = await _fetch(target)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        return NodePreflight(
            node_id=target.node_id,
            role=target.role,
            engine=target.engine,
            status='unreachable',
            elapsed_ms=(time.monotonic() - started) * 1000,
            reason='metrics_probe_failed',
        )
    try:
        families = text_string_to_metric_families(body.decode('utf-8'))
        matched = set()
        for family in families:
            if (family.name, family.type) not in known:
                continue
            for sample in family.samples:
                labels = {key: value for key, value in sample.labels.items() if key not in ('le', 'quantile')}
                if all(labels.get(key) == value for key, value in target.labels.items()):
                    matched.add(family.name)
                    break
        status = 'available' if matched else 'missing'
        return NodePreflight(
            node_id=target.node_id,
            role=target.role,
            engine=target.engine,
            status=status,
            metric_names=sorted(matched),
            elapsed_ms=(time.monotonic() - started) * 1000,
            reason=None if matched else 'no_matching_documented_metrics',
        )
    except (ValueError, TypeError, IndexError):
        return NodePreflight(
            node_id=target.node_id,
            role=target.role,
            engine=target.engine,
            status='invalid',
            elapsed_ms=(time.monotonic() - started) * 1000,
            reason='invalid_prometheus_payload',
        )


async def _probe_all(targets: Sequence[TelemetryTarget]) -> List[NodePreflight]:
    semaphore = asyncio.Semaphore(8)
    return list(await asyncio.gather(*(_probe_one(target, semaphore) for target in targets)))


def probe_nodes(targets: Sequence[TelemetryTarget]) -> List[NodePreflight]:
    """Fetch configured Metrics endpoints only; do not send inference or cache-reset requests."""
    return asyncio.run(_probe_all(targets))
