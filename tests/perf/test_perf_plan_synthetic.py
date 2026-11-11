"""Synthetic compatibility: server-built flags, controlled process and artifact checks."""

import json
import os
import stat
from pathlib import Path

import pytest

from evalscope.perf.executors.arguments import AIPerfArguments
from evalscope.perf.executors.models import ExecutionContext, ExternalRuntime
from evalscope.perf.integrations.aiperf.synthetic import (
    SyntheticRunSpec,
    build_synthetic_arguments,
    prepare_synthetic_run,
    run_synthetic,
    validate_synthetic_spec,
)
from evalscope.perf.plans import compile_plan_text

FIXTURES = Path(__file__).parent / 'fixtures' / 'perf_plan'
AIPERF_FIXTURES = Path(__file__).parent / 'fixtures' / 'aiperf'


def _flags(arguments: list[str]) -> dict:
    flags: dict = {}
    index = 1
    while index < len(arguments):
        name = arguments[index]
        if name == '--streaming':
            flags[name] = True
            index += 1
        else:
            flags[name] = arguments[index + 1]
            index += 2
    return flags


def _spec(**overrides) -> SyntheticRunSpec:
    values = {
        'case_id': 'input-1000',
        'model': 'served-model',
        'url': 'http://127.0.0.1:8000/v1',
        'tokenizer_path': '/models/tokenizer',
        'shared_system_tokens': 900,
        'unique_user_tokens': 100,
        'output_token_limit': 32,
        'request_count': 16,
        'concurrency': 4,
    }
    values.update(overrides)
    return SyntheticRunSpec(**values)


def test_flags_reproduce_the_shared_system_split() -> None:
    arguments = build_synthetic_arguments(_spec(), '/tmp/artifacts')
    assert arguments[0] == 'profile'
    flags = _flags(arguments)
    assert flags['--shared-system-prompt-length'] == '900'
    assert flags['--isl'] == '100'
    assert flags['--osl'] == '32'
    assert flags['--request-count'] == '16'
    assert flags['--concurrency'] == '4'
    assert flags['--endpoint-type'] == 'chat'
    assert flags['--url'] == 'http://127.0.0.1:8000/v1'
    assert '--streaming' in arguments
    assert flags['--output-artifact-dir'] == '/tmp/artifacts'


def test_chat_completions_url_is_normalized_to_the_api_base() -> None:
    arguments = build_synthetic_arguments(_spec(url='http://127.0.0.1:8000/v1/chat/completions'), '/tmp/artifacts')
    flags = _flags(arguments)
    assert flags['--url'] == 'http://127.0.0.1:8000/v1'
    prefixed = build_synthetic_arguments(
        _spec(url='http://127.0.0.1:8000/proxy/v1/chat/completions'), '/tmp/artifacts'
    )
    assert _flags(prefixed)['--url'] == 'http://127.0.0.1:8000/proxy/v1'
    with pytest.raises(ValueError):
        build_synthetic_arguments(_spec(url='http://127.0.0.1:8000/v1?token=leak'), '/tmp/artifacts')


@pytest.mark.parametrize(
    'overrides',
    [
        {'stream': False},
        {'apply_chat_template': True},
        {'cache_bust': 'tokens'},
    ],
)
def test_unsupported_semantics_are_rejected(overrides: dict) -> None:
    with pytest.raises(ValueError):
        validate_synthetic_spec(_spec(**overrides))


def test_credentials_on_the_command_line_are_rejected() -> None:
    arguments = AIPerfArguments(
        model='served-model',
        url='http://127.0.0.1:8000/v1',
        tokenizer_path='/models/tokenizer',
        api_key='sk-secret',
        load={'points': [{'concurrency': 1}], 'request_count': 1, 'output_token_limit': 1},
    )
    with pytest.raises(ValueError, match='API key'):
        validate_synthetic_spec(_spec(), arguments)


