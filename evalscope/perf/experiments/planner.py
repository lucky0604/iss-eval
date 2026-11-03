"""Deterministic cache-ratio, load, and repetition matrix planning."""

import hashlib
import json
import random
from typing import List, Literal, Optional

from pydantic import Field

from evalscope.perf.executors.arguments import AIPerfArguments, LoadPoint
from evalscope.perf.executors.urls import normalize_chat_url
from evalscope.perf.integrations.aiperf.single_run import validate_single_run_configuration
from evalscope.utils import BaseArgument


class CacheSubrunPlan(BaseArgument):
    """One uniquely named, independently seeded matrix member."""

    subrun_id: str
    order: int
    target_ratio: float
    load_mode: Literal['closed_loop', 'open_loop']
    load_point: LoadPoint
    repetition: int
    seed: int
    namespace: str
    request_count: int
    primer_requests: int
    output_token_limit: Optional[int] = None


class CacheExperimentPlan(BaseArgument):
    """A reproducible plan and its request budget."""

    schema_version: Literal['1.0'] = '1.0'
    plan_hash: str
    configuration_hash: str
    tokenizer_id: str
    template_id: str
    subruns: List[CacheSubrunPlan]
    profiling_requests: int = Field(ge=0)
    primer_requests: int = Field(ge=0)
    max_profiling_output_tokens: Optional[int] = Field(default=None, ge=0)
    max_primer_output_tokens: Optional[int] = Field(default=None, ge=0)


def plan_cache_experiment(
    arguments: AIPerfArguments, task_id: str, tokenizer_id: str, template_id: str
) -> CacheExperimentPlan:
    """Expand and deterministically order the complete Cartesian product."""
    cache = arguments.cache_experiment
    if cache is None:
        raise ValueError('A cache experiment is required')
    for point in arguments.load.points:
        load = arguments.load.model_copy(update={'points': [point], 'repetitions': 1})
        validate_single_run_configuration(arguments.model_copy(update={'cache_experiment': None, 'load': load}))
    if not task_id or not tokenizer_id or not template_id:
        raise ValueError('Task, tokenizer, and template identities are required')
    if len(set(cache.target_ratios)) != len(cache.target_ratios):
        raise ValueError('Duplicate target ratios are not supported')
    if len({point.model_dump_json() for point in arguments.load.points}) != len(arguments.load.points):
        raise ValueError('Duplicate load points are not supported')
    size = len(cache.target_ratios) * len(arguments.load.points) * arguments.load.repetitions
    if size > 10000:
        raise ValueError('Experiment exceeds the maximum of 10000 subruns')
    if any(ratio > 0 for ratio in cache.target_ratios) and cache.prefix_pool_size > arguments.load.request_count:
        raise ValueError('Every shared prefix requires a profiling request')
    base_url, endpoint = normalize_chat_url(arguments.url)
    identity = {
        'task_id': task_id,
        'model': arguments.model,
        'url': base_url + endpoint,
        'tokenizer_path': arguments.tokenizer_path,
        'tokenizer_id': tokenizer_id,
        'template_id': template_id,
        'stream': arguments.stream,
        'deployment_mode': arguments.deployment_mode,
        'cache': cache.model_dump(mode='json'),
        'load': arguments.load.model_dump(mode='json'),
        'telemetry': arguments.telemetry.model_dump(mode='json', exclude={'targets': {'__all__': {'headers'}}}),
    }
    config_hash = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    members = []
    for ratio_index, ratio in enumerate(cache.target_ratios):
        for load_index, point in enumerate(arguments.load.points):
            for repetition in range(arguments.load.repetitions):
                subrun_id = f'r{ratio_index:03d}-l{load_index:03d}-n{repetition:03d}'
                namespace = hashlib.sha256(f'{config_hash}:{subrun_id}'.encode()).hexdigest()
                members.append(
                    CacheSubrunPlan(
                        subrun_id=subrun_id,
                        order=0,
                        target_ratio=ratio,
                        load_mode=arguments.load.mode,
                        load_point=point,
                        repetition=repetition,
                        seed=cache.seed,
                        namespace=namespace,
                        request_count=arguments.load.request_count,
                        primer_requests=cache.prefix_pool_size if ratio else 0,
                        output_token_limit=arguments.load.output_token_limit,
                    )
                )
    random.Random(cache.seed).shuffle(members)
    for index, member in enumerate(members):
        member.order = index
    payload = {'config_hash': config_hash, 'subruns': [member.model_dump(mode='json') for member in members]}
    plan_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CacheExperimentPlan(
        plan_hash=plan_hash,
        configuration_hash=config_hash,
        tokenizer_id=tokenizer_id,
        template_id=template_id,
        subruns=members,
        profiling_requests=sum(member.request_count for member in members),
        primer_requests=sum(member.primer_requests for member in members),
        max_profiling_output_tokens=sum(member.request_count * arguments.load.output_token_limit for member in members),
        max_primer_output_tokens=sum(member.primer_requests for member in members),
    )
