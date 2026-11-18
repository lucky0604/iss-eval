"""Dynamo PD frontend/router families stay visible in node windows."""

from evalscope.perf.integrations.aiperf.metrics import project_node_metrics
from evalscope.perf.results.models import NodeMetricSample
from evalscope.perf.results.telemetry import MetricWindow

WINDOW = MetricWindow(start_ns=1_000, end_ns=9_000)
LABELS = {'model': 'glm-5.3-flash'}


def _sample(timestamp_ns: int, name: str, kind: str, observation: dict) -> NodeMetricSample:
    return NodeMetricSample(
        timestamp_ns=timestamp_ns,
        node_id='dynamo-frontend',
        role='frontend',
        engine='dynamo',
        endpoint='prometheus://dynamo-frontend/metrics',
        metric_name=name,
        metric_type=kind,
        labels=LABELS,
        observation=observation,
    )


def test_dynamo_frontend_and_router_metrics_are_projected() -> None:
    samples = [
        _sample(2_000, 'dynamo_frontend_queued_requests', 'gauge', {'value': 0}),
        _sample(7_000, 'dynamo_frontend_queued_requests', 'gauge', {'value': 4}),
        _sample(
            2_000,
            'dynamo_frontend_time_to_first_token_seconds',
            'histogram',
            {'buckets': {'0.1': 1, '+Inf': 2}, 'sum': 0.3, 'count': 2},
        ),
        _sample(
            7_000,
            'dynamo_frontend_time_to_first_token_seconds',
            'histogram',
            {'buckets': {'0.1': 2, '+Inf': 4}, 'sum': 0.5, 'count': 4},
        ),
        _sample(
            2_000,
            'dynamo_component_router_kv_hit_rate',
            'histogram',
            {'buckets': {'0.5': 1, '+Inf': 1}, 'sum': 0.4, 'count': 1},
        ),
        _sample(
            7_000,
            'dynamo_component_router_kv_hit_rate',
            'histogram',
            {'buckets': {'0.5': 1, '+Inf': 2}, 'sum': 0.9, 'count': 2},
        ),
    ]
    rows = project_node_metrics(samples, WINDOW, 5_000, 'observed_request_bounds')
    by_key = {row.metric.key: row for row in rows}
    assert set(by_key) == {'waiting_requests', 'ttft', 'router_kv_hit_rate'}
    assert all(row.role == 'frontend' and row.engine == 'dynamo' for row in rows)
    assert by_key['waiting_requests'].statistic is not None
    assert by_key['ttft'].histogram is not None
    assert by_key['router_kv_hit_rate'].histogram is not None
