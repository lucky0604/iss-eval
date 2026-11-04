"""Serial execution of a reproducible prefix-cache experiment matrix."""

import os
import stat
from pathlib import Path
from typing import Literal, Optional, Sequence

from evalscope.perf.executors.arguments import AIPerfArguments
from evalscope.perf.executors.external import Cancellation
from evalscope.perf.executors.models import ExecutionContext
from evalscope.perf.experiments.planner import plan_cache_experiment
from evalscope.perf.experiments.prefix_workload import PrefixTokenizer
from evalscope.perf.experiments.scenario import CacheScenarioArguments, _ScenarioCancellation, run_cache_scenario
from evalscope.perf.experiments.storage import write_experiment_json
from evalscope.utils import BaseArgument


class CacheExperimentOutcome(BaseArgument):
    """Small task result; the experiment manifest contains subrun details."""

    status: Literal['completed', 'stopped', 'error']
    quality: Literal['partial', 'invalid']
    plan_hash: str
    completed_subruns: int
    total_subruns: int
    cleanup_confirmed: bool
    manifest: str = 'experiment.json'


def run_cache_experiment(
    arguments: AIPerfArguments,
    context: ExecutionContext,
    tokenizer: PrefixTokenizer,
    token_pool: Sequence[int],
    tokenizer_id: str,
    template_id: str,
    cancel_event: Optional[Cancellation] = None,
) -> CacheExperimentOutcome:
    """Run planned subruns serially without implicit retry or resume."""
    plan = plan_cache_experiment(arguments, context.task_id, tokenizer_id, template_id)
    if context.expected_plan_hash is not None and context.expected_plan_hash != plan.plan_hash:
        raise ValueError('The experiment plan changed after validation')
    if context.runtime is None:
        raise ValueError('AIPerf runtime is not configured')
    if getattr(tokenizer, 'tokenizer_hash', tokenizer_id) != tokenizer_id:
        raise ValueError('Tokenizer identity does not match the experiment')
    if getattr(tokenizer, 'template_hash', template_id) != template_id:
        raise ValueError('Tokenizer template identity does not match the experiment')
    root = Path(context.output_dir).resolve(strict=True)
    lock_path = root / '.experiment-created'
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    os.close(fd)
    runs_dir = root / 'runs'
    try:
        runs_dir.mkdir(exist_ok=False)
        write_experiment_json(root / 'experiment_plan.json', plan.model_dump(mode='json'))
    except BaseException:
        lock_path.unlink(missing_ok=True)
        raise
    rows = [{'subrun_id': item.subrun_id, 'status': 'not_started', 'quality': None} for item in plan.subruns]
    state = {
        'schema_version': '1.0',
        'plan_hash': plan.plan_hash,
        'status': 'running',
        'phase': 'preflight',
        'current_subrun': None,
        'completed_subruns': 0,
        'total_subruns': len(rows),
        'subruns': rows,
    }
    manifest = root / 'experiment.json'
    write_experiment_json(manifest, state)
    cancellation = _ScenarioCancellation(cancel_event)
    status = 'completed'
    cleanup_confirmed = True
    for index, item in enumerate(plan.subruns):
        if cancellation.is_set():
            status = 'stopped'
            break
        row = rows[index]
        row['status'] = 'running'
        state.update({'current_subrun': item.subrun_id, 'phase': 'preflight'})
        write_experiment_json(manifest, state)
        run_dir = runs_dir / item.subrun_id
        try:
            run_dir.mkdir(exist_ok=False)
            load = arguments.load.model_copy(update={'points': [item.load_point], 'repetitions': 1})
            sub_arguments = arguments.model_copy(update={'load': load})
            sub_context = context.model_copy(update={'output_dir': str(run_dir)})
            scenario = CacheScenarioArguments(
                target_ratio=item.target_ratio,
                namespace=item.namespace,
                tokenizer_id=tokenizer_id,
                template_id=template_id,
            )

            def publish_phase(phase: str) -> None:
                if phase in ('preflight', 'generating', 'priming', 'profiling', 'draining', 'importing'):
                    state['phase'] = phase
                    write_experiment_json(manifest, state)

            outcome = run_cache_scenario(
                sub_arguments, sub_context, scenario, tokenizer, token_pool, cancellation, on_phase=publish_phase
            )
            row['status'] = outcome.status
            row['quality'] = outcome.quality
            row['scenario'] = f'runs/{item.subrun_id}/scenario.json'
            if outcome.reason == 'invalid_prefix_workload':
                try:
                    descriptor = os.open(
                        root / 'benchmark.log',
                        os.O_CREAT
                        | os.O_APPEND
                        | os.O_WRONLY
                        | getattr(os, 'O_NOFOLLOW', 0)
                        | getattr(os, 'O_NONBLOCK', 0),
                        0o600,
                    )
                    try:
                        if stat.S_ISREG(os.fstat(descriptor).st_mode):
                            os.write(
                                descriptor,
                                b'Prefix workload is invalid; check tokenizer, template, target ratios and prefix pool.\n',
                            )
                    finally:
                        os.close(descriptor)
                except OSError:
                    pass
            if not outcome.cleanup_confirmed:
                cleanup_confirmed = False
                status = 'error'
                break
            if outcome.status == 'cancelled':
                status = 'stopped'
                break
            if outcome.status != 'completed' or outcome.quality == 'invalid':
                status = 'error'
                break
            state['completed_subruns'] += 1
            write_experiment_json(manifest, state)
            if cancellation.is_set() and index + 1 < len(plan.subruns):
                status = 'stopped'
                break
        except Exception:
            row['status'] = 'error'
            row['quality'] = 'invalid'
            cleanup_confirmed = False
            status = 'error'
            break
    state.update(
        {
            'status': status,
            'last_phase': state.get('phase'),
            'phase': None,
            'current_subrun': None,
            'quality': 'invalid' if status == 'error' else 'partial',
        }
    )
    write_experiment_json(manifest, state)
    return CacheExperimentOutcome(
        status=status,
        quality='invalid' if status == 'error' else 'partial',
        plan_hash=plan.plan_hash,
        completed_subruns=state['completed_subruns'],
        total_subruns=len(rows),
        cleanup_confirmed=cleanup_confirmed,
    )
