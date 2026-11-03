"""Conservative window calculations for raw Prometheus time series."""

import math
from typing import Dict, List, Literal, Optional, Sequence

from pydantic import Field, StrictInt, field_validator, model_validator

from evalscope.utils import BaseArgument


class MetricWindow(BaseArgument):
    """An explicit profiling window in the collector's nanosecond clock."""

    start_ns: StrictInt = Field(ge=0)
    end_ns: StrictInt = Field(gt=0)

    @model_validator(mode='after')
    def validate_order(self) -> 'MetricWindow':
        if self.end_ns <= self.start_ns:
            raise ValueError('Metric window must have positive duration')
        return self


class ScalarSample(BaseArgument):
    """One timestamped counter or gauge observation."""

    timestamp_ns: StrictInt = Field(ge=0)
    value: float = Field(allow_inf_nan=False)

    @field_validator('value', mode='before')
    @classmethod
    def reject_boolean(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError('Metric values must be numeric')
        return value


class WindowStatistic(BaseArgument):
    """A window value with coverage and explicit boundary semantics."""

    availability: Literal['available', 'partial', 'missing', 'invalid']
    value: Optional[float] = None
    sample_peak: Optional[float] = None
    coverage: Optional[float] = Field(default=None, ge=0, le=1)
    sample_count: int = Field(ge=0)
    reason: Optional[str] = None
    source_start_ns: Optional[int] = None
    source_end_ns: Optional[int] = None
    boundary: Literal['exact', 'bracketed', 'step', 'unknown'] = 'unknown'


class HistogramSample(BaseArgument):
    """Cumulative histogram bucket counts, count, and sum at one scrape."""

    timestamp_ns: StrictInt = Field(ge=0)
    buckets: Dict[str, StrictInt]
    count: StrictInt = Field(ge=0)
    sum: float = Field(ge=0, allow_inf_nan=False)


class HistogramWindow(BaseArgument):
    """Window bucket deltas and approximate quantiles, never averaged percentiles."""

    availability: Literal['available', 'partial', 'missing', 'invalid']
    metric_key: str
    unit: str
    window_start_ns: int
    window_end_ns: int
    source_start_ns: Optional[int] = None
    source_end_ns: Optional[int] = None
    buckets: Dict[str, int] = Field(default_factory=dict)
    count: Optional[int] = None
    sum: Optional[float] = None
    mean: Optional[float] = None
    p50: Optional[float] = None
    p95: Optional[float] = None
    p99: Optional[float] = None
    boundary: Literal['exact', 'bracketed', 'unknown'] = 'unknown'
    reason: Optional[str] = None


def _ordered(samples: Sequence[ScalarSample | HistogramSample]) -> None:
    if any(left.timestamp_ns >= right.timestamp_ns for left, right in zip(samples, samples[1:])):
        raise ValueError('Samples must have strictly increasing timestamps')


def _bracket(
    samples: Sequence[ScalarSample | HistogramSample], window: MetricWindow, max_gap_ns: int
) -> Optional[tuple[int, int]]:
    if type(max_gap_ns) is not int or max_gap_ns <= 0:
        raise ValueError('Maximum scrape gap must be a positive integer')
    _ordered(samples)
    before = [i for i, sample in enumerate(samples) if sample.timestamp_ns <= window.start_ns]
    after = [i for i, sample in enumerate(samples) if sample.timestamp_ns >= window.end_ns]
    if not before or not after:
        return None
    first, last = before[-1], after[0]
    if (
        window.start_ns - samples[first].timestamp_ns > max_gap_ns
        or samples[last].timestamp_ns - window.end_ns > max_gap_ns
    ):
        return None
    return first, last


def counter_window(samples: Sequence[ScalarSample], window: MetricWindow, max_gap_ns: int) -> WindowStatistic:
    """Compute a bracketed counter delta without assuming a missing baseline is zero."""
    selected = _bracket(samples, window, max_gap_ns)
    if selected is None:
        return WindowStatistic(availability='missing', sample_count=len(samples), reason='window_not_bracketed')
    first, last = selected
    chosen = samples[first : last + 1]
    if any(sample.value < 0 for sample in chosen) or any(b.value < a.value for a, b in zip(chosen, chosen[1:])):
        return WindowStatistic(availability='invalid', sample_count=len(chosen), reason='counter_reset_or_negative')
    exact = chosen[0].timestamp_ns == window.start_ns and chosen[-1].timestamp_ns == window.end_ns
    gap = any(b.timestamp_ns - a.timestamp_ns > max_gap_ns for a, b in zip(chosen, chosen[1:]))
    reason = 'scrape_gap' if gap else ('bracket_includes_outside_window' if not exact else None)
    return WindowStatistic(
        availability='partial' if reason else 'available',
        value=chosen[-1].value - chosen[0].value,
        sample_count=len(chosen),
        reason=reason,
        source_start_ns=chosen[0].timestamp_ns,
        source_end_ns=chosen[-1].timestamp_ns,
        boundary='exact' if exact else 'bracketed',
    )


def gauge_window(samples: Sequence[ScalarSample], window: MetricWindow, max_gap_ns: int) -> WindowStatistic:
    """Compute a time-weighted gauge mean, truncating stale step intervals."""
    if type(max_gap_ns) is not int or max_gap_ns <= 0:
        raise ValueError('Maximum scrape gap must be a positive integer')
    _ordered(samples)
    duration = window.end_ns - window.start_ns
    covered = 0
    integral = 0.0
    values = []
    for index, sample in enumerate(samples):
        next_time = samples[index + 1].timestamp_ns if index + 1 < len(samples) else window.end_ns
        left = max(window.start_ns, sample.timestamp_ns)
        right = min(window.end_ns, next_time, sample.timestamp_ns + max_gap_ns)
        if right <= left:
            continue
        interval = right - left
        covered += interval
        integral += sample.value * interval
        values.append(sample.value)
    boundary_values = [sample.value for sample in samples if sample.timestamp_ns == window.end_ns]
    values.extend(boundary_values)
    if not covered:
        return WindowStatistic(
            availability='missing',
            sample_peak=max(values) if values else None,
            sample_count=len(values),
            coverage=0,
            reason='no_covered_interval',
            boundary='step',
        )
    coverage = covered / duration
    return WindowStatistic(
        availability='available' if covered == duration else 'partial',
        value=integral / covered,
        sample_peak=max(values),
        coverage=coverage,
        sample_count=len(values),
        reason=None if covered == duration else 'scrape_gap',
        boundary='step',
    )


def _bucket_bounds(buckets: Dict[str, int]) -> List[tuple[float, str, int]]:
    parsed = []
    for key, value in buckets.items():
        try:
            boundary = float(key)
        except ValueError:
            raise ValueError('Invalid histogram bucket boundary') from None
        if math.isnan(boundary) or boundary < 0 or value < 0:
            raise ValueError('Invalid histogram bucket')
        parsed.append((boundary, key, value))
    parsed.sort()
    if not parsed or parsed[-1][0] != math.inf or len({value[0] for value in parsed}) != len(parsed):
        raise ValueError('Histogram requires unique boundaries including +Inf')
    if any(left[2] > right[2] for left, right in zip(parsed, parsed[1:])):
        raise ValueError('Histogram buckets are not cumulative')
    return parsed


def _quantile(buckets: Dict[str, int], count: int, fraction: float) -> Optional[float]:
    if not count:
        return None
    target = count * fraction
    previous_bound, previous_count = 0.0, 0
    for boundary, _, cumulative in _bucket_bounds(buckets):
        if cumulative >= target:
            if math.isinf(boundary):
                return None
            if cumulative == previous_count:
                return boundary
            return previous_bound + (boundary - previous_bound) * (target - previous_count) / (
                cumulative - previous_count
            )
        previous_bound, previous_count = boundary, cumulative
    return None


def histogram_window(
    samples: Sequence[HistogramSample], window: MetricWindow, max_gap_ns: int, unit: str, metric_key: str
) -> HistogramWindow:
    """Compute histogram deltas and approximate quantiles from compatible buckets."""
    selected = _bracket(samples, window, max_gap_ns)
    identity = {
        'unit': unit,
        'metric_key': metric_key,
        'window_start_ns': window.start_ns,
        'window_end_ns': window.end_ns,
    }
    if selected is None:
        return HistogramWindow(availability='missing', reason='window_not_bracketed', **identity)
    first, last = selected
    chosen = samples[first : last + 1]
    try:
        shapes = []
        for sample in chosen:
            buckets = _bucket_bounds(sample.buckets)
            if buckets[-1][2] != sample.count:
                raise ValueError('Histogram +Inf count does not match count')
            shapes.append(tuple(item[1] for item in buckets))
        if len(set(shapes)) != 1:
            raise ValueError('Histogram bucket boundaries changed')
        if any(
            b.count < a.count or b.sum < a.sum or any(b.buckets[k] < a.buckets[k] for k in a.buckets)
            for a, b in zip(chosen, chosen[1:])
        ):
            raise ValueError('Histogram reset')
        buckets = {key: chosen[-1].buckets[key] - chosen[0].buckets[key] for key in shapes[0]}
        count = chosen[-1].count - chosen[0].count
        total = chosen[-1].sum - chosen[0].sum
        if _bucket_bounds(buckets)[-1][2] != count:
            raise ValueError('Histogram delta count does not match +Inf')
    except ValueError as error:
        return HistogramWindow(availability='invalid', reason=str(error), **identity)
    exact = chosen[0].timestamp_ns == window.start_ns and chosen[-1].timestamp_ns == window.end_ns
    gap = any(b.timestamp_ns - a.timestamp_ns > max_gap_ns for a, b in zip(chosen, chosen[1:]))
    reason = 'scrape_gap' if gap else ('bracket_includes_outside_window' if not exact else None)
    return HistogramWindow(
        availability='partial' if reason else 'available',
        buckets=buckets,
        count=count,
        sum=total,
        mean=total / count if count else None,
        p50=_quantile(buckets, count, 0.5),
        p95=_quantile(buckets, count, 0.95),
        p99=_quantile(buckets, count, 0.99),
        boundary='exact' if exact else 'bracketed',
        reason=reason,
        source_start_ns=chosen[0].timestamp_ns,
        source_end_ns=chosen[-1].timestamp_ns,
        **identity,
    )


def merge_histogram_windows(windows: Sequence[HistogramWindow]) -> HistogramWindow:
    """Merge matching node buckets, then calculate quantiles from pooled counts."""
    if not windows:
        raise ValueError('At least one histogram is required')
    first = windows[0]
    identity = {
        'unit': first.unit,
        'metric_key': first.metric_key,
        'window_start_ns': first.window_start_ns,
        'window_end_ns': first.window_end_ns,
    }
    compatible = all(
        item.availability in ('available', 'partial')
        and item.count is not None
        and item.sum is not None
        and item.metric_key == first.metric_key
        and item.unit == first.unit
        and item.window_start_ns == first.window_start_ns
        and item.window_end_ns == first.window_end_ns
        and item.source_start_ns == first.source_start_ns
        and item.source_end_ns == first.source_end_ns
        and set(item.buckets) == set(first.buckets)
        for item in windows
    )
    if not compatible:
        return HistogramWindow(availability='invalid', reason='incompatible_histogram_series', **identity)
    buckets = {key: sum(item.buckets[key] for item in windows) for key in first.buckets}
    count = sum(item.count for item in windows if item.count is not None)
    total = sum(item.sum for item in windows if item.sum is not None)
    return HistogramWindow(
        availability='partial' if any(item.availability == 'partial' for item in windows) else 'available',
        buckets=buckets,
        count=count,
        sum=total,
        mean=total / count if count else None,
        p50=_quantile(buckets, count, 0.5),
        p95=_quantile(buckets, count, 0.95),
        p99=_quantile(buckets, count, 0.99),
        boundary=first.boundary,
        source_start_ns=first.source_start_ns,
        source_end_ns=first.source_end_ns,
        reason='one_or_more_series_partial' if any(item.availability == 'partial' for item in windows) else None,
        **identity,
    )
