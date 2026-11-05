"""Ephemeral per-target authentication transport for AIPerf metrics scrapes."""

import asyncio
import secrets
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Sequence

import aiohttp
from pydantic import Field

from evalscope.perf.executors.arguments import TelemetryTarget
from evalscope.perf.integrations.aiperf.live import LiveRecorder
from evalscope.utils import BaseArgument

_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_RESPONSE_DEADLINE_SECONDS = 5.0


class MetricsRelayRoutes(BaseArgument):
    """Temporary collector URLs indexed by configured node identity."""

    collector_urls: Dict[str, str] = Field(default_factory=dict)


async def _fetch(target: TelemetryTarget) -> tuple[bytes, str]:
    headers = {key: value.get_secret_value() for key, value in target.headers.items()}
    headers['Accept-Encoding'] = 'identity'
    timeout = aiohttp.ClientTimeout(total=_RESPONSE_DEADLINE_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False, auto_decompress=False) as session:
        async with session.get(target.url, headers=headers, allow_redirects=False) as response:
            if response.status < 200 or response.status >= 300:
                raise ValueError('Metrics endpoint returned an unsuccessful status')
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise ValueError('Metrics relay response exceeds size limit')
            content_type = response.headers.get('Content-Type', 'text/plain')
            return bytes(body), content_type


class _RelayServer(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = False
    request_queue_size = 32

    def __init__(self, targets: Sequence[TelemetryTarget], recorder: Optional[LiveRecorder] = None) -> None:
        super().__init__(('127.0.0.1', 0), _RelayHandler)
        self.routes = {f'/{secrets.token_urlsafe(24)}/metrics': target for target in targets}
        self.slots = threading.BoundedSemaphore(16)
        self.recorder = recorder

    def get_request(self) -> tuple:
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        return


class _RelayHandler(BaseHTTPRequestHandler):
    server: _RelayServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        target = self.server.routes.get(self.path)
        if target is None:
            self.send_error(404)
            return
        try:
            body, content_type = asyncio.run(_fetch(target))
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            self._record(target, None)
            try:
                self.send_error(502)
            except OSError:
                pass
            return
        self._record(target, body)

    def _record(self, target: TelemetryTarget, body: Optional[bytes]) -> None:
        if self.server.recorder is not None:
            try:
                self.server.recorder.record(target, body)
            except (OSError, ValueError):
                pass


@contextmanager
def metrics_auth_relay(
    targets: Sequence[TelemetryTarget], live_path: Optional[Path] = None, live_interval: float = 1.0
) -> Iterator[MetricsRelayRoutes]:
    """Forward target responses unchanged and optionally record bounded live gauges."""
    selected = list(targets) if live_path is not None else [target for target in targets if target.headers]
    if not selected:
        yield MetricsRelayRoutes()
        return
    try:
        recorder = LiveRecorder(live_path, selected, live_interval) if live_path is not None else None
    except OSError:
        recorder = None
    server = _RelayServer(selected, recorder)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, name='aiperf-metrics-relay')
    thread.start()
    try:
        port = server.server_port
        yield MetricsRelayRoutes(
            collector_urls={target.node_id: f'http://127.0.0.1:{port}{path}' for path, target in server.routes.items()}
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
