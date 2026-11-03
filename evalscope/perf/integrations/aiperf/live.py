"""Bounded, node-scoped snapshots from actual Prometheus scrape responses."""

import math
import threading
import time
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import Field

from evalscope.perf.executors.arguments import TelemetryTarget
from evalscope.perf.experiments.storage import write_experiment_json
from evalscope.perf.integrations.aiperf.metrics import MAPPINGS
from evalscope.utils import BaseArgument


class LiveGauge(BaseArgument):
    """A single unaggregated gauge series from one actual node scrape."""

    name: str
    key: str
    labels: Dict[str, str]
    value: float
    unit: str


class LiveNode(BaseArgument):
    node_id: str
    role: Literal['prefill', 'decode']
    engine: Literal['vllm', 'sglang']
    status: Literal['waiting', 'available', 'unavailable'] = 'waiting'
    sampled_at_ns: Optional[int] = None
    attempted_at_ns: Optional[int] = None
    truncated: bool = False
    gauges: List[LiveGauge] = Field(default_factory=list, max_length=16)


class LiveSnapshot(BaseArgument):
    expected_interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    nodes: List[LiveNode] = Field(default_factory=list, max_length=64)


class LiveRecorder:
    """Atomically publish the latest bounded samples without altering scrape responses."""

    def __init__(self, path: Path, targets: List[TelemetryTarget], interval: float) -> None:
        self.path = path
        self.interval = interval
        self.lock = threading.Lock()
        self.nodes = {
            target.node_id: LiveNode(node_id=target.node_id, role=target.role, engine=target.engine)
            for target in targets
        }
        self._publish()

    def _publish(self) -> None:
        write_experiment_json(
            self.path,
            LiveSnapshot(expected_interval_seconds=self.interval, nodes=list(self.nodes.values())).model_dump(
                mode='json'
            ),
        )

    def record(self, target: TelemetryTarget, body: Optional[bytes]) -> None:
        """Record success or failure, preserving the last successful sample on failure."""
        now = time.time_ns()
        gauges = None
        truncated = False
        if body is not None and len(body) <= 2 * 1024 * 1024:
            try:
                from prometheus_client.parser import text_string_to_metric_families

                mappings = {
                    (item.name, item.kind): item
                    for item in MAPPINGS
                    if item.engine == target.engine and item.kind == 'gauge'
                }
                gauges = []
                for family in text_string_to_metric_families(body.decode('utf-8')):
                    mapping = mappings.get((family.name, family.type))
                    if mapping is None:
                        continue
                    for sample in family.samples:
                        if sample.name != mapping.name or not all(
                            sample.labels.get(k) == v for k, v in target.labels.items()
                        ):
                            continue
                        if not math.isfinite(sample.value):
                            continue
                        if len(gauges) == 16:
                            truncated = True
                            break
                        if len(sample.labels) > 8 or any(
                            len(k.encode()) > 64 or len(v.encode()) > 64 for k, v in sample.labels.items()
                        ):
                            truncated = True
                            continue
                        gauges.append(
                            LiveGauge(
                                name=mapping.name,
                                key=mapping.key,
                                labels=sample.labels,
                                value=sample.value * mapping.scale,
                                unit=mapping.unit,
                            )
                        )
                    if truncated and len(gauges) == 16:
                        break
            except (UnicodeError, ValueError, TypeError):
                gauges = None
        with self.lock:
            node = self.nodes[target.node_id]
            node.attempted_at_ns = now
            if gauges is None:
                node.status = 'unavailable'
            else:
                node.status = 'available'
                node.sampled_at_ns = now
                node.gauges = gauges
                node.truncated = truncated
            self._publish()