def test_model_is_one_argv_element_and_never_a_command(tmp_path: Path) -> None:
    marker = tmp_path / 'executed'
    model = f'model $(touch {marker})'
    arguments = build_synthetic_arguments(_spec(model=model), '/tmp/artifacts')
    flags = _flags(arguments)
    assert flags['--model'] == model
    assert sum(1 for item in arguments if item.startswith('--')) == 11
    assert not marker.exists()


def test_artifact_directory_is_private_and_inside_the_task(tmp_path: Path) -> None:
    root = tmp_path / 'task'
    root.mkdir()
    context = ExecutionContext(task_id='task', output_dir=str(root), runtime=ExternalRuntime(executable='/aiperf'))
    prepared = prepare_synthetic_run(_spec(), context)
    directory = Path(prepared.artifact_dir)
    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    with pytest.raises(ValueError, match='already exists'):
        prepare_synthetic_run(_spec(), context)
    outside = tmp_path / 'outside'
    outside.mkdir()
    with pytest.raises(ValueError):
        prepare_synthetic_run(_spec(), ExecutionContext(task_id='t', output_dir=str(outside), runtime=None))


def _fake_aiperf(tmp_path: Path) -> Path:
    script = tmp_path / 'fake-aiperf'
    script.write_text(
        '#!/usr/bin/env python3\n'
        'import json, sys\n'
        'from pathlib import Path\n'
        'if "--version" in sys.argv:\n'
        '    print("0.13.0")\n'
        '    raise SystemExit(0)\n'
        'argv = sys.argv[1:]\n'
        'target = Path(argv[argv.index("--output-artifact-dir") + 1])\n'
        '(target / "argv.json").write_text(json.dumps(argv))\n'
        'fixtures = Path("' + str(AIPERF_FIXTURES) + '")\n'
        '(target / "profile_export.jsonl").write_text((fixtures / "profile_export.jsonl").read_text())\n'
        '(target / "profile_export_aiperf.json").write_text((fixtures / "profile_export_aiperf.json").read_text())\n'
    )
    script.chmod(0o755)
    return script


@pytest.mark.skipif(os.name != 'posix', reason='the fake runtime is a POSIX script')
def test_synthetic_run_records_exit_reason_and_artifacts(tmp_path: Path) -> None:
    script = _fake_aiperf(tmp_path)
    root = tmp_path / 'task'
    root.mkdir()
    context = ExecutionContext(
        task_id='task', output_dir=str(root), runtime=ExternalRuntime(executable=str(script))
    )
    outcome = run_synthetic(_spec(url='http://127.0.0.1:9/v1'), context)
    assert outcome.exit_reason == 'completed'
    assert outcome.artifact_status == 'available'
    assert outcome.executor_version == '0.13.0'
    argv = json.loads((Path(outcome.raw_artifact_dir) / 'argv.json').read_text())
    assert argv[0] == 'profile'
    assert '--shared-system-prompt-length' in argv


@pytest.mark.skipif(os.name != 'posix', reason='the fake runtime is a POSIX script')
def test_missing_export_is_not_reported_as_success(tmp_path: Path) -> None:
    script = tmp_path / 'fake-aiperf'
    script.write_text(
        '#!/usr/bin/env python3\n'
        'import sys\n'
        'if "--version" in sys.argv:\n'
        '    print("0.13.0")\n'
    )
    script.chmod(0o755)
    root = tmp_path / 'task'
    root.mkdir()
    context = ExecutionContext(
        task_id='task', output_dir=str(root), runtime=ExternalRuntime(executable=str(script))
    )
    outcome = run_synthetic(_spec(), context)
    assert outcome.exit_reason == 'error'
    assert outcome.artifact_status == 'missing'


def test_compiled_plan_keeps_the_shared_and_unique_split_for_execution() -> None:
    plan = compile_plan_text((FIXTURES / 'synthetic-small.json').read_text())
    run = plan.runs[0]
    assert (run.shared_system_tokens, run.unique_user_tokens) == (900, 100)
    assert run.declared_total_input_tokens == 1000
    assert run.message_structure == 'system_and_user'
    assert run.primer_requests == 0
