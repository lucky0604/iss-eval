"""Validation and compatibility tests for executor configuration."""

from copy import deepcopy
from typing import Callable

import pytest
from pydantic import ValidationError

from evalscope.perf.executors.arguments import parse_perf_task_arguments
from evalscope.perf.executors.dispatch import run_perf_task
from evalscope.perf.executors.models import ExecutionContext


@pytest.fixture
def payload() -> dict:
    return {
        'engine': 'aiperf',
        'aiperf': {
            'model': 'model',
            'url': 'http://localhost:8000/v1/chat/completions',
            'tokenizer_path': '/tokenizer',
            'api_key': 'model-secret',
            'cache_experiment': {'target_ratios': [0, 0.8, 0.9], 'total_input_tokens': 100},
            'load': {'points': [{'concurrency': 2}], 'request_count': 10, 'output_token_limit': 8},
            'deployment_mode': 'pd',
            'telemetry': {
                'required': True,
                'targets': [
                    {'node_id': 'p', 'role': 'prefill', 'engine': 'vllm', 'url': 'http://p:8000/metrics',
                     'headers': {'Authorization': 'metric-secret'}},
                    {'node_id': 'd', 'role': 'decode', 'engine': 'sglang', 'url': 'http://d:8000/metrics'},
                ],
            },
        },
    }


def test_legacy_and_envelope_native_use_existing_arguments() -> None:
    native = {'model': 'model', 'url': 'http://localhost/v1', 'number': 3, 'parallel': 1}
    legacy = parse_perf_task_arguments(native)
    envelope = parse_perf_task_arguments({'engine': 'evalscope', 'native': native})
    assert legacy.native == envelope.native
    assert legacy.native is not None
    assert legacy.native.url == 'http://localhost/v1/chat/completions'


def test_aiperf_fields_and_secret_projection(payload: dict) -> None:
    args = parse_perf_task_arguments(payload).aiperf
    assert args is not None and args.cache_experiment is not None
    assert args.cache_experiment.target_ratios == [0, 0.8, 0.9]
    assert args.load.points[0].concurrency == 2
    assert args.api_key is not None and args.api_key.get_secret_value() == 'model-secret'
    assert args.telemetry.targets[0].headers['Authorization'].get_secret_value() == 'metric-secret'
    assert 'model-secret' not in str(args) and 'metric-secret' not in str(args)


def test_dispatch_preserves_native_result_and_routes_external_executor(payload: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    context = ExecutionContext(task_id='test', output_dir='/tmp/task')
    native = parse_perf_task_arguments({'model': 'model', 'url': 'http://localhost/v1'})
    expected = object()
    received = []

    def execute(arguments: object) -> object:
        received.append(arguments)
        return expected

    monkeypatch.setattr('evalscope.perf.executors.dispatch.run_perf_benchmark', execute)
    assert run_perf_task(native, context) is expected
    assert received == [native.native]
    with pytest.raises(ValueError, match='server-owned input'):
        run_perf_task(parse_perf_task_arguments(payload), context)
    external = parse_perf_task_arguments(payload)
    calls = []
    monkeypatch.setattr('evalscope.perf.integrations.aiperf.single_run.run_single',
                        lambda arguments, execution, input_file: calls.append((arguments, execution, input_file)))
    run_perf_task(external, context, '/tmp/task/input.jsonl')
    assert calls == [(external.aiperf, context, '/tmp/task/input.jsonl')]


@pytest.mark.parametrize('ratio', [True, -0.1, 1.1, float('nan'), float('inf'), '0.8'])
def test_bad_ratios_fail(payload: dict, ratio: object) -> None:
    payload['aiperf']['cache_experiment']['target_ratios'] = [ratio]
    with pytest.raises(ValueError):
        parse_perf_task_arguments(payload)


@pytest.mark.parametrize('change', [
    lambda p: p.update(native={'model': 'other'}),
    lambda p: p.update(model='ambiguous'),
    lambda p: p['aiperf']['load'].update(request_count=True),
    lambda p: p['aiperf']['load']['points'][0].update(rate=1),
    lambda p: p['aiperf']['telemetry']['targets'].pop(),
    lambda p: p['aiperf'].update(model='  '),
    lambda p: p['aiperf'].update(tokenizer_path='\t'),
    lambda p: p['aiperf']['telemetry']['targets'][0].update(node_id=' '),
    lambda p: p['aiperf'].update(url='http://name:password@localhost/'),
    lambda p: p['aiperf'].update(unsupported=True),
])
def test_invalid_envelopes_are_rejected(payload: dict, change: Callable[[dict], None]) -> None:
    changed = deepcopy(payload)
    change(changed)
    with pytest.raises((ValueError, ValidationError)):
        parse_perf_task_arguments(changed)


def test_input_lengths_and_generic_warmup_are_typed(payload: dict) -> None:
    config = payload['aiperf']
    config['cache_experiment'].update(
        {
            'input_token_values': [100, 8000],
            'warmup': {
                'request_count': 3,
                'concurrency': 1,
                'total_input_tokens': 16000,
                'output_token_limit': 1000,
            },
        }
    )
    args = parse_perf_task_arguments(payload).aiperf
    assert args is not None and args.cache_experiment is not None
    assert args.cache_experiment.input_lengths == [100, 8000]
    assert args.cache_experiment.warmup is not None
    assert args.cache_experiment.warmup.request_count == 3


@pytest.mark.parametrize('change', [
    lambda c: c.update(input_token_values=[8000, 100]),
    lambda c: c.update(input_token_values=[100, 100]),
    lambda c: c.update(input_token_values=[100, 0]),
    lambda c: c.update(input_token_values=[200]),
    lambda c: c.update(mode='synthetic_isl', target_ratios=[0.8]),
    lambda c: c.update(warmup={'request_count': True, 'concurrency': 1, 'total_input_tokens': 1, 'output_token_limit': 1}),
])
def test_invalid_input_lengths_and_baseline_are_rejected(payload: dict, change: Callable[[dict], None]) -> None:
    config = payload['aiperf']['cache_experiment']
    change(config)
    with pytest.raises((ValueError, ValidationError)):
        parse_perf_task_arguments(payload)


def test_synthetic_baseline_uses_zero_ratios(payload: dict) -> None:
    config = payload['aiperf']['cache_experiment']
    config.update(mode='synthetic_isl', target_ratios=[0])
    args = parse_perf_task_arguments(payload).aiperf
    assert args is not None and args.cache_experiment is not None
    assert args.cache_experiment.mode == 'synthetic_isl'
    assert args.cache_experiment.input_lengths == [100]


def test_single_run_inline_prompts_must_match_the_request_budget(payload: dict) -> None:
    config = payload['aiperf']
    config['cache_experiment'] = None
    config['load']['request_count'] = 2
    config['input_prompts'] = ['first', 'second']
    assert parse_perf_task_arguments(payload).aiperf.input_prompts == ['first', 'second']
    for invalid in ([], ['only one'], ['first', ' '], ['x' * (4 * 1024 * 1024), 'last']):
        config['input_prompts'] = invalid
        with pytest.raises(ValueError):
            parse_perf_task_arguments(payload)
    config['input_prompts'] = ['first', 'second']
    config['cache_experiment'] = {'target_ratios': [0.8], 'total_input_tokens': 100}
    with pytest.raises(ValueError, match='cannot be combined'):
        parse_perf_task_arguments(payload)
