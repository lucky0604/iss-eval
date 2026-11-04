# Copyright (c) Alibaba, Inc. and its affiliates.
"""Tests for the Phase 1 in-process task queue and the asynchronous invoke contract.

Skipped automatically when Flask (service extra) is not installed.
"""
import json
import os
import threading
import time
from pathlib import Path

import pytest

flask = pytest.importorskip('flask')
from evalscope.service.api_models import ActiveTaskInfo  # noqa: E402
from evalscope.service.utils import log  # noqa: E402
from evalscope.service.utils.log import read_task_manifest, read_task_result  # noqa: E402
from evalscope.service.utils.task_queue import TaskQueue, get_task_queue, reset_task_queue  # noqa: E402


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError('condition not reached before timeout')


def _meta(task_id: str) -> ActiveTaskInfo:
    return ActiveTaskInfo(task_id=task_id, kind='eval', model='demo')


@pytest.fixture(autouse=True)
def clean_queue():
    reset_task_queue()
    yield
    reset_task_queue()


@pytest.fixture
def outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    return tmp_path


def test_fifo_position_and_persisted_result(outputs: Path) -> None:
    queue = TaskQueue(max_workers=1)
    release = threading.Event()
    started = threading.Event()
    second_ran = threading.Event()

    def blocking() -> dict:
        started.set()
        release.wait(5)
        return {'status': 'completed', 'task_id': 'eval_1', 'result': {'score': 1}}

    first = queue.submit('eval_1', 'eval', _meta('eval_1'), blocking)
    second = queue.submit(
        'eval_2', 'eval', _meta('eval_2'),
        lambda: second_ran.set() or {'status': 'completed', 'task_id': 'eval_2'},
    )
    assert first['queue_position'] == 1
    # eval_1 is already running, so eval_2 waits at the front of the queue.
    assert second['status'] == 'queued' and second['queue_position'] == 1
    assert started.wait(5)

    waiting = queue.get('eval_2')
    assert waiting['status'] == 'queued'
    assert waiting['queue_position'] == 1

    release.set()
    _wait_until(lambda: queue.get('eval_1') is None and queue.get('eval_2') is None)
    assert second_ran.is_set()
    assert read_task_result('eval_1')['status'] == 'completed'
    assert read_task_manifest(str(outputs / 'eval_1'))['status'] == 'completed'
    assert read_task_manifest(str(outputs / 'eval_2'))['status'] == 'completed'


def test_cancel_queued_task_never_runs(outputs: Path) -> None:
    queue = TaskQueue(max_workers=1)
    release = threading.Event()
    started = threading.Event()
    second_ran = threading.Event()

    queue.submit(
        'eval_1', 'eval', _meta('eval_1'),
        lambda: (started.set(), release.wait(5), {'status': 'completed', 'task_id': 'eval_1'})[-1],
    )
    queue.submit(
        'eval_2', 'eval', _meta('eval_2'),
        lambda: second_ran.set() or {'status': 'completed', 'task_id': 'eval_2'},
    )
    assert started.wait(5)

    assert queue.cancel('eval_2') == 'stopped'
    assert queue.get('eval_2') is None
    assert read_task_result('eval_2') == {'status': 'stopped', 'task_id': 'eval_2'}
    assert read_task_manifest(str(outputs / 'eval_2'))['status'] == 'stopped'

    release.set()
    _wait_until(lambda: queue.get('eval_1') is None)
    assert not second_ran.is_set()


def test_cancel_reports_unknown_and_running() -> None:
    queue = TaskQueue(max_workers=1)
    release = threading.Event()
    started = threading.Event()

    queue.submit(
        'eval_1', 'eval', _meta('eval_1'),
        lambda: (started.set(), release.wait(5), {'status': 'completed', 'task_id': 'eval_1'})[-1],
    )
    assert started.wait(5)
    assert queue.cancel('eval_1') == 'running'
    assert queue.cancel('missing') == 'unknown'
    release.set()
    _wait_until(lambda: queue.get('eval_1') is None)


def test_duplicate_task_id_is_rejected(outputs: Path) -> None:
    queue = TaskQueue(max_workers=1)
    release = threading.Event()
    started = threading.Event()
    queue.submit(
        'eval_1', 'eval', _meta('eval_1'),
        lambda: (started.set(), release.wait(5), {'status': 'completed', 'task_id': 'eval_1'})[-1],
    )
    assert started.wait(5)
    with pytest.raises(ValueError):
        queue.submit('eval_1', 'eval', _meta('eval_1'), lambda: {})
    release.set()
    _wait_until(lambda: queue.get('eval_1') is None)


