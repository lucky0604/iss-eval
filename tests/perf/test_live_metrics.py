"""Live snapshots contain only bounded observations from real node responses."""

from pathlib import Path

from evalscope.perf.executors.arguments import TelemetryTarget
from evalscope.perf.integrations.aiperf.live import LiveRecorder, LiveSnapshot


def test_live_samples_preserve_zero_labels_and_last_value_after_failure(tmp_path: Path) -> None:
    target = TelemetryTarget(node_id='p', role='prefill', engine='vllm', url='http://localhost:8080/metrics',
                             labels={'model': 'a'}, headers={'Authorization': 'Bearer secret'})
    path = tmp_path / 'live.json'
    recorder = LiveRecorder(path, [target], 2.5)
    recorder.record(target, b'# TYPE vllm:num_requests_waiting gauge\n'
                    b'vllm:num_requests_waiting{model="a"} 0\n'
                    b'vllm:num_requests_waiting{model="b"} 9\n')
    snapshot = LiveSnapshot.model_validate_json(path.read_text())
    node = snapshot.nodes[0]
    assert snapshot.expected_interval_seconds == 2.5
    assert node.status == 'available' and node.gauges[0].value == 0
    assert len(node.gauges) == 1 and node.gauges[0].labels == {'model': 'a'}
    assert 'secret' not in path.read_text()
    recorder.record(target, None)
    failed = LiveSnapshot.model_validate_json(path.read_text()).nodes[0]
    assert failed.status == 'unavailable' and failed.sampled_at_ns == node.sampled_at_ns
    assert failed.gauges == node.gauges


def test_live_series_are_bounded(tmp_path: Path) -> None:
    target = TelemetryTarget(node_id='d', role='decode', engine='sglang', url='http://localhost:8080/metrics')
    recorder = LiveRecorder(tmp_path / 'live.json', [target], 1)
    body = '# TYPE sglang:num_queue_reqs gauge\n' + ''.join(
        f'sglang:num_queue_reqs{{worker="{index}"}} {index}\n' for index in range(30)
    )
    recorder.record(target, body.encode())
    node = LiveSnapshot.model_validate_json((tmp_path / 'live.json').read_text()).nodes[0]
    assert node.truncated and len(node.gauges) == 16
