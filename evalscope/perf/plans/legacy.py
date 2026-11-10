"""Read-only migration facts for the legacy prefix-cache shell script.

The snapshot is evidence, not an executable plan: it keeps the source integers
(``235800``/``26199``, ``900000``/``99999``) exactly as measured so a converted
plan can be compared against the source instead of re-deriving a "clean" value.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field, StrictInt

from evalscope.utils import BaseArgument

from .alignment import LegacySourceFacts
from .documents import (
    DeploymentFacts,
    PerfPlanDocument,
    PlanConnection,
    PlanExecution,
    PlanLoad,
    PlanLoadPoint,
    PlanPriming,
    PlanWarmup,
    SyntheticCase,
    SyntheticWorkload,
)

LEGACY_SNAPSHOT_KIND = 'iss-eval.migration-source-snapshot'


class LegacySnapshotError(ValueError):
    """The snapshot is not the documented migration source."""


class LegacySnapshot(BaseArgument):
    """The subset of the snapshot used for mapping; unknown keys are ignored on purpose."""

    model_config = ConfigDict(extra='ignore', protected_namespaces=(), validate_default=True)

    kind: Literal['iss-eval.migration-source-snapshot'] = LEGACY_SNAPSHOT_KIND
    not_an_executable_plan: bool = True
    source_script: str = ''
    source_runtime_version: Optional[str] = None
    source_defaults: Dict[str, Any] = Field(default_factory=dict)
    local_awk_observation: Dict[str, Any] = Field(default_factory=dict)
    deployment_document: Dict[str, Any] = Field(default_factory=dict)
    script_formal_cases: Optional[StrictInt] = None
    script_formal_requests: Optional[StrictInt] = None
    script_ordinary_warmup_requests: Optional[StrictInt] = None
    script_explicit_primer_requests: Optional[StrictInt] = None


def _awk_cases(snapshot: LegacySnapshot) -> List[Dict[str, Any]]:
    cases = snapshot.local_awk_observation.get('cases')
    if not isinstance(cases, list):
        return []
    return [case for case in cases if isinstance(case, dict)]


def legacy_source_facts(snapshot: LegacySnapshot) -> LegacySourceFacts:
    """Normalize the snapshot into explicit source facts; nothing is recomputed."""
    defaults = snapshot.source_defaults
    warmup = defaults.get('warmup') if isinstance(defaults.get('warmup'), dict) else {}
    deployment = snapshot.deployment_document
    version = snapshot.source_runtime_version
    return LegacySourceFacts(
        script_name=snapshot.source_script or 'unknown',
        runtime_version=None if not version or str(version).lower() == 'unknown' else str(version),
        model=defaults.get('model'),
        url=defaults.get('url'),
        tokenizer=defaults.get('tokenizer'),
        endpoint_type=defaults.get('endpoint_type'),
        streaming=defaults.get('streaming'),
        input_lengths=[int(value) for value in defaults.get('input_lengths', [])],
        target_prefix_ratio=defaults.get('target_prefix_ratio'),
        output_tokens=defaults.get('output_tokens'),
        request_count=defaults.get('request_count'),
        concurrencies=[int(value) for value in defaults.get('concurrencies', [])],
        repetitions=defaults.get('repetitions'),
        order=defaults.get('order'),
        warmup_request_count=warmup.get('request_count'),
        warmup_input_tokens=warmup.get('input_tokens'),
        warmup_output_tokens=warmup.get('output_tokens'),
        warmup_concurrency=warmup.get('concurrency'),
        warmup_failure_policy=warmup.get('failure_policy'),
        declared_max_model_len=deployment.get('max_model_len'),
        routing_policy=deployment.get('routing_policy'),
        awk_cases=_awk_cases(snapshot),
    )


def plan_from_legacy(
    snapshot: LegacySnapshot,
    *,
    model: str,
    url: str,
    tokenizer_path: str,
    intent: Literal['diagnostic', 'benchmark'] = 'diagnostic',
) -> PerfPlanDocument:
    """Map the source defaults onto a portable plan without widening or shrinking values.

    The connection identity is supplied explicitly: the script defaults point at
    a different model and endpoint than the deployment document, so inferring
    them from a file path would silently test the wrong service.
    """
    facts = legacy_source_facts(snapshot)
    cases = []
    for case in facts.awk_cases:
        declared = int(case['declared_total'])
        cases.append(
            SyntheticCase(
                case_id=f'legacy-{declared}',
                declared_total_input_tokens=declared,
                shared_system_tokens=int(case['shared']),
                unique_user_tokens=int(case['unique']),
                target_prefix_ratio=facts.target_prefix_ratio,
            )
        )
    if not cases:
        raise LegacySnapshotError('The snapshot has no measured shared/unique split to migrate')
    deployment = snapshot.deployment_document
    return PerfPlanDocument(
        name=f'Legacy migration of {facts.script_name}',
        intent=intent,
        connection=PlanConnection(model=model, url=url, tokenizer_path=tokenizer_path),
        deployment=DeploymentFacts(
            mode=deployment.get('mode', 'unknown')
            if deployment.get('mode') in ('aggregated', 'disaggregated')
            else 'unknown',
            declared_max_model_len=facts.declared_max_model_len,
            evidence='declared' if facts.declared_max_model_len else 'unknown',
            routing_policy='round_robin' if facts.routing_policy == 'round_robin' else 'unknown',
            routing_coverage='unknown',
            max_num_seqs=deployment.get('max_num_seqs'),
        ),
        workload=SyntheticWorkload(kind='aiperf_synthetic', cases=cases),
        load=PlanLoad(
            points=[PlanLoadPoint(concurrency=value) for value in facts.concurrencies],
            request_count=facts.request_count or 1,
            output_token_limit=facts.output_tokens or 1,
            repetitions=facts.repetitions or 1,
        ),
        warmup=PlanWarmup(
            enabled=bool(facts.warmup_request_count),
            request_count=facts.warmup_request_count or 3,
            input_tokens=facts.warmup_input_tokens or 16000,
            output_token_limit=facts.warmup_output_tokens or 1000,
            concurrency=facts.warmup_concurrency or 1,
            failure_policy='record_and_continue' if facts.warmup_failure_policy == 'record_and_continue' else 'abort',
        ),
        priming=PlanPriming(policy='none'),
        execution=PlanExecution(order='load_then_input', seed=42),
    )
