"""Run identity is read from the AIPerf summary instead of being typed in."""

from pathlib import Path

from evalscope.perf.results.aiperf_import import (
    discover_run_directories,
    infer_run_identity,
    load_run_summary,
)

FIXTURES = Path(__file__).parent / 'fixtures' / 'aiperf'


def test_identity_comes_from_the_recorded_input_config() -> None:
    identity = infer_run_identity(load_run_summary(FIXTURES))
    assert identity.model == 'mock-model'
    assert identity.endpoint == 'http://127.0.0.1:18080'
    assert identity.load_mode == 'closed_loop'
    assert identity.concurrency == 1
    assert identity.rate is None
    assert identity.planned_requests == 2
    assert identity.output_token_limit == 2
    assert identity.sources['model'] == 'input_config.models'
    assert identity.sources['output_token_limit'] == 'input_config.datasets'


def test_open_loop_run_reads_its_rate() -> None:
    summary = {
        'input_config': {
            'models': {'items': [{'name': 'm'}]},
            'phases': [{'kind': 'profiling', 'type': 'rate', 'request_rate': 12.5, 'requests': 40}],
        }
    }
    identity = infer_run_identity(summary)
    assert identity.load_mode == 'open_loop'
    assert identity.rate == 12.5
    assert identity.concurrency is None
    assert identity.planned_requests == 40


def test_cli_command_only_fills_gaps_left_by_the_config() -> None:
    summary = {
        'run_info': {
            'cli_command': 'aiperf profile --model cm --concurrency 4 --request-count 8 --osl 128 --endpoint /v1'
        }
    }
    identity = infer_run_identity(summary)
    assert identity.model == 'cm'
    assert identity.load_mode == 'closed_loop'
    assert identity.concurrency == 4
    assert identity.planned_requests == 8
    assert identity.output_token_limit == 128
    assert identity.sources['model'] == 'run_info.cli_command'

    recorded = {'input_config': {'models': {'items': [{'name': 'structured'}]}}}
    assert infer_run_identity({**summary, **recorded}).model == 'structured'


def test_missing_summary_leaves_every_field_unknown() -> None:
    identity = infer_run_identity(None)
    assert identity.model is None
    assert identity.load_mode is None
    assert identity.output_token_limit is None
    assert identity.sources == {}


def test_run_discovery_handles_flat_and_batch_layouts(tmp_path: Path) -> None:
    flat = tmp_path / 'flat'
    flat.mkdir()
    (flat / 'profile_export.jsonl').write_text('', encoding='utf-8')
    assert discover_run_directories(flat.resolve()) == [flat.resolve()]

    batch = tmp_path / 'batch'
    for name in ('conc_1', 'conc_2'):
        run = batch / 'exports' / name
        run.mkdir(parents=True)
        (run / 'profile_export.jsonl').write_text('', encoding='utf-8')
    assert [run.name for run in discover_run_directories(batch.resolve())] == ['conc_1', 'conc_2']
