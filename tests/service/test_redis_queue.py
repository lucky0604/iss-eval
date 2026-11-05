# Copyright (c) Alibaba, Inc. and its affiliates.
"""Tests for the Redis/RQ task queue backend.

The suite launches a throwaway ``redis-server`` on a free port and runs RQ's
``SimpleWorker`` in a thread, so it needs no external services but skips when
the binary or the service extra is missing.
"""

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict

import pytest

pytest.importorskip('flask')
redis = pytest.importorskip('redis')
rq = pytest.importorskip('rq')

from evalscope.service.api_models import ActiveTaskInfo  # noqa: E402
from evalscope.service.utils import log, queue_config, task_jobs  # noqa: E402
from evalscope.service.utils.log import read_task_manifest, read_task_result  # noqa: E402
from evalscope.service.utils.process import TaskStoppedError  # noqa: E402
from evalscope.service.utils.queue_config import QueueUnavailableError  # noqa: E402
from evalscope.service.utils.redis_queue import RedisTaskQueue  # noqa: E402
from evalscope.service.utils.task_jobs import JOB_ENTRYPOINTS, TaskJob, execute_job  # noqa: E402
from evalscope.service.utils.task_queue import get_task_queue, queue_health, reset_task_queue  # noqa: E402

REDIS_SERVER = shutil.which('redis-server')

pytestmark = pytest.mark.skipif(REDIS_SERVER is None, reason='redis-server is not installed')

TEST_OUTCOME: Dict[str, dict] = {}


def _redis_test_job(task_id: str) -> dict:
    """A job entrypoint that blocks until its test releases it."""

    def run() -> dict:
        state = TEST_OUTCOME[task_id]
        state['started'].set()
        state['release'].wait(10)
        if state.get('stopped'):
            raise TaskStoppedError(task_id)
        return {'status': 'completed', 'task_id': task_id, 'result': {'ok': True}}

    return task_jobs._execute(task_id, run)


def _invalid_payload_job(task_id: str) -> dict:
    return task_jobs._execute(task_id, lambda: {'status': 'queued', 'task_id': task_id})


def _failing_job(task_id: str) -> dict:
    def run() -> dict:
        raise RuntimeError('boom')

    return task_jobs._execute(task_id, run)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError('condition not reached before timeout')


def _meta(task_id: str) -> ActiveTaskInfo:
    return ActiveTaskInfo(task_id=task_id, kind='eval', model='demo')


