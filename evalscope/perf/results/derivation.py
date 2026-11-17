"""Shared, traceable derivations for per-request timing values.

Only fallbacks that stay within the upstream measurement boundaries live here so
that the importer, the archive adapters and the report layer cannot drift into
different TPOT definitions.
"""

from dataclasses import dataclass
from typing import Literal, Optional

from .models import PerfRequestRecord

ITL_DERIVATION_METHOD = 'latency_minus_ttft_over_output_sequence_length_minus_one-v1'


@dataclass(frozen=True)
class ResolvedItl:
    """A per-request inter-token latency with its provenance."""

    seconds: Optional[float]
    source: Optional[Literal['aiperf_raw', 'derived']]
    method: Optional[str]
    reason: Optional[str] = None


def derive_itl_seconds(
    *,
    latency_seconds: Optional[float],
    ttft_seconds: Optional[float],
    output_sequence_tokens: Optional[int],
) -> ResolvedItl:
    """Derive TPOT as (latency - ttft) / (OSL - 1) for a single successful request."""
    if latency_seconds is None:
        return ResolvedItl(None, None, None, 'request_latency_missing')
    if ttft_seconds is None:
        return ResolvedItl(None, None, None, 'ttft_missing')
    if output_sequence_tokens is None:
        return ResolvedItl(None, None, None, 'output_sequence_length_missing')
    if output_sequence_tokens <= 1:
        return ResolvedItl(None, None, None, 'output_sequence_length_too_small')
    if latency_seconds < ttft_seconds:
        return ResolvedItl(None, None, None, 'latency_below_ttft')
    return ResolvedItl(
        (latency_seconds - ttft_seconds) / (output_sequence_tokens - 1),
        'derived',
        ITL_DERIVATION_METHOD,
    )


def resolve_itl(record: PerfRequestRecord) -> ResolvedItl:
    """Prefer the raw AIPerf inter-token latency, then a documented derivation."""
    if record.itl_seconds is not None:
        return ResolvedItl(record.itl_seconds, record.itl_source or 'aiperf_raw', record.itl_method, None)
    if record.status != 'success':
        return ResolvedItl(None, None, None, 'request_not_successful')
    output_sequence_tokens = record.aiperf_output_sequence_tokens
    if output_sequence_tokens is None and record.output_tokens_source == 'aiperf_estimate':
        output_sequence_tokens = record.output_tokens
    return derive_itl_seconds(
        latency_seconds=record.latency_seconds,
        ttft_seconds=record.ttft_seconds,
        output_sequence_tokens=output_sequence_tokens,
    )