def test_failed_runner_is_recorded_as_error(outputs: Path) -> None:
    queue = TaskQueue(max_workers=1)

    def failing() -> dict:
        raise RuntimeError('boom')

    queue.submit('eval_1', 'eval', _meta('eval_1'), failing)
    _wait_until(lambda: queue.get('eval_1') is None)
    assert read_task_result('eval_1')['status'] == 'error'
    assert read_task_manifest(str(outputs / 'eval_1'))['status'] == 'error'


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from evalscope.service.app import create_app
    from evalscope.service.blueprints import eval as eval_routes
    from evalscope.service.blueprints import perf, tasks

    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(eval_routes, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(tasks, 'OUTPUT_DIR', str(tmp_path))
    return create_app(outputs=str(tmp_path)).test_client()


EVAL_PAYLOAD = {'model': 'demo', 'datasets': ['general_qa'], 'api_url': 'http://localhost:18101/v1'}


def _stub_eval(monkeypatch: pytest.MonkeyPatch, release: threading.Event, started: threading.Event) -> None:
    def execute(func, config, **kwargs):
        started.set()
        release.wait(5)
        return {'general_qa': {'score': 1}}

    monkeypatch.setattr('evalscope.service.blueprints.eval.run_in_subprocess', execute)
    monkeypatch.setattr('evalscope.service.blueprints.eval._build_result_table', lambda path: '| ok |')


def test_async_invoke_returns_immediately_and_result_is_recoverable(client, monkeypatch) -> None:
    release = threading.Event()
    started = threading.Event()
    _stub_eval(monkeypatch, release, started)

    blocker = client.post(
        '/api/v1/eval/invoke', query_string={'async': 1}, json=EVAL_PAYLOAD, headers={'EvalScope-Task-Id': 'eval_blocker'}
    )
    assert blocker.status_code == 202
    assert started.wait(5)

    response = client.post(
        '/api/v1/eval/invoke', query_string={'async': 1}, json=EVAL_PAYLOAD, headers={'EvalScope-Task-Id': 'eval_async'}
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body == {
        'status': 'queued',
        'task_id': 'eval_async',
        'enqueued_at': body['enqueued_at'],
        'queue_position': 1,
    }

    active = client.get('/api/v1/tasks/active').get_json()
    tasks = {task['task_id']: task for task in active['tasks']}
    assert set(tasks) == {'eval_blocker', 'eval_async'}
    assert tasks['eval_blocker']['status'] == 'running'
    assert tasks['eval_async']['status'] == 'queued'
    assert tasks['eval_async']['percent'] == 0.0

    detail = client.get('/api/v1/tasks/eval_async').get_json()
    assert detail['status'] == 'queued'
    assert detail['queue_position'] == 1

    pending = client.get('/api/v1/tasks/eval_async/result')
    assert pending.status_code == 202
    assert pending.get_json()['status'] == 'queued'

    release.set()
    _wait_until(lambda: get_task_queue().get('eval_async') is None and get_task_queue().get('eval_blocker') is None)

    finished = client.get('/api/v1/tasks/eval_async/result')
    assert finished.status_code == 200
    payload = finished.get_json()
    assert payload['status'] == 'completed'
    assert payload['table'] == '| ok |'
    assert payload['result'] == {'general_qa': {'score': 1}}


def test_stop_queued_task_marks_it_stopped_without_running(client, monkeypatch) -> None:
    release = threading.Event()
    started = threading.Event()
    _stub_eval(monkeypatch, release, started)

    first = client.post(
        '/api/v1/eval/invoke', query_string={'async': 1}, json=EVAL_PAYLOAD, headers={'EvalScope-Task-Id': 'eval_first'}
    )
    assert first.status_code == 202
    assert started.wait(5)

    second = client.post(
        '/api/v1/eval/invoke', query_string={'async': 1}, json=EVAL_PAYLOAD, headers={'EvalScope-Task-Id': 'eval_second'}
    )
    assert second.status_code == 202
    assert second.get_json()['queue_position'] == 1

    stopped = client.post('/api/v1/eval/stop', query_string={'task_id': 'eval_second'})
    assert stopped.status_code == 200
    assert stopped.get_json() == {'status': 'stopped', 'task_id': 'eval_second'}

    result = client.get('/api/v1/tasks/eval_second/result')
    assert result.status_code == 200
    assert result.get_json() == {'status': 'stopped', 'task_id': 'eval_second'}

    release.set()
    _wait_until(lambda: get_task_queue().get('eval_first') is None)


def test_blocking_invoke_stays_synchronous(client, monkeypatch) -> None:
    release = threading.Event()
    started = threading.Event()
    _stub_eval(monkeypatch, release, started)
    release.set()

    response = client.post('/api/v1/eval/invoke', json=EVAL_PAYLOAD, headers={'EvalScope-Task-Id': 'eval_blocking'})
    assert response.status_code == 200
    assert response.get_json()['status'] == 'completed'
    assert read_task_result('eval_blocking')['status'] == 'completed'


def test_result_endpoint_rejects_invalid_and_missing_ids(client) -> None:
    assert client.get('/api/v1/tasks/eval%5C1/result').status_code == 400
    assert client.get('/api/v1/tasks/eval_absent/result').status_code == 404
