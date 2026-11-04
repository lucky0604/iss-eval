"""Deterministic cache experiment plans and accurate request budgets."""

import pytest

from evalscope.perf.executors.arguments import AIPerfArguments, WarmupConfiguration
from evalscope.perf.experiments.planner import plan_cache_experiment


def _config() -> AIPerfArguments:
    return AIPerfArguments(
        model='model', url='http://localhost/v1', tokenizer_path='/tokenizer', api_key='secret',
        cache_experiment={'target_ratios': [0, 0.8, 0.9], 'total_input_tokens': 100, 'prefix_pool_size': 2},
        load={'points': [{'concurrency': 2}, {'concurrency': 4}], 'request_count': 10,
              'output_token_limit': 5, 'repetitions': 3},
    )


def test_matrix_has_stable_unique_ids_and_budgets() -> None:
    config = _config()
    plan = plan_cache_experiment(config, 'task', 'tokenizer-hash', 'template-hash')
    assert len(plan.subruns) == 18
    assert plan.profiling_requests == 180 and plan.primer_requests == 24
    assert plan.max_profiling_output_tokens == 900 and plan.max_primer_output_tokens == 24
    assert all(run.output_token_limit == 5 for run in plan.subruns)
    assert len({run.subrun_id for run in plan.subruns}) == 18
    assert len({run.namespace for run in plan.subruns}) == 18
    assert [run.order for run in plan.subruns] == list(range(18))
    assert plan == plan_cache_experiment(config, 'task', 'tokenizer-hash', 'template-hash')
    assert 'secret' not in plan.model_dump_json()
    assert plan.plan_hash != plan_cache_experiment(config, 'task', 'tokenizer-hash', 'other-template').plan_hash
    assert plan.plan_hash != plan_cache_experiment(config, 'other-task', 'tokenizer-hash', 'template-hash').plan_hash


def test_duplicate_or_excessive_matrix_is_rejected() -> None:
    config = _config()
    assert config.cache_experiment is not None
    config.cache_experiment.target_ratios = [0.8, 0.8]
    with pytest.raises(ValueError, match='Duplicate target'):
        plan_cache_experiment(config, 'task', 'tokenizer', 'template')
    config = _config()
    config.load.repetitions = 2000
    with pytest.raises(ValueError, match='10000'):
        plan_cache_experiment(config, 'task', 'tokenizer', 'template')


def test_input_lengths_expand_the_matrix_and_keep_single_input_ids() -> None:
    config = _config()
    assert config.cache_experiment is not None
    config.cache_experiment.input_token_values = [100, 200]
    config.cache_experiment.warmup = WarmupConfiguration(
        request_count=3,
        concurrency=1,
        total_input_tokens=16000,
        output_token_limit=1000,
    )
    plan = plan_cache_experiment(config, 'task', 'tokenizer', 'template')
    assert len(plan.subruns) == 36
    assert {run.input_tokens for run in plan.subruns} == {100, 200}
    assert all(run.subrun_id.startswith(('i000-', 'i001-')) for run in plan.subruns)
    assert plan.warmup_requests == 3 and plan.max_warmup_output_tokens == 3000
    assert plan.profiling_requests == 360
    assert len({run.subrun_id for run in plan.subruns}) == 36
    assert len({run.namespace for run in plan.subruns}) == 36

    single = _config()
    single_plan = plan_cache_experiment(single, 'task', 'tokenizer', 'template')
    assert all(not run.subrun_id.startswith('i') for run in single_plan.subruns)
    assert {run.input_tokens for run in single_plan.subruns} == {100}


def test_synthetic_baseline_has_no_primers_and_no_routing_reason() -> None:
    config = _config()
    assert config.cache_experiment is not None
    config.cache_experiment.mode = 'synthetic_isl'
    config.cache_experiment.target_ratios = [0]
    plan = plan_cache_experiment(config, 'task', 'tokenizer', 'template')
    assert plan.primer_requests == 0
    assert {run.target_ratio for run in plan.subruns} == {0}
    assert plan.configuration is not None
    assert plan.configuration.cache.mode == 'synthetic_isl'
