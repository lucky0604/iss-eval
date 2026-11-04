"""Conservative counter, gauge, and histogram window semantics."""

import pytest

from evalscope.perf.results.telemetry import (
    HistogramSample,
    MetricWindow,
    ScalarSample,
    counter_window,
    gauge_window,
    histogram_window,
    merge_histogram_windows,
)


def _scalar(values: list[tuple[int, float]]) -> list[ScalarSample]:
    return [ScalarSample(timestamp_ns=time, value=value) for time, value in values]


def test_counter_requires_baseline_and_rejects_reset() -> None:
    window = MetricWindow(start_ns=10, end_ns=20)
    assert counter_window(_scalar([(11, 5), (20, 9)]), window, 10).value is None
    reset = counter_window(_scalar([(10, 10), (15, 1), (20, 4)]), window, 10)
    assert reset.availability == 'invalid' and reset.value is None
    result = counter_window(_scalar([(9, 10), (15, 13), (21, 20)]), window, 10)
    assert result.value == 10 and result.boundary == 'bracketed'
    assert result.availability == 'partial' and result.source_start_ns == 9 and result.source_end_ns == 21
    exact = counter_window(_scalar([(10, 10), (15, 13), (20, 20)]), window, 10)
    assert exact.value == 10 and exact.availability == 'available' and exact.boundary == 'exact'


def test_counter_gaps_and_invalid_order_are_visible() -> None:
    window = MetricWindow(start_ns=10, end_ns=30)
    result = counter_window(_scalar([(10, 0), (30, 20)]), window, 10)
    assert result.availability == 'partial' and result.reason == 'scrape_gap'
    with pytest.raises(ValueError, match='increasing'):
        counter_window(_scalar([(10, 1), (10, 2), (30, 3)]), window, 10)


def test_gauge_time_weighted_coverage_does_not_fill_long_gaps() -> None:
    window = MetricWindow(start_ns=0, end_ns=20)
    result = gauge_window(_scalar([(0, 2), (5, 4), (18, 8)]), window, 5)
    assert result.availability == 'partial'
    assert result.coverage == pytest.approx(12 / 20)
    assert result.value == pytest.approx((2 * 5 + 4 * 5 + 8 * 2) / 12)
    assert result.sample_peak == 8
    missing = gauge_window(_scalar([(0, 2)]), MetricWindow(start_ns=10, end_ns=20), 5)
    assert missing.value is None and missing.coverage == 0
    boundary = gauge_window(_scalar([(10, 1), (20, 9)]), MetricWindow(start_ns=10, end_ns=20), 10)
    assert boundary.value == 1 and boundary.sample_peak == 9 and boundary.sample_count == 2


def _hist(time: int, first: int, second: int, count: int, total: float) -> HistogramSample:
    return HistogramSample(timestamp_ns=time, buckets={'1': first, '2': second, '+Inf': count}, count=count, sum=total)


def test_histogram_bucket_deltas_and_merged_quantiles() -> None:
    window = MetricWindow(start_ns=10, end_ns=20)
    one = histogram_window([_hist(10, 5, 8, 10, 10), _hist(20, 7, 11, 14, 15)], window, 10, 'seconds', 'queue')
    two = histogram_window([_hist(10, 0, 0, 0, 0), _hist(20, 2, 4, 4, 5)], window, 10, 'seconds', 'queue')
    assert one.count == 4 and one.mean == 1.25
    merged = merge_histogram_windows([one, two])
    assert merged.count == 8 and merged.buckets == {'1': 4, '2': 7, '+Inf': 8}
    assert merged.p50 == pytest.approx(1.0)
    assert merged.p99 is None
    incompatible = histogram_window([_hist(10, 0, 0, 0, 0), _hist(20, 2, 4, 4, 5)], window, 10,
                                    'seconds', 'decode')
    assert merge_histogram_windows([one, incompatible]).availability == 'invalid'


def test_histogram_reset_and_incompatible_buckets_are_not_fabricated() -> None:
    window = MetricWindow(start_ns=10, end_ns=20)
    reset = histogram_window([_hist(10, 5, 8, 10, 10), _hist(20, 1, 2, 3, 4)], window, 10, 'seconds', 'prefill')
    assert reset.availability == 'invalid' and reset.count is None
    changed = HistogramSample(timestamp_ns=20, buckets={'1': 1, '3': 2, '+Inf': 3}, count=3, sum=4)
    result = histogram_window([_hist(10, 0, 0, 0, 0), changed], window, 10, 'seconds', 'prefill')
    assert result.availability == 'invalid' and result.count is None


def test_gauge_mean_avoids_overflow_from_nanosecond_weighting() -> None:
    window = MetricWindow(start_ns=0, end_ns=10)
    large = gauge_window(_scalar([(0, 1e308)]), window, 10)
    assert large.availability == 'available' and large.value == pytest.approx(1e308)
    opposite = gauge_window(_scalar([(0, 1e308), (5, -1e308)]), window, 10)
    assert opposite.availability == 'available' and opposite.value == 0


def test_histogram_merge_rejects_non_finite_aggregate() -> None:
    window = MetricWindow(start_ns=10, end_ns=20)
    before = HistogramSample(timestamp_ns=10, buckets={'1e308': 0, '+Inf': 0}, count=0, sum=0)
    after = HistogramSample(timestamp_ns=20, buckets={'1e308': 1, '+Inf': 1}, count=1, sum=1e308)
    one = histogram_window([before, after], window, 10, 'seconds', 'queue')
    assert one.availability == 'available' and one.sum == 1e308
    merged = merge_histogram_windows([one, one])
    assert merged.availability == 'invalid' and merged.reason == 'non_finite_histogram_sum'
    assert merged.sum is None and merged.mean is None


def test_histogram_quantile_avoids_intermediate_overflow() -> None:
    before = HistogramSample(timestamp_ns=10, buckets={'1e308': 0, '+Inf': 0}, count=0, sum=0)
    after = HistogramSample(timestamp_ns=20, buckets={'1e308': 10, '+Inf': 10}, count=10, sum=1e308)
    result = histogram_window([before, after], MetricWindow(start_ns=10, end_ns=20), 10, 'seconds', 'queue')
    assert result.availability == 'available'
    assert result.p50 == pytest.approx(5e307) and result.p95 == pytest.approx(9.5e307)
    assert result.p99 == pytest.approx(9.9e307)
