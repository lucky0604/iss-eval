"""Restarting the real service process keeps archives readable and the runtime disabled."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

import pytest

from tests.perf.test_prefix_priming import _server

pytestmark = pytest.mark.skipif(not os.environ.get('EVALSCOPE_TEST_AIPERF'), reason='requires isolated AIPerf 0.13.0')

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


class ServiceProcess:
    """A real Flask service process with its own output root and environment."""

    def __init__(self, root: Path, runtime: Optional[str], log_path: Path) -> None:
        self.port = _free_port()
        self.log_path = log_path
        self.process: Optional[subprocess.Popen] = None
        self._log = log_path.open('w', encoding='utf-8')
        env = dict(os.environ)
        env['EVALSCOPE_OUTPUT_DIR'] = str(root)
        env['NO_PROXY'] = '127.0.0.1,localhost'
        env['no_proxy'] = env['NO_PROXY']
        env['PYTHONUNBUFFERED'] = '1'
        env.pop('EVALSCOPE_AIPERF_EXECUTABLE', None)
        if runtime is not None:
            env['EVALSCOPE_AIPERF_EXECUTABLE'] = runtime
        code = (
            'from evalscope.service import run_service; '
            f'run_service(host="127.0.0.1", port={self.port}, outputs={str(root)!r})'
        )
        try:
            self.process = subprocess.Popen(
                [sys.executable, '-c', code],
                cwd=str(_REPO_ROOT),
                env=env,
                stdout=self._log,
                stderr=subprocess.STDOUT,
            )
            self._wait_until_ready()
        except BaseException:
            self.stop()
            raise

    def _wait_until_ready(self) -> None:
        assert self.process is not None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(f'Service exited before becoming ready:\n{self.log_tail()}')
            try:
                status, _ = self.request('GET', '/health', timeout=1)
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
                continue
            if status == 200:
                return
            time.sleep(.1)
        raise AssertionError(f'Service did not become ready:\n{self.log_tail()}')

    def request(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: float = 300,
    ) -> tuple[int, bytes]:
        body = json.dumps(payload).encode('utf-8') if payload is not None else None
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', data=body, method=method)
        if body is not None:
            request.add_header('Content-Type', 'application/json')
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def json(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: float = 300,
    ) -> tuple[int, Any]:
        status, body = self.request(method, path, payload, headers, timeout)
        return status, json.loads(body) if body else None

    def log_tail(self) -> str:
        try:
            return self.log_path.read_text(encoding='utf-8', errors='replace')[-4000:]
        except OSError:
            return ''

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._log.close()

    def __enter__(self) -> 'ServiceProcess':
        return self

    def __exit__(self, exc_type: type, exc: BaseException, traceback: Any) -> None:
        self.stop()


def _tokenizer(directory: Path) -> Path:
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    backend = Tokenizer(models.WordLevel({'[UNK]': 0, 'first': 1, 'second': 2, 'prompt': 3}, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]').save_pretrained(directory)
    return directory


def _payload(url: str, tokenizer_path: Path) -> dict:
    return {
        'engine': 'aiperf',
        'aiperf': {
            'model': 'model',
            'url': url,
            'tokenizer_path': str(tokenizer_path),
            'api_key': 'fixture-key',
            'input_prompts': ['first prompt', 'second prompt'],
            'load': {'points': [{'concurrency': 1}], 'request_count': 2, 'output_token_limit': 7},
        },
    }


def _capture_count(capture: Path) -> int:
    try:
        return sum(1 for line in capture.read_text(encoding='utf-8').splitlines() if line.strip())
    except OSError:
        return 0


def _wait_for_requests(capture: Path, minimum: int, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _capture_count(capture) >= minimum:
            return
        time.sleep(.1)
    raise AssertionError(f'Mock server received fewer than {minimum} requests')


def test_restart_reads_archive_without_runtime(tmp_path: Path) -> None:
    runtime = os.environ['EVALSCOPE_TEST_AIPERF']
    root = tmp_path / 'outputs'
    root.mkdir()
    tokenizer_path = _tokenizer(tmp_path / 'tokenizer')
    dataset = tmp_path / 'native.txt'
    dataset.write_text('first prompt\nsecond prompt\n')

    with _server(tmp_path / 'inference', 'success') as (url, capture):
        payload = _payload(url, tokenizer_path)
        with ServiceProcess(root, runtime, tmp_path / 'first-service.log') as first:
            status, body = first.json(
                'POST', '/api/v1/perf/invoke', payload, headers={'EvalScope-Task-Id': 'before-restart'}
            )
            assert status == 200 and body['status'] == 'completed', (status, body, first.log_tail())
            assert json.loads(first.request('GET', '/api/v1/tasks/active')[1])['total'] == 0
        first_pid = first.process.pid
        assert first.process.poll() is not None
        assert len(capture.read_text().splitlines()) == 2

        # The second process starts from the same output root with the runtime disabled.
        with ServiceProcess(root, None, tmp_path / 'second-service.log') as second:
            assert second.process.pid != first_pid
            status, engines = second.json('GET', '/api/v1/perf/engines')
            aiperf = next(item for item in engines['engines'] if item['engine'] == 'aiperf')
            assert status == 200 and not aiperf['available']
            assert 'unavailable' in aiperf['reason']
            assert runtime not in json.dumps(engines)

            status, active = second.json('GET', '/api/v1/tasks/active')
            assert status == 200 and active['total'] == 0

            status, task = second.json('GET', '/api/v1/tasks/before-restart')
            assert status == 200 and task['status'] == 'completed'
            assert task['kind'] == 'perf' and task['engine'] == 'aiperf'
            assert task['html_report_url'] and task['report_url']

            params = {'root_path': str(root)}
            status, listing = second.json('GET', f"/api/v1/perf/list?{urllib.parse.urlencode(params)}")
            assert status == 200 and listing['total'] == 1
            assert listing['runs'][0]['engine'] == 'aiperf'
            assert listing['runs'][0]['executor_version'] == '0.13.0'

            run_params = {**params, 'path': 'before-restart/perf'}
            query = urllib.parse.urlencode(run_params)
            status, detail = second.json('GET', f'/api/v1/perf/detail?{query}')
            assert status == 200 and detail['engine'] == 'aiperf'
            assert detail['executor_version'] == '0.13.0'
            assert detail['quality'] in ('complete', 'partial')

            status, runs = second.json('GET', f'/api/v1/perf/runs?{query}')
            assert status == 200 and runs['total'] == 1
            assert runs['runs'][0]['succeed_requests'] == 2 and runs['runs'][0]['has_requests']

            request_params = {**run_params, 'run': 'single'}
            status, requests = second.json('GET', f"/api/v1/perf/requests?{urllib.parse.urlencode(request_params)}")
            assert status == 200 and requests['total'] == 2 and requests['has_db']
            assert len(requests['rows']) == 2
            assert 'fixture-key' not in json.dumps(requests)

            status, chart = second.request('GET', f"/api/v1/perf/chart?{query}&chart_type=rps")
            assert status == 200 and b'plotly' in chart

            status, report = second.request('GET', f'/api/v1/perf/history/report?{query}')
            assert status == 200
            report_text = report.decode('utf-8')
            assert 'evalscope-aiperf-single-report:1' in report_text
            assert 'fixture-key' not in report_text

            # A new AIPerf task must fail explicitly without sending traffic or creating a task directory.
            status, rejected = second.json(
                'POST', '/api/v1/perf/invoke', payload, headers={'EvalScope-Task-Id': 'after-restart'}
            )
            assert status == 503 and 'unavailable' in rejected['error']
            assert not (root / 'after-restart').exists()
            assert len(capture.read_text().splitlines()) == 2

            # Keep the flat legacy payload working: no `engine` field means the native executor.
            native = {
                'model': 'model',
                'url': url,
                'api_key': 'fixture-key',
                'api': 'openai',
                'dataset': 'custom',
                'dataset_path': str(dataset),
                'number': 2,
                'parallel': 1,
                'max_tokens': 7,
            }
            status, result = second.json(
                'POST', '/api/v1/perf/invoke', native, headers={'EvalScope-Task-Id': 'native-after-restart'}
            )
            assert status == 200 and result['status'] == 'completed', (status, result, second.log_tail())

            native_params = {'root_path': str(root), 'path': 'native-after-restart/perf'}
            status, native_runs = second.json('GET', f"/api/v1/perf/runs?{urllib.parse.urlencode(native_params)}")
            assert status == 200 and native_runs['runs'][0]['succeed_requests'] == 2
        assert second.process.poll() is not None

    sent = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(sent) == 4
    assert {item['body']['messages'][0]['content'] for item in sent[2:]} == {'first prompt', 'second prompt'}


def test_crashed_service_stops_traffic_and_does_not_fabricate_completion(tmp_path: Path) -> None:
    runtime = os.environ['EVALSCOPE_TEST_AIPERF']
    root = tmp_path / 'outputs'
    root.mkdir()
    tokenizer_path = _tokenizer(tmp_path / 'tokenizer')

    with _server(tmp_path / 'inference', 'slow_success') as (url, capture):
        payload = _payload(url, tokenizer_path)
        payload['aiperf']['input_prompts'] = ['prompt'] * 120
        payload['aiperf']['load'] = {'points': [{'concurrency': 1}], 'request_count': 120, 'output_token_limit': 7}
        outcome = {}
        with ServiceProcess(root, runtime, tmp_path / 'crashed-service.log') as first:
            assert first.process is not None

            def invoke() -> None:
                try:
                    outcome['response'] = first.json(
                        'POST', '/api/v1/perf/invoke', payload, headers={'EvalScope-Task-Id': 'crash-run'}, timeout=120
                    )
                except BaseException as error:
                    outcome['error'] = repr(error)

            thread = threading.Thread(target=invoke, daemon=True)
            thread.start()
            try:
                _wait_for_requests(capture, 3)
                first.process.kill()
                first.process.wait(timeout=10)
                before = _capture_count(capture)
                time.sleep(5)
                stable = _capture_count(capture)
                time.sleep(2)
                assert _capture_count(capture) == stable
                assert stable - before <= 1
                thread.join(timeout=15)
                assert not thread.is_alive()
                assert 'error' in outcome and 'response' not in outcome
            finally:
                thread.join(timeout=15)
        assert first.process.poll() is not None

        # The replacement process must not resurrect the interrupted task or invent a completed run.
        with ServiceProcess(root, None, tmp_path / 'after-crash.log') as second:
            status, active = second.json('GET', '/api/v1/tasks/active')
            assert status == 200 and active['total'] == 0
            status, task = second.json('GET', '/api/v1/tasks/crash-run')
            assert status == 200 and task['status'] == 'unknown'
            assert task['report_url'] is None and task['html_report_url'] is None

            status, listing = second.json(
                'GET', f"/api/v1/perf/list?{urllib.parse.urlencode({'root_path': str(root)})}"
            )
            assert status == 200 and listing['total'] == 0
        assert _capture_count(capture) == stable
