"""Matrix execution: every tier sends its own input/output length and load point.

The executor is faked at its process boundary, never at the plan boundary: the
managed path drives the real cache scenario (real priming requests, real input
files) and the synthetic path drives a fake executable that records the argv it
was given. Assertions look at what the server and the CLI actually received, so
a run that silently reused the first tier's output length cannot pass.
"""

import json
import shutil
import urllib.request
from collections import Counter
from pathlib import Path

import pytest

from evalscope.perf.executors.models import ExecutionContext, ExternalRuntime
from evalscope.perf.plans import compile_plan_text
from evalscope.perf.plans.execution import run_perf_plan
from tests.perf.test_prefix_priming import _server
from tests.perf.test_prefix_workload_plan import _Tokenizer

FIXTURES = Path(__file__).parent / 'fixtures' / 'aiperf'


def _managed_plan(url: str) -> str:
    """2 input × 2 output × 2 ratio × 2 concurrency = 16 runs."""
    document = {
        'kind': 'iss-eval.perf-plan',
        'schema_version': '1.1',
        'name': 'Managed matrix execution',
        'intent': 'benchmark',
        'connection': {
            'model': 'mock-model',
            'url': url,
            'tokenizer_path': '/tokenizer',
            'protocol': 'openai_chat',
            'streaming': True,
        },
        'deployment': {'mode': 'aggregated', 'declared_max_model_len': 256, 'evidence': 'declared'},
        'workload': {
            'kind': 'token_prefix',
            'input_lengths': [20, 24],
            'target_ratios': [0.0, 0.9],
            'prefix_pool_size': 1,
            'seed': 7,
        },
        'load': {
            'mode': 'closed_loop',
            'points': [{'concurrency': 1}, {'concurrency': 2}],
            'request_count': 2,
            'output_token_limits': [4, 6],
            'repetitions': 1,
        },
        'warmup': {'enabled': False, 'request_count': 1, 'input_tokens': 8, 'output_token_limit': 4, 'concurrency': 1},
        'priming': {'policy': 'prime_all_prefixes', 'timeout_seconds': 30},
        'execution': {'order': 'seeded_shuffle', 'seed': 7},
        'telemetry': {'required': False, 'targets': []},
    }
    return json.dumps(document)


def _synthetic_plan(mode: str) -> str:
    points = [{'concurrency': 1}, {'concurrency': 3}] if mode == 'closed_loop' else [{'rate': 1.5}, {'rate': 2.5}]
    document = {
        'kind': 'iss-eval.perf-plan',
        'schema_version': '1.1',
        'name': f'Synthetic {mode} matrix',
        'intent': 'diagnostic',
        'connection': {
            'model': 'served-model',
            'url': 'http://127.0.0.1:9/v1',
            'tokenizer_path': '/tokenizer',
            'protocol': 'openai_chat',
            'streaming': True,
        },
        'deployment': {'mode': 'aggregated', 'declared_max_model_len': 4096, 'evidence': 'declared'},
        'workload': {
            'kind': 'aiperf_synthetic',
            'cases': [
                {
                    'case_id': 'case-a',
                    'declared_total_input_tokens': 120,
                    'shared_system_tokens': 100,
                    'unique_user_tokens': 20,
                    'target_prefix_ratio': 0.8333,
                }
            ],
            'apply_chat_template': False,
            'cache_bust': 'none',
        },
        'load': {
            'mode': mode,
            'points': points,
            'request_count': 4,
            'output_token_limits': [32, 64],
            'repetitions': 1,
        },
        'warmup': {'enabled': False, 'request_count': 1, 'input_tokens': 8, 'output_token_limit': 4, 'concurrency': 1},
        'priming': {'policy': 'none', 'timeout_seconds': 30},
        'execution': {'order': 'load_then_input'},
        'telemetry': {'required': False, 'targets': []},
    }
    return json.dumps(document)


def _context(root: Path, executable: str = '/unused-aiperf') -> ExecutionContext:
    return ExecutionContext(
        task_id='matrix-execution',
        output_dir=str(root),
        runtime=ExternalRuntime(executable=executable, graceful_timeout=5, terminate_timeout=2, kill_timeout=2),
    )


