"""Validate configuration mapping and the pinned runtime's local HTTP flow."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from evalscope.perf.executors.arguments import (
    AIPerfArguments,
    LoadConfiguration,
    PerfTaskArguments,
    TelemetryConfiguration,
)
from evalscope.perf.executors.dispatch import run_perf_task
from evalscope.perf.executors.models import ExecutionContext, ExternalRuntime
from evalscope.perf.integrations.aiperf.single_run import prepare_single_run, run_single
from evalscope.perf.results.aiperf_import import import_aiperf_run, query_aiperf_requests, query_node_metrics
from evalscope.perf.results.models import AIPerfImportArguments


def _arguments(tokenizer: str, url: str = 'http://localhost:8000/v1') -> AIPerfArguments:
    return AIPerfArguments(
        model='mock-model', url=url, tokenizer_path=tokenizer, api_key='fixture-key',
        load={'points': [{'concurrency': 1}], 'request_count': 2, 'output_token_limit': 2},
    )


def _dataset(root: Path) -> Path:
    path = root / 'input.jsonl'
    path.write_text('\n'.join(json.dumps({'text': f'token{i} token{i + 1}', 'output_length': 2})
                              for i in range(2)) + '\n')
    return path


def test_prepared_config_preserves_inputs_without_secret(tmp_path: Path) -> None:
    context = ExecutionContext(task_id='task', output_dir=str(tmp_path), runtime=ExternalRuntime(executable='/aiperf'))
    args = _arguments('/tokenizer', 'http://localhost:8000/proxy/v1/chat/completions')
    prepared = prepare_single_run(args, context, str(_dataset(tmp_path)))
    config_path = Path(prepared.config_file)
    try:
        text = config_path.read_text()
        config = json.loads(text)['benchmark']
        assert 'fixture-key' not in text
        assert config['endpoint']['path'] == '/proxy/v1/chat/completions'
        assert config['endpoint']['api_key'] == '${EVALSCOPE_AIPERF_API_KEY}'
        assert config['phases'][0]['concurrency'] == 1
        assert config['phases'][0]['requests'] == 2
        assert config['server_metrics']['enabled'] is False and config['server_metrics']['urls'] == []
        assert config_path.stat().st_mode & 0o077 == 0
        assert Path(prepared.artifact_dir).stat().st_mode & 0o077 == 0
        assert prepared.invocation.log_file == str(tmp_path / 'benchmark.log')
        assert prepared.invocation.environment['EVALSCOPE_AIPERF_API_KEY'].get_secret_value() == 'fixture-key'
        with pytest.raises(ValueError, match='already exists'):
            prepare_single_run(args, context, str(tmp_path / 'input.jsonl'))
    finally:
        config_path.unlink()


def test_prepared_model_and_tokenizer_are_data_not_shell_commands(tmp_path: Path) -> None:
    context = ExecutionContext(task_id='task', output_dir=str(tmp_path), runtime=ExternalRuntime(executable='/aiperf'))
    marker = tmp_path / 'executed'
    model = f'model "quoted" `touch {marker}` $(touch {marker})'
    tokenizer = '/tokenizer with spaces/$(ignored)'
    arguments = _arguments(tokenizer).model_copy(update={'model': model})
    prepared = prepare_single_run(arguments, context, str(_dataset(tmp_path)))
    try:
        config = json.loads(Path(prepared.config_file).read_text())['benchmark']
        assert config['models']['items'][0]['name'] == model
        assert config['tokenizer']['name'] == tokenizer
        assert not marker.exists()
    finally:
        Path(prepared.config_file).unlink()


@pytest.mark.parametrize('url', [
    'http://localhost/v1/chat/completions?secret=value', 'http://localhost/custom', 'http://localhost/v1#fragment',
])
def test_unsupported_endpoint_is_rejected(tmp_path: Path, url: str) -> None:
    context = ExecutionContext(task_id='task', output_dir=str(tmp_path), runtime=ExternalRuntime(executable='/aiperf'))
    with pytest.raises(ValueError):
        prepare_single_run(_arguments('/tokenizer', url), context, str(_dataset(tmp_path)))


def test_rejects_experiment_and_input_outside_task(tmp_path: Path) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    context = ExecutionContext(task_id='task', output_dir=str(root), runtime=ExternalRuntime(executable='/aiperf'))
    arguments = _arguments('/tokenizer')
    with pytest.raises(ValueError, match='inside the task'):
        prepare_single_run(arguments, context, str(_dataset(tmp_path)))
    arguments.cache_experiment = {'target_ratios': [0.8], 'total_input_tokens': 100}
    with pytest.raises(ValueError, match='orchestration'):
        prepare_single_run(arguments, context, str(_dataset(root)))


@pytest.mark.skipif(not os.environ.get('EVALSCOPE_TEST_AIPERF'), reason='requires isolated AIPerf 0.13.0')
@pytest.mark.parametrize('mode', ['closed_loop', 'open_loop'])
def test_pinned_aiperf_sends_real_local_requests(tmp_path: Path, mode: str) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    tokenizer = Tokenizer(models.WordLevel({'[UNK]': 0, '[PAD]': 1, **{f'token{i}': i + 2 for i in range(100)}},
                                           unk_token='[UNK]'))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer_path = tmp_path / 'tokenizer'
    PreTrainedTokenizerFast(tokenizer_object=tokenizer, unk_token='[UNK]', pad_token='[PAD]').save_pretrained(
        str(tokenizer_path)
    )
    fixture = Path(__file__).parent / 'fixtures' / 'aiperf' / 'mock_inference_server.py'
    capture = tmp_path / 'capture.jsonl'
    metrics_capture = tmp_path / 'metrics_capture.jsonl'
    server = subprocess.Popen([sys.executable, str(fixture), '--capture', str(capture), '--metrics-capture',
                               str(metrics_capture), '--mode', 'cache_usage' if mode == 'open_loop' else 'success'],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert server.stdout is not None
        port = int(server.stdout.readline())
        context = ExecutionContext(task_id='local-test', output_dir=str(tmp_path), runtime=ExternalRuntime(
            executable=os.environ['EVALSCOPE_TEST_AIPERF'], graceful_timeout=3, terminate_timeout=2, kill_timeout=3,
        ))
        args = _arguments(str(tokenizer_path), f'http://127.0.0.1:{port}/v1')
        load = LoadConfiguration(mode=mode, points=[{'concurrency': 1} if mode == 'closed_loop' else {'rate': 50.0}],
                                 request_count=2, output_token_limit=2)
        telemetry = TelemetryConfiguration(sample_interval_seconds=0.05, targets=[{
            'node_id': 'local', 'role': 'prefill', 'engine': 'vllm', 'url': f'http://127.0.0.1:{port}/metrics',
        }])
        args = args.model_copy(update={'load': load, 'telemetry': telemetry})
        outcome = run_perf_task(PerfTaskArguments(engine='aiperf', aiperf=args), context, str(_dataset(tmp_path)))
        assert outcome.exit_reason == 'completed' and outcome.cleanup_confirmed, outcome
        assert outcome.executor_version == '0.13.0'
        assert outcome.artifact_status == 'available'
        requests = [json.loads(line) for line in capture.read_text().splitlines()]
        assert len(requests) == 2
        assert all(request['fixture_authorization_matches'] for request in requests)
        assert all(request['path'] == '/v1/chat/completions' for request in requests)
        assert all(request['body']['model'] == 'mock-model' and request['body']['stream'] for request in requests)
        assert all(request['body']['max_completion_tokens'] == 2 for request in requests)
        artifact_dir = Path(outcome.raw_artifact_dir)
        summary = json.loads((artifact_dir / 'profile_export_aiperf.json').read_text())
        phase = summary['input_config']['phases'][0]
        assert phase['type'] == ('concurrency' if mode == 'closed_loop' else 'poisson')
        assert phase.get('concurrency' if mode == 'closed_loop' else 'rate') == (1 if mode == 'closed_loop' else 50)
        assert (artifact_dir / 'server_metrics_export.jsonl').is_file()
        metrics_requests = [json.loads(line) for line in metrics_capture.read_text().splitlines()]
        assert metrics_requests and all(not request['authorization_present'] for request in metrics_requests)
        assert not any(b'fixture-key' in path.read_bytes() for path in artifact_dir.rglob('*') if path.is_file())
        assert not list(tmp_path.glob('.aiperf-config-*.json'))
        normalized = tmp_path / 'normalized'
        result = import_aiperf_run(AIPerfImportArguments(
            raw_dir=str(artifact_dir), normalized_dir=str(normalized), targets=[{
                'node_id': 'local', 'role': 'prefill', 'engine': 'vllm', 'url': f'http://127.0.0.1:{port}/metrics',
            }],
        ))
        total, page = query_aiperf_requests(str(normalized), phase='profiling')
        assert result.profiling_requests == total == 2
        assert all(record.cached_tokens == (6 if mode == 'open_loop' else None) for record in page)
        assert all(record.prompt_tokens_source == 'server_usage' for record in page)
        node_count, nodes = query_node_metrics(str(normalized), node_id='local', metric_name='mock_requests')
        assert node_count and all(node.semantics == 'unverified' for node in nodes)
    finally:
        server.terminate()
        server.wait(timeout=5)
        if server.stdout:
            server.stdout.close()


@pytest.mark.skipif(not os.environ.get('EVALSCOPE_TEST_AIPERF'), reason='requires isolated AIPerf 0.13.0')
def test_pinned_aiperf_can_be_cancelled_during_request(tmp_path: Path) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    backend = Tokenizer(models.WordLevel({'[UNK]': 0, **{f'token{i}': i + 1 for i in range(100)}}, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer_path = tmp_path / 'tokenizer'
    PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]').save_pretrained(str(tokenizer_path))
    fixture = Path(__file__).parent / 'fixtures' / 'aiperf' / 'mock_inference_server.py'
    capture = tmp_path / 'capture.jsonl'
    server = subprocess.Popen([sys.executable, str(fixture), '--capture', str(capture), '--mode', 'timeout'],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert server.stdout is not None
        port = int(server.stdout.readline())
        context = ExecutionContext(task_id='cancel-test', output_dir=str(tmp_path), runtime=ExternalRuntime(
            executable=os.environ['EVALSCOPE_TEST_AIPERF'], graceful_timeout=5, terminate_timeout=2, kill_timeout=3,
        ))
        cancel = threading.Event()

        def cancel_when_sent() -> None:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not capture.exists():
                time.sleep(0.02)
            cancel.set()

        thread = threading.Thread(target=cancel_when_sent)
        thread.start()
        outcome = run_single(_arguments(str(tokenizer_path), f'http://127.0.0.1:{port}/v1'), context,
                             str(_dataset(tmp_path)), cancel_event=cancel)
        thread.join(timeout=5)
        assert capture.exists(), 'AIPerf never reached the local endpoint'
        assert outcome.exit_reason == 'cancelled' and outcome.cleanup_confirmed, outcome
    finally:
        server.terminate()
        server.wait(timeout=5)
        if server.stdout:
            server.stdout.close()
