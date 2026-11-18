"""The explicit Metrics probe checks endpoints only, without side effects."""

from pathlib import Path

import pytest

from evalscope.perf.integrations.aiperf.preflight import NodePreflight
from evalscope.service.app import create_app
from evalscope.service.utils import log


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from evalscope.service.blueprints import perf

    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    return create_app(outputs=str(tmp_path)).test_client()


def _payload() -> dict:
    return {
        'telemetry': {
            'required': False,
            'sample_interval_seconds': 1,
            'targets': [
                {
                    'node_id': 'prefill',
                    'role': 'prefill',
                    'engine': 'vllm',
                    'url': 'http://10.0.0.1:9000/metrics',
                    'labels': {'model': 'alpha'},
                    'headers': {'Authorization': 'Bearer node-secret'},
                },
                {
                    'node_id': 'decode',
                    'role': 'decode',
                    'engine': 'sglang',
                    'url': 'http://10.0.0.2:9000/metrics',
                },
            ],
        },
    }


def test_probes_each_target_and_returns_only_typed_results(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.service.blueprints import perf

    seen = []

    def probe(targets):
        seen.extend(targets)
        prefill, decode = targets
        return [
            NodePreflight(node_id=prefill.node_id, role=prefill.role, engine=prefill.engine, status='available',
                          metric_names=['vllm:num_requests_waiting'], elapsed_ms=1.5),
            NodePreflight(node_id=decode.node_id, role=decode.role, engine=decode.engine, status='unreachable',
                          elapsed_ms=2.0, reason='metrics_probe_failed'),
        ]

    monkeypatch.setattr(perf, 'probe_nodes', probe)
    response = client.post('/api/v1/perf/telemetry/probe', json=_payload())
    assert response.status_code == 200
    body = response.get_json()
    assert [(node['node_id'], node['status']) for node in body['nodes']] == [
        ('prefill', 'available'), ('decode', 'unreachable')]
    assert body['nodes'][0]['metric_names'] == ['vllm:num_requests_waiting']
    assert 'reason' not in body['nodes'][0]
    assert body['nodes'][1]['reason'] == 'metrics_probe_failed'
    # Credentials reach the probe but never come back, and the endpoint is not echoed.
    assert seen[0].headers['Authorization'].get_secret_value() == 'Bearer node-secret'
    assert seen[0].labels == {'model': 'alpha'}
    text = response.get_data(as_text=True)
    assert 'node-secret' not in text and '10.0.0.1' not in text
    # A probe creates no task directory and nothing else on disk.
    assert list(tmp_path.iterdir()) == []


def test_rejects_malformed_configuration_without_echoing_secrets(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.service.blueprints import perf

    called = []
    monkeypatch.setattr(perf, 'probe_nodes', lambda targets: called.append(targets) or [])

    assert client.post('/api/v1/perf/telemetry/probe', json={}).status_code == 400
    assert client.post('/api/v1/perf/telemetry/probe', json={'telemetry': 'x'}).status_code == 400
    assert client.post('/api/v1/perf/telemetry/probe', json={'telemetry': {'targets': []}}).status_code == 400

    duplicate = _payload()
    duplicate['telemetry']['targets'][1]['node_id'] = 'prefill'
    response = client.post('/api/v1/perf/telemetry/probe', json=duplicate)
    assert response.status_code == 400 and 'unique' in response.get_json()['error']
    assert 'node-secret' not in response.get_data(as_text=True)

    bad_header = _payload()
    bad_header['telemetry']['targets'][0]['headers'] = {'Host': 'Bearer node-secret'}
    response = client.post('/api/v1/perf/telemetry/probe', json=bad_header)
    assert response.status_code == 400
    assert 'node-secret' not in response.get_data(as_text=True)
    assert called == []


def test_reports_unavailable_preflight_as_service_unavailable(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from evalscope.service.blueprints import perf

    def unavailable(targets):
        raise ImportError('prometheus_client missing')

    monkeypatch.setattr(perf, 'probe_nodes', unavailable)
    response = client.post('/api/v1/perf/telemetry/probe', json=_payload())
    assert response.status_code == 503
    assert response.get_json()['error'] == 'Metrics preflight is unavailable'