@pytest.fixture(scope='module')
def redis_url(tmp_path_factory) -> str:
    port = _free_port()
    directory = tmp_path_factory.mktemp('redis')
    process = subprocess.Popen(
        [
            REDIS_SERVER,
            '--port', str(port),
            '--bind', '127.0.0.1',
            '--save', '',
            '--appendonly', 'no',
            '--dir', str(directory),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )  # fmt: skip
    url = f'redis://127.0.0.1:{port}/0'
    try:
        connection = redis.Redis.from_url(url)
        _wait_until(lambda: _ping(connection), timeout=10)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


def _ping(connection) -> bool:
    try:
        return bool(connection.ping())
    except redis.RedisError:
        return False


@pytest.fixture
def queue_env(redis_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    name = f'evalscope-test-{time.time_ns()}'
    monkeypatch.setenv('EVALSCOPE_QUEUE_BACKEND', queue_config.BACKEND_REDIS)
    monkeypatch.setenv('EVALSCOPE_REDIS_URL', redis_url)
    monkeypatch.setenv('EVALSCOPE_REDIS_QUEUE', name)
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setitem(JOB_ENTRYPOINTS, 'test_job', _redis_test_job)
    monkeypatch.setitem(JOB_ENTRYPOINTS, 'invalid_payload', _invalid_payload_job)
    monkeypatch.setitem(JOB_ENTRYPOINTS, 'failing_job', _failing_job)
    reset_task_queue()
    yield name
    reset_task_queue()


@pytest.fixture
def events() -> dict:
    return TEST_OUTCOME


def _install_events(task_id: str, events: dict) -> dict:
    state = {'started': threading.Event(), 'release': threading.Event()}
    events[task_id] = state
    return state


@pytest.fixture
def start_worker(queue_env: str, redis_url: str, monkeypatch: pytest.MonkeyPatch):
    """Start RQ SimpleWorkers in threads; workers in threads cannot install signal handlers.

    Workers start after the test enqueued its jobs and exit once the queue drains.
    """
    from rq import Queue, SimpleWorker
    from rq.timeouts import TimerDeathPenalty
    from rq.worker import base

    monkeypatch.setattr(base.BaseWorker, '_install_signal_handlers', lambda self: None)
    monkeypatch.setattr(SimpleWorker, 'death_penalty_class', TimerDeathPenalty)

    connection = redis.Redis.from_url(redis_url)
    threads = []

    def start():
        worker = SimpleWorker([Queue(queue_env, connection=connection)], connection=connection)
        thread = threading.Thread(target=worker.work, kwargs={'burst': True, 'logging_level': 'CRITICAL'}, daemon=True)
        thread.start()
        threads.append(thread)
        return thread

    yield start
    for thread in threads:
        thread.join(timeout=15)
    connection.close()


def _queue() -> RedisTaskQueue:
    backend = get_task_queue()
    assert isinstance(backend, RedisTaskQueue)
    return backend


def _status(task_id: str):
    snapshot = _queue().get(task_id)
    return snapshot['status'] if snapshot is not None else None


def test_submit_get_list_and_cancel_queued(queue_env: str, events: dict) -> None:
    queue = _queue()
    _install_events('eval_queued', events)

    snapshot = queue.submit('eval_queued', 'eval', _meta('eval_queued'), TaskJob('test_job', ('eval_queued',)))
    assert snapshot['status'] == 'queued'
    assert snapshot['queue_position'] == 1

    running = queue.get('eval_queued')
    assert running is not None
    assert running['status'] == 'queued'
    assert running['queue_position'] == 1
    assert [item['task_id'] for item in queue.list()] == ['eval_queued']

    with pytest.raises(ValueError):
        queue.submit('eval_queued', 'eval', _meta('eval_queued'), TaskJob('test_job', ('eval_queued',)))

    assert queue.cancel('eval_queued') == 'stopped'
    assert queue.get('eval_queued') is None
    assert read_task_result('eval_queued') == {'status': 'stopped', 'task_id': 'eval_queued'}
    assert read_task_manifest(str(Path(log.OUTPUT_DIR) / 'eval_queued'))['status'] == 'stopped'

    # The canceled job is terminal, so the same task id can be submitted again.
    again = queue.submit('eval_queued', 'eval', _meta('eval_queued'), TaskJob('test_job', ('eval_queued',)))
    assert again['status'] == 'queued'
    assert queue.cancel('eval_queued') == 'stopped'


def test_submit_requires_a_task_job(queue_env: str) -> None:
    with pytest.raises(TypeError):
        _queue().submit('eval_runner', 'eval', _meta('eval_runner'), lambda: {'status': 'completed'})


def test_unknown_entrypoint_is_rejected() -> None:
    with pytest.raises(ValueError):
        TaskJob('does_not_exist', ())


def test_worker_executes_job_and_persists_result(queue_env: str, events: dict, start_worker) -> None:
    state = _install_events('eval_worker', events)
    state['release'].set()

    snapshot = _queue().submit('eval_worker', 'eval', _meta('eval_worker'), TaskJob('test_job', ('eval_worker',)))
    assert snapshot['queue_position'] == 1

    thread = start_worker()
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert read_task_result('eval_worker')['status'] == 'completed'
    assert _queue().get('eval_worker') is None


def test_cancel_running_job_reaches_worker(
    queue_env: str, events: dict, start_worker, monkeypatch: pytest.MonkeyPatch, redis_url: str
) -> None:
    state = _install_events('eval_running', events)

    def fake_stop(task_id: str) -> bool:
        state['stopped'] = True
        state['release'].set()
        return True

    monkeypatch.setattr(task_jobs, 'stop_process', fake_stop)

    _queue().submit('eval_running', 'eval', _meta('eval_running'), TaskJob('test_job', ('eval_running',)))
    thread = start_worker()
    assert state['started'].wait(10)
    _wait_until(lambda: _status('eval_running') == 'running')

    assert _queue().cancel('eval_running') == 'cancelling'
    _wait_until(lambda: (read_task_result('eval_running') or {}).get('status') == 'stopped')
    assert state['stopped'] is True

    thread.join(timeout=15)
    assert not thread.is_alive()
    assert _queue().get('eval_running') is None
    connection = redis.Redis.from_url(redis_url)
    try:
        assert connection.get(queue_config.cancellation_key('eval_running')) is None
    finally:
        connection.close()


def test_failing_job_and_invalid_payload_are_terminal_errors(queue_env: str, events: dict, start_worker) -> None:
    _install_events('eval_failing', events)['release'].set()
    _install_events('eval_invalid', events)['release'].set()

    _queue().submit('eval_failing', 'eval', _meta('eval_failing'), TaskJob('failing_job', ('eval_failing',)))
    _queue().submit('eval_invalid', 'eval', _meta('eval_invalid'), TaskJob('invalid_payload', ('eval_invalid',)))

    thread = start_worker()
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert read_task_result('eval_failing')['status'] == 'error'
    assert read_task_result('eval_invalid')['status'] == 'error'


def test_execute_job_normalizes_non_terminal_payload(queue_env: str, events: dict) -> None:
    _install_events('eval_direct', events)['release'].set()
    payload = execute_job(TaskJob('invalid_payload', ('eval_direct',)))
    assert payload['status'] == 'error'
    assert read_task_result('eval_direct')['status'] == 'error'


def test_queue_health_reports_backend(queue_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert queue_health() == {'backend': 'redis'}

    monkeypatch.setenv('EVALSCOPE_REDIS_URL', f'redis://127.0.0.1:{_free_port()}/0')
    reset_task_queue()
    with pytest.raises(Exception):
        queue_health()


def test_submit_reports_unavailable_redis(queue_env: str) -> None:
    backend = RedisTaskQueue(url=f'redis://127.0.0.1:{_free_port()}/0', queue_name=queue_env)
    try:
        with pytest.raises(QueueUnavailableError):
            backend.submit('eval_down', 'eval', _meta('eval_down'), TaskJob('test_job', ('eval_down',)))
    finally:
        backend.shutdown()


def test_api_async_invoke_returns_503_when_queue_is_down(
    queue_env: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from evalscope.service.app import create_app
    from evalscope.service.blueprints import eval as eval_routes

    monkeypatch.setattr(eval_routes, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setenv('EVALSCOPE_REDIS_URL', f'redis://127.0.0.1:{_free_port()}/0')
    reset_task_queue()

    client = create_app(outputs=str(tmp_path)).test_client()
    response = client.post(
        '/api/v1/eval/invoke',
        query_string={'async': 1},
        json={'model': 'demo', 'datasets': ['general_qa'], 'api_url': 'http://localhost:18101/v1'},
        headers={'EvalScope-Task-Id': 'eval_down_api'},
    )
    assert response.status_code == 503
    assert response.get_json()['error'] == 'Task queue is unavailable'


def test_readiness_endpoint_reflects_redis(queue_env: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from evalscope.service.app import create_app

    client = create_app(outputs=str(tmp_path)).test_client()
    ready = client.get('/ready')
    assert ready.status_code == 200
    assert ready.get_json()['queue'] == {'backend': 'redis', 'reachable': True}

    monkeypatch.setenv('EVALSCOPE_REDIS_URL', f'redis://127.0.0.1:{_free_port()}/0')
    reset_task_queue()
    unavailable = client.get('/ready')
    assert unavailable.status_code == 503
    assert unavailable.get_json()['queue']['reachable'] is False


def test_api_async_invoke_runs_on_worker(
    queue_env: str, redis_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, events: dict
) -> None:
    from evalscope.service.app import create_app
    from evalscope.service.blueprints import eval as eval_routes
    from evalscope.service.blueprints import perf, tasks

    monkeypatch.setattr(eval_routes, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(tasks, 'OUTPUT_DIR', str(tmp_path))

    started = threading.Event()
    release = threading.Event()

    def execute(func, config, **kwargs):
        started.set()
        release.wait(10)
        return {'general_qa': {'score': 1}}

    monkeypatch.setattr(eval_routes, 'run_in_subprocess', execute)
    monkeypatch.setattr(eval_routes, '_build_result_table', lambda path: '| ok |')

    from rq import Queue, SimpleWorker
    from rq.timeouts import TimerDeathPenalty
    from rq.worker import base

    monkeypatch.setattr(base.BaseWorker, '_install_signal_handlers', lambda self: None)
    monkeypatch.setattr(SimpleWorker, 'death_penalty_class', TimerDeathPenalty)

    client = create_app(outputs=str(tmp_path)).test_client()
    response = client.post(
        '/api/v1/eval/invoke',
        query_string={'async': 1},
        json={'model': 'demo', 'datasets': ['general_qa'], 'api_url': 'http://localhost:18101/v1'},
        headers={'EvalScope-Task-Id': 'eval_api'},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body['status'] == 'queued'
    assert body['task_id'] == 'eval_api'
    assert body['queue_position'] == 1

    connection = redis.Redis.from_url(redis_url)
    worker = SimpleWorker([Queue(queue_env, connection=connection)], connection=connection)
    thread = threading.Thread(target=worker.work, kwargs={'burst': True, 'logging_level': 'CRITICAL'}, daemon=True)
    thread.start()
    try:
        assert started.wait(10)
        assert client.get('/api/v1/tasks/eval_api').get_json()['status'] == 'running'
        assert client.get('/api/v1/tasks/eval_api/result').status_code == 202

        release.set()
        thread.join(timeout=15)
        assert not thread.is_alive()
    finally:
        if thread.is_alive():
            release.set()
        connection.close()

    finished = client.get('/api/v1/tasks/eval_api/result')
    assert finished.status_code == 200
    payload = finished.get_json()
    assert payload['status'] == 'completed'
    assert payload['table'] == '| ok |'
    assert payload['result'] == {'general_qa': {'score': 1}}
