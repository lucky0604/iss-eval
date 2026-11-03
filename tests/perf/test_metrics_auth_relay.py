"""The private metrics relay forwards credentials only to the configured node."""

import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

from evalscope.perf.executors.arguments import TelemetryTarget
from evalscope.perf.integrations.aiperf.live import LiveSnapshot
from evalscope.perf.integrations.aiperf.metrics_relay import metrics_auth_relay


@contextmanager
def _endpoint(status: int = 200, redirect: str | None = None) -> Iterator[tuple[str, list]]:
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed.append(self.headers.get('Authorization'))
            self.send_response(status)
            if redirect:
                self.send_header('Location', redirect)
            self.end_headers()
            self.wfile.write(b'# TYPE example gauge\nexample 3\n')

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05})
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/metrics', observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_relay_isolates_node_credentials_and_stops() -> None:
    with _endpoint() as (p, first), _endpoint() as (d, second):
        targets = [
            TelemetryTarget(node_id='p', role='prefill', engine='vllm', url=p,
                            headers={'Authorization': 'Bearer prefill-only'}),
            TelemetryTarget(node_id='d', role='decode', engine='sglang', url=d,
                            headers={'Authorization': 'Bearer decode-only'}),
        ]
        with metrics_auth_relay(targets) as routes:
            assert urlopen(routes.collector_urls['p'], timeout=3).read().endswith(b'example 3\n')
            assert urlopen(routes.collector_urls['d'], timeout=3).read().endswith(b'example 3\n')
            with pytest.raises(HTTPError) as error:
                urlopen(routes.collector_urls['p'] + '/bad', timeout=3)
            assert error.value.code == 404
        assert first == ['Bearer prefill-only'] and second == ['Bearer decode-only']
        with pytest.raises(URLError):
            urlopen(routes.collector_urls['p'], timeout=3)


def test_live_relay_records_unauthenticated_node_without_changing_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evalscope.perf.integrations.aiperf.metrics_relay as relay

    body = b'# TYPE vllm:num_requests_waiting gauge\nvllm:num_requests_waiting 0\n'

    async def fetch(target: TelemetryTarget) -> tuple[bytes, str]:
        return body, 'text/plain'

    monkeypatch.setattr(relay, '_fetch', fetch)
    target = TelemetryTarget(node_id='p', role='prefill', engine='vllm', url='http://localhost/metrics')
    path = tmp_path / 'live.json'
    with metrics_auth_relay([target], path, 2) as routes:
        assert urlopen(routes.collector_urls['p'], timeout=3).read() == body
    snapshot = LiveSnapshot.model_validate_json(path.read_text())
    assert snapshot.nodes[0].gauges[0].value == 0


def test_relay_refuses_redirects_and_header_injection() -> None:
    with _endpoint() as (destination, received), _endpoint(302, destination) as (source, _):
        target = TelemetryTarget(node_id='p', role='prefill', engine='vllm', url=source,
                                 headers={'Authorization': 'Bearer secret'})
        with metrics_auth_relay([target]) as routes:
            with pytest.raises(HTTPError) as error:
                urlopen(routes.collector_urls['p'], timeout=3)
            assert error.value.code == 502
        assert not received
    with pytest.raises(ValueError):
        TelemetryTarget(node_id='p', role='prefill', engine='vllm', url='http://localhost/metrics',
                        headers={'Authorization': 'bad\r\nInjected: secret'})


def test_relay_bounds_slow_stream_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    import evalscope.perf.integrations.aiperf.metrics_relay as relay

    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b'x')
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05})
    thread.start()
    try:
        monkeypatch.setattr(relay, '_RESPONSE_DEADLINE_SECONDS', 0.25)
        target = TelemetryTarget(node_id='p', role='prefill', engine='vllm',
                                 url=f'http://127.0.0.1:{server.server_port}/metrics',
                                 headers={'Authorization': 'Bearer secret'})
        start = time.monotonic()
        with metrics_auth_relay([target]) as routes:
            with pytest.raises(HTTPError) as error:
                urlopen(routes.collector_urls['p'], timeout=3)
            assert error.value.code == 502
        assert time.monotonic() - start < 2
        assert not any(item.name == 'aiperf-metrics-relay' for item in threading.enumerate())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
