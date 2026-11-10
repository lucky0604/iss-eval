"""Comparison of plan semantics and execution-time identity.

Two questions are deliberately separate:

* :func:`compare_plan_semantics` answers "do these two accepted plans describe
  the same measurement?" from the semantic fingerprint and the hash algorithm
  version.
* :func:`compare_effective_identities` answers "did these two executions
  actually use the same tokenizer, template and runtime?" from the archived
  runtime facts.

Neither function infers identity from a path, a task id or an output location,
and an unknown fact never yields ``matched``.
"""

from typing import List, Literal, Optional, Tuple

from pydantic import Field

from evalscope.utils import BaseArgument

from .compiler import CompiledPerfPlan
from .provenance import EffectiveIdentity

_DEPLOYMENT_LABELS = (
    ('model_revision', 'model revision'),
    ('engine_image_digest', 'engine image digest'),
    ('engine_version', 'engine version'),
    ('deployment_id', 'deployment id'),
    ('tp', 'TP'),
    ('dp', 'DP'),
    ('pp', 'PP'),
    ('ep', 'EP'),
    ('kv_dtype', 'KV cache dtype'),
    ('prefix_cache', 'prefix cache'),
    ('routing_policy', 'routing policy'),
    ('routing_coverage', 'routing coverage'),
    ('max_num_seqs', 'max num seqs'),
    ('declared_max_model_len', 'declared context limit'),
    ('mode', 'deployment mode'),
    ('node_count', 'node count'),
    ('gpu_count', 'GPU count'),
)


class IdentityComparison(BaseArgument):
    """A comparison verdict with the reasons that produced it."""

    status: Literal['matched', 'different', 'unknown']
    reasons: List[str] = Field(default_factory=list)


def deployment_differences(left: CompiledPerfPlan, right: CompiledPerfPlan) -> List[str]:
    """List declared deployment facts that differ, for an explicit explanation."""
    first = left.normalized.deployment
    second = right.normalized.deployment
    differences: List[str] = []
    for field, label in _DEPLOYMENT_LABELS:
        before, after = getattr(first, field), getattr(second, field)
        if before != after:
            differences.append(f'{label}: {before!r} -> {after!r}')
    return differences


def compare_plan_semantics(left: CompiledPerfPlan, right: CompiledPerfPlan) -> IdentityComparison:
    """Return whether two accepted plans are directly comparable."""
    if left.contract_version != right.contract_version:
        return IdentityComparison(
            status='unknown',
            reasons=[
                f'contract version {left.contract_version} != {right.contract_version}; recompilation is required'
            ],
        )
    if left.compiler_version != right.compiler_version:
        return IdentityComparison(
            status='unknown',
            reasons=[
                f'compiler version {left.compiler_version} != {right.compiler_version}; the fingerprint inputs changed'
            ],
        )
    if left.semantic_fingerprint == right.semantic_fingerprint:
        return IdentityComparison(status='matched', reasons=['semantic fingerprints are identical'])
    differences = deployment_differences(left, right)
    reasons = differences or ['workload, load, warmup, priming or run matrix differ']
    if left.normalized.connection.model != right.normalized.connection.model:
        reasons.insert(0, 'model differs')
    return IdentityComparison(status='different', reasons=reasons)


def _tokenizer_reason(left: EffectiveIdentity, right: EffectiveIdentity) -> Optional[Tuple[str, str]]:
    """Return ``(status, reason)`` for the tokenizer/template content identity."""
    for label, first, second in (
        ('tokenizer content', left.tokenizer.content_hash, right.tokenizer.content_hash),
        ('chat template', left.tokenizer.template_hash, right.tokenizer.template_hash),
    ):
        if first is None or second is None:
            return 'unknown', f'{label} identity was not resolved for both runs'
        if first != second:
            return 'different', f'{label} content differs ({first[:12]} != {second[:12]})'
    return None


def compare_effective_identities(left: EffectiveIdentity, right: EffectiveIdentity) -> IdentityComparison:
    """Return whether two executions used the same runtime identity.

    A shared archive path proves nothing: the comparison relies on resolved
    content hashes and the recorded runtime version. A missing fact is
    ``unknown``; equal unknown values are not ``matched``.
    """
    if left.identity_version != right.identity_version:
        return IdentityComparison(
            status='unknown',
            reasons=[f'identity version {left.identity_version} != {right.identity_version}'],
        )
    if left.contract_version != right.contract_version or left.compiler_version != right.compiler_version:
        return IdentityComparison(
            status='unknown',
            reasons=['contract or compiler version differs; the recorded identity is not comparable'],
        )
    tokenizer = _tokenizer_reason(left, right)
    if tokenizer is not None and tokenizer[0] == 'different':
        return IdentityComparison(status='different', reasons=[tokenizer[1]])
    if tokenizer is not None:
        return IdentityComparison(status='unknown', reasons=[tokenizer[1]])
    if left.aiperf_version is None or right.aiperf_version is None:
        return IdentityComparison(
            status='unknown',
            reasons=['the AIPerf version was not recorded for both runs'],
        )
    if left.aiperf_version != right.aiperf_version:
        return IdentityComparison(
            status='different',
            reasons=[f'AIPerf version {left.aiperf_version} != {right.aiperf_version}'],
        )
    return IdentityComparison(
        status='matched',
        reasons=[
            'tokenizer and template content, and AIPerf version, are identical; '
            'deployment facts are declared, not measured',
        ],
    )
