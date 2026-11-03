"""Loopback-only OpenAI streaming and Prometheus fixture for AIPerf integration tests."""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional


class MockServer(ThreadingHTTPServer):
    """A small local server that records every inference request."""

    def __init__(self, capture: Path, mode: str = 'success', metrics_mode: str = 'normal',
                 metrics_capture: Optional[Path] = None) -> None:
        super().__init__(('127.0.0.1', 0), MockHandler)
        self.capture = capture
        self.mode = mode
        self.metrics_mode = metrics_mode
        self.metrics_capture = metrics_capture
        self.lock = threading.Lock()
        self.requests_seen = 0
        self.scrapes_seen = 0


class MockHandler(BaseHTTPRequestHandler):
    """Respond to chat completions and expose deterministic test metrics."""

    server: MockServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path != '/metrics':
            self.send_error(404)
            return
        with self.server.lock:
            self.server.scrapes_seen += 1
            scrapes_seen = self.server.scrapes_seen
            count = self.server.requests_seen
            if self.server.metrics_capture is not None:
                with self.server.metrics_capture.open('a', encoding='utf-8') as capture:
                    capture.write(json.dumps({'authorization_present': bool(self.headers.get('Authorization'))}) + '\n')
        if self.server.metrics_mode == 'unavailable':
            self.send_error(503)
            return
        if self.server.metrics_mode == 'reset':
            count = 100 if scrapes_seen == 1 else 1
        if self.server.metrics_mode == 'missing' or (self.server.metrics_mode == 'delayed' and scrapes_seen == 1):
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        body = (
            '# TYPE mock_requests_total counter\n'
            f'mock_requests_total{{role="prefill"}} {count}\n'
            '# TYPE mock_queue gauge\n'
            'mock_queue{role="prefill"} 0\n'
            '# TYPE mock_latency_seconds histogram\n'
            f'mock_latency_seconds_bucket{{role="prefill",le="1"}} {count}\n'
            f'mock_latency_seconds_bucket{{role="prefill",le="+Inf"}} {count}\n'
            f'mock_latency_seconds_sum{{role="prefill"}} 0\n'
            f'mock_latency_seconds_count{{role="prefill"}} {count}\n'
        ).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; version=0.0.4')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path.split('?', 1)[0] != '/v1/chat/completions':
            self.send_error(404)
            return
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with self.server.lock:
            self.server.requests_seen += 1
            request_number = self.server.requests_seen
            with self.server.capture.open('a', encoding='utf-8') as capture:
                capture.write(json.dumps({
                    'path': self.path,
                    'body': payload,
                    'fixture_authorization_matches': self.headers.get('Authorization') == 'Bearer fixture-key',
                    'fixture_header_matches': self.headers.get('X-Fixture') == 'through',
                }) + '\n')
        if self.server.mode == 'http_error' or (self.server.mode == 'alternating_error' and request_number % 2 == 0):
            self.send_error(503)
            return
        if self.server.mode == 'timeout':
            time.sleep(30)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        if self.server.mode == 'stream_error':
            self.wfile.write(b'data: {"error":{"message":"fixture error"}}\n\n')
            self.wfile.flush()
            return
        base = {'id': 'fixture', 'object': 'chat.completion.chunk', 'model': payload.get('model', 'mock')}
        chunks = [
            {**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'hello'}, 'finish_reason': None}]},
            {**base, 'choices': [{'index': 0, 'delta': {'content': ' world'}, 'finish_reason': None}]},
            {**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        ]
        if self.server.mode == 'single_token':
            del chunks[1]
        if self.server.mode == 'unfinished':
            del chunks[2]
        if self.server.mode != 'missing_usage':
            output_tokens = 1 if self.server.mode == 'single_token' else 2
            usage = {'prompt_tokens': 8, 'completion_tokens': output_tokens, 'total_tokens': 8 + output_tokens}
            if self.server.mode == 'cache_usage':
                usage['prompt_tokens_details'] = {'cached_tokens': 6}
            chunks.append({**base, 'choices': [], 'usage': usage})
        for chunk in chunks:
            self.wfile.write(f'data: {json.dumps(chunk)}\n\n'.encode())
            self.wfile.flush()
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


def main() -> None:
    """Run the fixture until interrupted, printing its dynamically assigned port."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--mode', choices=['success', 'cache_usage', 'missing_usage', 'http_error', 'alternating_error', 'stream_error',
                                           'single_token', 'unfinished', 'timeout'], default='success')
    parser.add_argument('--metrics-mode', choices=['normal', 'missing', 'unavailable', 'reset', 'delayed'],
                        default='normal')
    parser.add_argument('--metrics-capture', type=Path)
    args = parser.parse_args()
    server = MockServer(args.capture, args.mode, args.metrics_mode, args.metrics_capture)
    print(server.server_port, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