def _fake_executable(tmp_path: Path) -> Path:
    """A fake AIPerf that reports its version and records every argv it receives.

    It also writes the two artifacts a completed run must leave behind, so the
    synthetic path proceeds through its import step instead of reporting a
    failure. The argv capture lives next to the run directory, not in the
    artifact directory, so the artifact layout stays realistic.
    """
    summary = FIXTURES / 'profile_export_aiperf.json'
    records = FIXTURES / 'profile_export.jsonl'
    script = tmp_path / 'fake-aiperf'
    script.write_text(
        f'''#!/usr/bin/env python3
import json, shutil, sys
from pathlib import Path
if '--version' in sys.argv:
    print('0.13.0')
    sys.exit(0)
argv = sys.argv[1:]
artifact = Path(argv[argv.index('--output-artifact-dir') + 1])
root = artifact.parent.parent
with (root / 'cli.jsonl').open('a', encoding='utf-8') as capture:
    capture.write(json.dumps(argv) + '\\n')
artifact.mkdir(parents=True, exist_ok=True)
shutil.copyfile({str(summary)!r}, artifact / 'profile_export_aiperf.json')
shutil.copyfile({str(records)!r}, artifact / 'profile_export.jsonl')
'''
    )
    script.chmod(0o755)
    return script


def test_managed_matrix_sends_each_tier_its_own_input_and_output_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import evalscope.perf.experiments.scenario as scenario_module

    root = tmp_path / 'perf'
    root.mkdir()
    observed_order: list[str] = []

    monkeypatch.setattr(scenario_module, 'probe_version', lambda context, cancel: '0.13.0')

    def fake_run(
        arguments,
        context: ExecutionContext,
        input_file: str,
        version: str,
        cancel_event: object,
        on_draining: object,
    ):
        rows = [json.loads(line) for line in Path(input_file).read_text().splitlines()]
        # The input file the executor will profile must carry this tier's OSL,
        # not the document's first output limit.
        assert {row['output_length'] for row in rows} == {arguments.load.output_token_limit}
        point = arguments.load.points[0]
        assert arguments.load.mode == 'closed_loop' and point.concurrency is not None
        observed_order.append(Path(context.output_dir).name)
        for row in rows:
            body = {
                'model': 'mock-model',
                'messages': [
                    {'role': 'system', 'content': 'shared'},
                    {'role': 'user', 'content': row['text']},
                ],
                'max_completion_tokens': row['output_length'],
                'stream': True,
            }
            request = urllib.request.Request(
                f'{arguments.url}/chat/completions',
                data=json.dumps(body).encode(),
                headers={'Content-Type': 'application/json'},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
        raw = Path(context.output_dir) / 'raw' / 'aiperf'
        raw.mkdir(parents=True)
        for name in ('profile_export.jsonl', 'profile_export_aiperf.json'):
            shutil.copyfile(FIXTURES / name, raw / name)
        from evalscope.perf.executors.models import ExecutionOutcome

        return ExecutionOutcome(
            exit_reason='completed',
            exit_code=0,
            cleanup_confirmed=True,
            artifact_status='available',
            raw_artifact_dir=str(raw),
            executor_version=version,
        )

    monkeypatch.setattr(scenario_module, '_run_verified', fake_run)
    with _server(tmp_path, 'success') as (url, capture):
        plan = compile_plan_text(_managed_plan(url))
        assert plan.run_count == 16
        outcome = run_perf_plan(
            plan,
            _context(root),
            source_text=_managed_plan(url),
            tokenizer=_Tokenizer(),
            token_pool=list(range(200)),
            tokenizer_id='fixture',
            template_id='fixture-template',
        )

    assert outcome.status == 'completed'
    assert outcome.completed_runs == 16 and outcome.total_runs == 16
    # The orchestrator runs the compiled order exactly once: no re-shuffle.
    assert observed_order == [run.run_id for run in plan.runs]

    captured = [json.loads(line) for line in capture.read_text().splitlines()]
    profiling = [item for item in captured if item['body']['max_completion_tokens'] > 1]
    assert len(profiling) == 32
    profile_counter = Counter(
        (
            len(item['body']['messages'][-1]['content'].split()) + 3,
            item['body']['max_completion_tokens'],
        )
        for item in profiling
    )
    assert profile_counter == {
        (20, 4): 8,
        (20, 6): 8,
        (24, 4): 8,
        (24, 6): 8,
    }
    # Primers stay at one token and never borrow the tier's output budget.
    primers = [item for item in captured if item['body']['max_completion_tokens'] == 1]
    assert len(primers) == 8

    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert manifest['status'] == 'completed'
    assert len(manifest['runs']) == 16
    assert [row['status'] for row in manifest['runs']] == ['completed'] * 16
    dimensions = {
        (row['input_tokens'], row['output_token_limit'], row['target_ratio'], row['concurrency']) for row in manifest['runs']
    }
    assert dimensions == {
        (input_tokens, output_tokens, ratio, concurrency)
        for input_tokens in (20, 24)
        for output_tokens in (4, 6)
        for ratio in (0.0, 0.9)
        for concurrency in (1, 2)
    }
    assert all(row['load_mode'] == 'closed_loop' and row['rate'] is None for row in manifest['runs'])
    # Formal and priming budgets stay separate: only the 8 ratio>0 runs prime,
    # and no primer is ever charged against a run's formal request count.
    assert sum(row['formal_requests'] for row in manifest['runs']) == 32
    assert sum(row['primer_requests'] for row in manifest['runs']) == 8
    assert all(row['primer_requests'] == 0 for row in manifest['runs'] if row['target_ratio'] == 0.0)
    assert all(row['formal_requests'] == 2 for row in manifest['runs'])


def test_arguments_build_one_load_point_per_run() -> None:
    """Each run's arguments carry its own load point, OSL and request count."""
    from evalscope.perf.plans.execution import _arguments, display_arguments

    closed = compile_plan_text(_synthetic_plan('closed_loop'))
    for run in closed.runs:
        arguments = _arguments(closed, run=run)
        assert arguments.load.mode == 'closed_loop'
        assert [(point.concurrency, point.rate) for point in arguments.load.points] == [(run.concurrency, None)]
        assert arguments.load.output_token_limit == run.output_token_limit
        assert arguments.load.request_count == run.request_count

    opened = compile_plan_text(_synthetic_plan('open_loop'))
    for run in opened.runs:
        arguments = _arguments(opened, run=run)
        assert arguments.load.mode == 'open_loop'
        assert [(point.concurrency, point.rate) for point in arguments.load.points] == [(None, run.rate)]
        assert arguments.load.output_token_limit == run.output_token_limit
    display = display_arguments(opened)
    assert display.load.mode == 'open_loop' and display.load.points[0].rate == opened.runs[0].rate


@pytest.mark.parametrize('mode', ['closed_loop', 'open_loop'])
def test_synthetic_cli_uses_only_the_declared_load_flag(tmp_path: Path, mode: str) -> None:
    root = tmp_path / 'perf'
    root.mkdir()
    executable = _fake_executable(tmp_path)
    plan = compile_plan_text(_synthetic_plan(mode))
    assert plan.run_count == 4

    outcome = run_perf_plan(plan, _context(root, executable=str(executable)), source_text=_synthetic_plan(mode))

    assert outcome.status == 'completed'
    expected_osl = {run.output_token_limit for run in plan.runs}
    seen_osl: set[int] = set()
    seen_points: set[str] = set()
    for run in plan.runs:
        argv = json.loads((root / 'runs' / run.run_id / 'cli.jsonl').read_text().splitlines()[0])
        flags = dict(zip(argv[:-1], argv[1:]))
        assert flags['--osl'] == str(run.output_token_limit)
        seen_osl.add(int(flags['--osl']))
        assert flags['--request-count'] == str(run.request_count)
        if mode == 'closed_loop':
            assert '--concurrency' in flags and '--request-rate' not in flags
            assert int(flags['--concurrency']) == run.concurrency
            seen_points.add(f'c{flags["--concurrency"]}')
        else:
            assert '--request-rate' in flags and '--concurrency' not in flags
            assert float(flags['--request-rate']) == run.rate
            seen_points.add(f'r{flags["--request-rate"]}')
    assert seen_osl == expected_osl == {32, 64}
    assert seen_points == ({'c1', 'c3'} if mode == 'closed_loop' else {'r1.5', 'r2.5'})

    manifest = json.loads((root / 'plan' / 'manifest.json').read_text())
    assert {row['load_mode'] for row in manifest['runs']} == {mode}
    if mode == 'open_loop':
        assert all(row['concurrency'] is None and row['rate'] in (1.5, 2.5) for row in manifest['runs'])
    else:
        assert all(row['concurrency'] in (1, 3) and row['rate'] is None for row in manifest['runs'])
