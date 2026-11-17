"""Gather the fixed snapshot data an export renders, with no page limits.

Everything a report shows comes from one :class:`ReportSnapshot`: the same
runs, the same percentiles, the same request index the Web app reads. Exports
never recompute a percentile and never take the current table page as the
whole set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from evalscope.perf.results.report import ReportChartSeries, ReportHeader, ReportRequestRow, ReportRun
from evalscope.perf.results.table import run_record
from evalscope.utils.logger import get_logger

from ..perf_reports import PerfReportError, ReportSnapshot

logger = get_logger()

CHART_SPECS: Tuple[Tuple[str, str], ...] = (
    ('ttft', 'p95'),
    ('tpot', 'p95'),
    ('latency', 'p95'),
    ('input_tps', 'mean'),
    ('output_tps', 'mean'),
    ('rpm', 'mean'),
)
TOP_REQUEST_LIMIT = 20


@dataclass
class ExportDataset:
    """All report content, fixed at render time from one snapshot."""

    header: ReportHeader
    runs: List[ReportRun]
    charts: List[Tuple[str, str, List[ReportChartSeries]]] = field(default_factory=list)
    top_requests: List[Tuple[str, ReportRequestRow]] = field(default_factory=list)
    request_total: int = 0
    failed_runs: int = 0

    @property
    def records(self) -> List[Dict[str, Optional[object]]]:
        return [run_record(run) for run in self.runs]

    def run(self, run_key: str) -> Optional[ReportRun]:
        return next((run for run in self.runs if run.run_key == run_key), None)


def _top_requests(snapshot: ReportSnapshot, runs: List[ReportRun]) -> List[Tuple[str, ReportRequestRow]]:
    ranked: List[Tuple[str, ReportRequestRow]] = []
    for run in runs:
        if run.run_key not in snapshot.normalized_dirs:
            continue
        try:
            rows = snapshot.top_requests(run_key=run.run_key, metric='latency', limit=TOP_REQUEST_LIMIT)
        except PerfReportError:
            continue
        ranked.extend((run.run_id, row) for row in rows)
    ranked.sort(key=lambda item: (item[1].latency_seconds is None, -(item[1].latency_seconds or 0.0), item[1].index))
    return ranked[:TOP_REQUEST_LIMIT]


def _charts(snapshot: ReportSnapshot, runs: List[ReportRun]) -> List[Tuple[str, str, List[ReportChartSeries]]]:
    """The same six charts, reduced to the group the export was asked for."""
    selected = {run.run_id for run in runs}
    charts: List[Tuple[str, str, List[ReportChartSeries]]] = []
    for metric, statistic in CHART_SPECS:
        series = [
            item.model_copy(update={'points': points})
            for item in snapshot.charts(metric=metric, statistic=statistic)
            if (points := [point for point in item.points if point.run_id in selected])
        ]
        charts.append((metric, statistic, series))
    return charts


def collect(snapshot: ReportSnapshot, *, group: Optional[str] = None) -> ExportDataset:
    runs = snapshot.selected_runs(group=group)
    header = snapshot.header()
    return ExportDataset(
        header=header,
        runs=runs,
        charts=_charts(snapshot, runs),
        top_requests=_top_requests(snapshot, runs),
        request_total=sum(run.counts.total for run in runs),
        failed_runs=sum(1 for run in runs if run.availability != 'available'),
    )


def request_rows(
    snapshot: ReportSnapshot,
    *,
    group: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    run_key: Optional[str] = None,
    phase: Optional[str] = 'profiling',
) -> Tuple[int, Iterator[Tuple[ReportRun, ReportRequestRow]]]:
    """Iterate every matching request of every selected run.

    Returns the number of runs that expose a request store plus a generator;
    callers stream it straight into a file rather than materializing 10^5 rows.
    """
    runs = [
        run
        for run in snapshot.selected_runs(group=group)
        if run.run_key in snapshot.normalized_dirs and (run_key is None or run.run_key == run_key)
    ]

    def iterate():
        for run in runs:
            for row in snapshot.iter_requests(
                run_key=run.run_key,
                status=status,
                phase=None if phase in (None, 'all') else phase,
                search=search,
                sort='index',
                order='asc',
            ):
                yield run, row

    return len(runs), iterate()
