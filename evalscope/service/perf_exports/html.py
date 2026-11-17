"""Standalone HTML report rendered from a fixed snapshot.

The document is deliberately self-contained: inline CSS, inline SVG and no
scripts or remote resources. It is what the PDF renderer prints and what a user
can archive and open offline years later. No value is recomputed here -- every
cell comes from the snapshot through the shared column dictionary.
"""

from __future__ import annotations

from html import escape
from typing import Dict, List, Optional, Sequence, Tuple

from evalscope.perf.results.report import ReportChartSeries, ReportRequestRow, ReportRun, group_key
from evalscope.perf.results.table import run_record

from .collect import ExportDataset
from .models import EXPORT_TEMPLATE_VERSION, ExportSpec
from .text import fill, text

PALETTE = ('#4e79a7', '#f28e2b', '#59a14f', '#e15759', '#76b7b2', '#edc948', '#b07aa1', '#9c755f')

CHART_TITLES = {
    'ttft': 'chart_ttft',
    'tpot': 'chart_tpot',
    'latency': 'chart_e2e',
    'input_tps': 'chart_input_tps',
    'output_tps': 'chart_output_tps',
    'rpm': 'chart_rpm',
}
CHART_UNITS = {
    'ttft': 'ms',
    'tpot': 'ms/token',
    'latency': 's',
    'input_tps': 'tok/s',
    'output_tps': 'tok/s',
    'rpm': 'rpm',
}
CHART_SCALE = {'ttft': 1000.0, 'tpot': 1000.0, 'latency': 1.0, 'input_tps': 1.0, 'output_tps': 1.0, 'rpm': 1.0}

STYLE = """
@page { size: A3 landscape; margin: 9mm 8mm 15mm 8mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; background: #fff; color: #16181d;
  font-family: "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei",
    -apple-system, "Segoe UI", sans-serif; font-size: 9px; line-height: 1.45; }
main { padding: 14px 16px 0; max-width: 1680px; margin: 0 auto; }
h1 { font-size: 19px; margin: 0 0 2px; }
h2 { font-size: 13px; margin: 14px 0 5px; padding-bottom: 3px; border-bottom: 1px solid #d6d8dd; break-after: avoid; }
.dictionary-section { break-inside: avoid; }
.subtitle { color: #555c66; margin: 0 0 10px; }
table { border-collapse: collapse; width: 100%; }
.meta td, .meta th { text-align: left; padding: 2px 8px 2px 0; vertical-align: top; white-space: nowrap; }
.meta th { color: #555c66; font-weight: 500; }
.counts { font-size: 11px; margin: 6px 0 2px; }
.note { color: #555c66; margin: 2px 0 0; }
.badges span { display: inline-block; border: 1px solid #c9ccd2; border-radius: 8px; padding: 0 6px; margin-left: 6px;
  font-size: 8px; color: #444; }
.matrix th, .matrix td { border: 0.6px solid #b9bcc3; padding: 2px 4px; }
.matrix thead { display: table-header-group; }
.matrix tr { break-inside: avoid; page-break-inside: avoid; }
.matrix thead th { background: #eceef2; font-weight: 600; text-align: center; }
.matrix thead th.band { background: #dfe3ea; }
.matrix td { white-space: nowrap; }
.matrix td.num { text-align: right; font-variant-numeric: tabular-nums; }
.matrix td.dim { background: #fafbfc; }
.matrix tr.group-row th { text-align: left; background: #f1f3f6; font-weight: 600;
  break-after: avoid; page-break-after: avoid; }
.matrix td.error { color: #a4262c; }
.chart-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 12px; }
.chart { border: 0.6px solid #d6d8dd; border-radius: 3px; padding: 6px; break-inside: avoid; }
.chart h3 { font-size: 9px; margin: 0 0 2px; }
.chart .empty { color: #555c66; padding: 24px 0; text-align: center; }
.chart svg { display: block; width: 100%; height: auto; aspect-ratio: 500 / 210; }
.legend { display: flex; flex-wrap: wrap; gap: 2px 8px; margin-top: 2px; color: #444; font-size: 7.5px; }
.legend i { display: inline-block; width: 10px; height: 2.5px; margin-right: 3px; vertical-align: middle; }
.footer { color: #666; margin-top: 8px; border-top: 1px solid #d6d8dd; padding-top: 4px; }
@media print { .footer { display: none; } }
.dictionary td, .dictionary th { border: 0.6px solid #b9bcc3; padding: 2px 5px; vertical-align: top; }
.dictionary th { background: #eceef2; text-align: left; }
.dictionary td.field { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; }
"""


def _fmt(value: Optional[object], digits: int = 2) -> str:
    if value is None:
        return '—'
    if isinstance(value, (int, float)):
        return f'{value:.{digits}f}'
    return escape(str(value))


def _group_label(run: ReportRun, t: Dict[str, str], locale: str) -> str:
    dimensions = run.dimensions
    workload = dimensions.workload or t['unknown']
    ratio = '—' if dimensions.target_ratio is None else f'{round(dimensions.target_ratio * 100)}%'
    osl = dimensions.output_token_limit
    mode = t['mode_open'] if dimensions.load_mode == 'open_loop' else t['mode_closed']
    if locale == 'zh':
        return f'{workload} · 前缀 {ratio} · 输出上限 {osl if osl is not None else "未知"} · {"开环" if dimensions.load_mode == "open_loop" else "闭环"}'
    return f'{workload} · {ratio} prefix · OSL {osl if osl is not None else "unknown"} · {mode}'


def _load_caption(run: ReportRun, t: Dict[str, str]) -> str:
    dimensions = run.dimensions
    if dimensions.load_mode == 'open_loop':
        return fill(t['group_open'], value=dimensions.rate if dimensions.rate is not None else '—')
    return fill(t['group_closed'], value=dimensions.concurrency if dimensions.concurrency is not None else '—')


def _lifecycle(run: ReportRun, t: Dict[str, str]) -> str:
    return t.get(f'status_{run.lifecycle}', t['status_unknown'])


def _facet_label(series_set: Sequence[ReportChartSeries], locale: str) -> str:
    """The scenario a chart compares: deployment, workload, structure, ISL, OSL and prefix ratio."""
    dimensions = series_set[0].facet_dimensions if series_set else None
    if dimensions is None:
        return series_set[0].facet if series_set else '—'
    unknown = '未知' if locale == 'zh' else 'unknown'
    isl = '—' if dimensions.input_tokens is None else str(dimensions.input_tokens)
    osl = '—' if dimensions.output_token_limit is None else str(dimensions.output_token_limit)
    ratio = '—' if dimensions.target_ratio is None else f'{round(dimensions.target_ratio * 100)}%'
    parts = [dimensions.deployment_mode, dimensions.workload or unknown, dimensions.message_structure]
    if locale == 'zh':
        parts.extend([f'输入 {isl}', f'输出上限 {osl}', f'前缀 {ratio}'])
    else:
        parts.extend([f'ISL {isl}', f'OSL {osl}', f'{ratio} prefix'])
    return ' · '.join(str(part) for part in parts if part)


def _series_label(series: ReportChartSeries, locale: str) -> str:
    if locale == 'zh':
        return '开环' if series.load_mode == 'open_loop' else '闭环'
    return 'open-loop' if series.load_mode == 'open_loop' else 'closed-loop'


def _series_label_with_repeat(series: ReportChartSeries, repetitions: int, locale: str) -> str:
    label = _series_label(series, locale)
    if repetitions > 1 and series.repetition is not None:
        repeat = '第 ' if locale == 'zh' else 'repeat '
        label = f'{label} · {repeat}{series.repetition + 1}'
    return label


def _x_value(series: ReportChartSeries, point_index: int) -> float:
    dimensions = series.points[point_index].dimensions
    if dimensions.load_mode == 'open_loop' and dimensions.rate is not None:
        return float(dimensions.rate)
    if dimensions.concurrency is not None:
        return float(dimensions.concurrency)
    if dimensions.rate is not None:
        return float(dimensions.rate)
    return float(series.points[point_index].order)


def _ticks(low: float, high: float, count: int = 4) -> List[float]:
    if high <= low:
        high = low + 1.0
    step = (high - low) / (count - 1)
    return [low + step * index for index in range(count)]


def _chart_svg(metric: str, series_set: Sequence[ReportChartSeries], t: Dict[str, str], locale: str) -> str:
    """One chart per facet, so only equivalent scenarios share an axis.

    A facet fixes deployment, workload, structure, ISL, OSL and prefix ratio;
    within it, closed- and open-loop series stay separate and every repetition
    effort is its own line instead of being joined into one curve.
    """
    groups: Dict[str, List[ReportChartSeries]] = {}
    for item in series_set:
        groups.setdefault(item.facet, []).append(item)
    return ''.join(_facet_chart_svg(metric, groups[key], t, locale) for key in sorted(groups))


def _facet_chart_svg(metric: str, series_set: Sequence[ReportChartSeries], t: Dict[str, str], locale: str) -> str:
    width, height = 500, 210
    left, right, top, bottom = 48, 10, 10, 34
    plot_width = width - left - right
    plot_height = height - top - bottom
    title = f'{t[CHART_TITLES[metric]]} · {_facet_label(series_set, locale)}'
    points: List[Tuple[float, float, str, str]] = []
    repetitions = len({item.repetition for item in series_set if item.repetition is not None}) or 1
    # (color, label, mode, contiguous measured segments)
    drawn: List[Tuple[str, str, str, List[List[Tuple[float, float]]]]] = []
    for series_index, item in enumerate(series_set):
        # One facet per chart, so colour separates load mode and repetition,
        # the dimensions still comparable inside it.
        color = PALETTE[series_index % len(PALETTE)]
        order = sorted(range(len(item.points)), key=lambda index: _x_value(item, index))
        segments: List[List[Tuple[float, float]]] = []
        current: List[Tuple[float, float]] = []
        for index in order:
            point = item.points[index]
            if point.value is None or point.availability != 'available':
                # An unmeasured point ends the segment: the gap is never drawn
                # as a measured straight line between its neighbours.
                if current:
                    segments.append(current)
                    current = []
                continue
            x = _x_value(item, index)
            y = point.value * CHART_SCALE[metric]
            current.append((x, y))
            points.append((x, y, color, f'{point.run_id}: {y:.3f} {CHART_UNITS[metric]}'))
        if current:
            segments.append(current)
        drawn.append((color, _series_label_with_repeat(item, repetitions, locale), item.load_mode or '', segments))
    if not points:
        return f'<div class="chart"><h3>{escape(title)}</h3><p class="empty">{escape(t["chart_empty"])}</p></div>'

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_low, x_high = min(xs), max(xs)
    if x_high <= x_low:
        x_high = x_low + 1.0
    y_low = 0.0 if min(ys) >= 0 else min(ys)
    y_high = max(ys) if max(ys) > 0 else 1.0
    y_high *= 1.08

    def sx(value: float) -> float:
        return left + (value - x_low) / (x_high - x_low) * plot_width

    def sy(value: float) -> float:
        return top + plot_height - (value - y_low) / (y_high - y_low) * plot_height

    parts = [
        f'<svg role="img" aria-label="{escape(title)}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
    ]
    for value in _ticks(y_low, y_high):
        y = sy(value)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#e3e5e9"/>')
        parts.append(
            f'<text x="{left - 4}" y="{y + 3:.1f}" text-anchor="end" font-size="8" fill="#555c66">{value:.4g}</text>'
        )
    for value in _ticks(x_low, x_high):
        x = sx(value)
        parts.append(
            f'<text x="{x:.1f}" y="{top + plot_height + 12}" text-anchor="middle" font-size="8" fill="#555c66">'
            f'{value:.4g}</text>'
        )
    parts.append(
        f'<line x1="{left}" y1="{top + plot_height}" x2="{width - right}" y2="{top + plot_height}" stroke="#9aa0a8"/>'
    )
    parts.append(
        f'<text x="{left + plot_width / 2:.1f}" y="{height - 4}" text-anchor="middle" font-size="8" fill="#555c66">'
        f'{"C / req/s" if len({item.load_mode for item in series_set}) > 1 else ("req/s" if series_set and series_set[0].load_mode == "open_loop" else "C")}'
        f'</text>'
    )
    parts.append(
        f'<text transform="translate(10 {top + plot_height / 2:.1f}) rotate(-90)" text-anchor="middle" '
        f'font-size="8" fill="#555c66">{escape(CHART_UNITS[metric])}</text>'
    )
    for color, _label, _mode, segments in drawn:
        for segment in segments:
            if len(segment) >= 2:
                path = ' '.join(
                    f'{"M" if index == 0 else "L"}{sx(x):.1f},{sy(y):.1f}' for index, (x, y) in enumerate(segment)
                )
                parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.4"/>')
            for x, y in segment:
                parts.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="2.6" fill="{color}"/>')
    parts.append('</svg>')
    legend = ''.join(
        f'<span><i style="background:{color}"></i>{escape(label)}</span>' for color, label, _mode, _segments in drawn
    )
    return (
        f'<div class="chart"><h3>{escape(title)} · {escape(CHART_UNITS[metric])}</h3>'
        f'{"".join(parts)}<div class="legend">{legend}</div></div>'
    )


def _wide_table(dataset: ExportDataset, t: Dict[str, str], locale: str) -> str:
    header = (
        '<thead><tr>'
        f'<th rowspan="2" scope="col">{escape(t["tier_isl"])}</th>'
        f'<th rowspan="2" scope="col">{escape(t["tier_load"])}</th>'
        f'<th rowspan="2" scope="col">{escape(t["tier_repeat"])}</th>'
        f'<th class="band" colspan="4" scope="colgroup">{escape(t["band_ttft"])}</th>'
        f'<th class="band" colspan="4" scope="colgroup">{escape(t["band_tpot"])}</th>'
        f'<th class="band" colspan="2" scope="colgroup">{escape(t["band_e2e"])}</th>'
        f'<th class="band" colspan="3" scope="colgroup">{escape(t["band_throughput"])}</th>'
        f'<th class="band" colspan="2" scope="colgroup">{escape(t["band_result"])}</th>'
        '</tr><tr>'
        + ''.join(f'<th scope="col">{escape(t[f"col_{stat}"])}</th>' for stat in ('mean', 'p90', 'p95', 'p99'))
        + ''.join(f'<th scope="col">{escape(t[f"col_{stat}"])}</th>' for stat in ('mean', 'p90', 'p95', 'p99'))
        + ''.join(f'<th scope="col">{escape(t[f"col_{stat}"])}</th>' for stat in ('mean', 'p99'))
        + f'<th scope="col">{escape(t["col_input_tps"])}</th><th scope="col">{escape(t["col_output_tps"])}</th>'
        + f'<th scope="col">{escape(t["col_rpm"])}</th>'
        + f'<th scope="col">{escape(t["col_success"])}</th><th scope="col">{escape(t["col_status"])}</th>'
        + '</tr></thead>'
    )
    rows: List[str] = []
    current_group: Optional[str] = None
    for run in dataset.runs:
        key = group_key(run.dimensions)
        if key != current_group:
            current_group = key
            rows.append(
                f'<tr class="group-row"><th colspan="18" scope="colgroup">{escape(_group_label(run, t, locale))}</th></tr>'
            )
        values = run_record(run)
        status_class = ' class="error"' if run.lifecycle == 'error' else ''
        rows.append(
            '<tr>'
            f'<td class="num dim">{run.dimensions.input_tokens if run.dimensions.input_tokens is not None else "—"}</td>'
            f'<td class="dim">{escape(_load_caption(run, t))}</td>'
            f'<td class="num dim">{run.dimensions.repetition if run.dimensions.repetition is not None else "—"}</td>'
            + ''.join(
                f'<td class="num">{_fmt(values[f"ttft_{stat}_ms"])}</td>' for stat in ('avg', 'p90', 'p95', 'p99')
            )
            + ''.join(
                f'<td class="num">{_fmt(values[f"tpot_{stat}_ms"])}</td>' for stat in ('avg', 'p90', 'p95', 'p99')
            )
            + f'<td class="num">{_fmt(values["e2e_avg_s"], 3)}</td><td class="num">{_fmt(values["e2e_p99_s"], 3)}</td>'
            + f'<td class="num">{_fmt(values["input_tps"])}</td><td class="num">{_fmt(values["output_tps"])}</td>'
            + f'<td class="num">{_fmt(values["rpm"])}</td>'
            + f'<td class="num">{values["success_requests"] if values["success_requests"] is not None else "—"}'
            f' / {values["total_requests"] if values["total_requests"] is not None else "—"}</td>'
            + f'<td{status_class}>{escape(_lifecycle(run, t))}</td>'
            '</tr>'
        )
    return f'<table class="matrix">{header}<tbody>{"".join(rows)}</tbody></table>'


def _top_requests_table(dataset: ExportDataset, t: Dict[str, str]) -> str:
    if not dataset.top_requests:
        return ''
    rows = []
    for run_id, row in dataset.top_requests:
        rows.append(
            '<tr>'
            f'<td>{escape(run_id)}</td>'
            f'<td class="num">{row.index}</td>'
            f'<td>{escape(row.status)}</td>'
            f'<td class="num">{row.prompt_tokens if row.prompt_tokens is not None else "—"}</td>'
            f'<td class="num">{row.output_tokens if row.output_tokens is not None else "—"}</td>'
            f'<td class="num">{row.cached_tokens if row.cached_tokens is not None else "—"}</td>'
            f'<td class="num">{_fmt(None if row.ttft_seconds is None else row.ttft_seconds * 1000)}</td>'
            f'<td class="num">{_fmt(None if row.tpot_seconds is None else row.tpot_seconds * 1000)}</td>'
            f'<td class="num">{_fmt(row.latency_seconds, 3)}</td>'
            '</tr>'
        )
    head = (
        '<thead><tr>'
        + ''.join(
            f'<th scope="col">{escape(t[key])}</th>'
            for key in (
                'request_run',
                'request_index',
                'request_status',
                'request_prompt',
                'request_output',
                'request_cached',
                'request_ttft',
                'request_tpot',
                'request_e2e',
            )
        )
        + '</tr></thead>'
    )
    return f'<table class="matrix">{head}<tbody>{"".join(rows)}</tbody></table>'


def _nodes_section(dataset: ExportDataset, t: Dict[str, str]) -> str:
    """The persisted node telemetry of each run, as numeric cells."""
    metric_rows: List[str] = []
    cache_rows: List[str] = []
    for run in dataset.runs:
        nodes = run.nodes
        if nodes is None:
            continue
        if not nodes.metrics:
            metric_rows.append(
                '<tr>'
                f'<td>{escape(run.run_id)}</td>'
                + f'<td>{escape(nodes.availability)}</td>'
                + '<td>—</td>' * 11
                + f'<td>{escape(nodes.reason or t["nodes_empty_run"])}</td>'
                '</tr>'
            )
        for metric in nodes.metrics:
            statistic = metric.statistic
            histogram = metric.histogram
            availability = (
                statistic.availability
                if statistic is not None
                else (histogram.availability if histogram else 'missing')
            )
            reason = metric.reason or (
                statistic.reason
                if statistic is not None and statistic.reason
                else (histogram.reason if histogram else None)
            )
            labels = ', '.join(f'{key}={value}' for key, value in metric.labels.items())
            metric_rows.append(
                '<tr>'
                f'<td>{escape(run.run_id)}</td>'
                f'<td>{escape(metric.node_id)}</td>'
                f'<td>{escape(metric.role)}</td>'
                f'<td>{escape(metric.metric_name)}</td>'
                f'<td>{escape(labels) if labels else "—"}</td>'
                f'<td class="num">{_fmt(statistic.value if statistic is not None else (histogram.mean if histogram else None), 4)}</td>'
                f'<td class="num">{_fmt(statistic.sample_peak if statistic is not None else None, 4)}</td>'
                f'<td class="num">{statistic.sample_count if statistic is not None and statistic.sample_count is not None else (histogram.count if histogram else "—")}</td>'
                f'<td class="num">{_fmt(statistic.coverage if statistic is not None else None, 4)}</td>'
                f'<td>{escape(statistic.boundary if statistic is not None and statistic.boundary else (histogram.boundary if histogram and histogram.boundary else "—"))}</td>'
                f'<td>{escape(metric.metric_unit or (histogram.unit if histogram else "") or "—")}</td>'
                f'<td>{escape(availability)}</td>'
                f'<td>{escape(reason or "—")}</td>'
                '</tr>'
            )
        for ratio in nodes.cache_ratios:
            cache_rows.append(
                '<tr>'
                f'<td>{escape(run.run_id)}</td>'
                f'<td>{escape(ratio.node_id)}</td>'
                f'<td>{escape(ratio.role)}</td>'
                f'<td class="num">{_fmt(ratio.hit_tokens, 0)}</td>'
                f'<td class="num">{_fmt(ratio.lookup_tokens, 0)}</td>'
                f'<td class="num">{_fmt(None if ratio.ratio is None else ratio.ratio * 100, 2)}</td>'
                f'<td>{escape(ratio.semantics or "—")}</td>'
                f'<td>{escape(ratio.availability)}</td>'
                f'<td>{escape(ratio.reason or "—")}</td>'
                '</tr>'
            )
    capability = dataset.header.capabilities.node_metrics
    if not metric_rows and not cache_rows:
        reason = capability.reason or t['nodes_unavailable']
        return f'<h2>{escape(t["nodes_title"])}</h2><p class="note">{escape(reason)}</p>'
    metric_head = (
        '<thead><tr>'
        + ''.join(
            f'<th scope="col">{escape(t[key])}</th>'
            for key in (
                'node_run',
                'node_node',
                'node_role',
                'node_metric',
                'node_labels',
                'node_mean',
                'node_peak',
                'node_samples',
                'node_coverage',
                'node_boundary',
                'node_unit',
                'node_availability',
                'node_reason',
            )
        )
        + '</tr></thead>'
    )
    parts = [
        f'<h2>{escape(t["nodes_title"])}</h2>',
        f'<p class="note">{escape(t["nodes_note"])}</p>',
    ]
    if metric_rows:
        parts.append(f'<table class="matrix">{metric_head}<tbody>{"".join(metric_rows)}</tbody></table>')
    if cache_rows:
        cache_head = (
            '<thead><tr>'
            + ''.join(
                f'<th scope="col">{escape(t[key])}</th>'
                for key in (
                    'node_run',
                    'node_node',
                    'node_role',
                    'node_cache_hit',
                    'node_cache_lookup',
                    'node_cache_ratio',
                    'node_cache_semantics',
                    'node_availability',
                    'node_reason',
                )
            )
            + '</tr></thead>'
        )
        parts.append(f'<h3>{escape(t["node_cache_title"])}</h3>')
        parts.append(f'<p class="note">{escape(t["node_cache_note"])}</p>')
        parts.append(f'<table class="matrix">{cache_head}<tbody>{"".join(cache_rows)}</tbody></table>')
    return ''.join(parts)


def _meta_table(dataset: ExportDataset, spec: ExportSpec, t: Dict[str, str], generated_at: str) -> str:
    header = dataset.header
    rows: List[Tuple[str, str]] = [
        (t['meta_snapshot'], header.snapshot_id),
        (t['meta_generated'], generated_at),
        (t['meta_source'], header.source_kind),
    ]
    if header.model:
        rows.append((t['meta_model'], header.model))
    if header.phase:
        rows.append((t['meta_phase'], header.phase))
    rows.extend(
        [
            (t['meta_statistics'], header.statistics_version),
            (t['meta_template'], EXPORT_TEMPLATE_VERSION),
            (t['meta_filter'], spec.group or t['all_groups']),
            (t['meta_locale'], t['language']),
        ]
    )
    cells = ''.join(
        f'<tr><th scope="row">{escape(label)}</th><td>{escape(str(value))}</td></tr>' for label, value in rows
    )
    return f'<table class="meta">{cells}</table>'


def _dictionary_table(t: Dict[str, str]) -> str:
    rows = [
        ('ttft_avg_ms / p90 / p95 / p99 / N', t['dict_ttft'], 'ms', t['dict_missing_text']),
        ('tpot_avg_ms / p90 / p95 / p99 / N', t['dict_tpot'], 'ms/token', t['dict_missing_text']),
        ('e2e_avg_s / p99_s', t['dict_e2e'], 's', t['dict_missing_text']),
        ('input_tps / output_tps / rpm', t['dict_tps'], 'tokens/s, requests/min', t['dict_missing_text']),
        ('success_requests / total_requests / lifecycle', t['dict_result'], 'requests', t['quality_note']),
    ]
    body = ''.join(
        f'<tr><td class="field">{escape(field)}</td><td>{escape(meaning)}</td><td>{escape(unit)}</td>'
        f'<td>{escape(missing)}</td></tr>'
        for field, meaning, unit, missing in rows
    )
    head = (
        f'<thead><tr><th scope="col">{escape(t["dict_field"])}</th><th scope="col">{escape(t["dict_meaning"])}</th>'
        f'<th scope="col">{escape(t["dict_unit"])}</th><th scope="col">{escape(t["dict_missing"])}</th></tr></thead>'
    )
    return f'<table class="dictionary">{head}<tbody>{body}</tbody></table>'


def render_html(dataset: ExportDataset, spec: ExportSpec, *, generated_at: str) -> str:
    t = text(spec.locale)
    header = dataset.header
    counts = fill(
        t['counts'],
        runs=len(dataset.runs),
        requests=dataset.request_total,
        success=header.counts.successful_requests or 0,
    )
    badges = ''
    if header.partial:
        badges += f'<span>{escape(t["partial"])}</span>'
    if header.quality:
        badges += f'<span>{escape(t["quality"])}: {escape(header.quality)}</span>'
    charts = ''.join(_chart_svg(metric, series, t, spec.locale) for metric, _statistic, series in dataset.charts)
    nodes = _nodes_section(dataset, t)
    failed = (
        f'<p class="note">{escape(fill(t["failed_runs"], count=dataset.failed_runs))}</p>'
        if dataset.failed_runs
        else ''
    )
    top = ''
    if dataset.top_requests:
        top = (
            f'<h2>{escape(t["top_requests"])}</h2>'
            f'<p class="note">{escape(fill(t["top_requests_note"], count=len(dataset.top_requests)))}</p>'
            f'{_top_requests_table(dataset, t)}'
        )
    footer = fill(
        t['footer'],
        snapshot=header.snapshot_id,
        statistics=header.statistics_version,
        template=EXPORT_TEMPLATE_VERSION,
    )
    return f"""<!DOCTYPE html>
<html lang="{'zh-CN' if spec.locale == 'zh' else 'en'}">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(t['title'])} · {escape(header.snapshot_id)}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<header>
<h1>{escape(t['title'])}</h1>
<p class="subtitle">{escape(t['subtitle'])}</p>
{_meta_table(dataset, spec, t, generated_at)}
<p class="counts">{escape(counts)}<span class="badges">{badges}</span></p>
{failed}
</header>
<h2>{escape(t['charts'])}</h2>
<div class="chart-grid">{charts}</div>
<h2>{escape(t['table'])}</h2>
{_wide_table(dataset, t, spec.locale)}
{top}
{nodes}
<h2>{escape(t['dictionary'])}</h2>
<section class="dictionary-section">
<p class="note">{escape(t['dict_note'])}</p>
{_dictionary_table(t)}
</section>
<p class="footer">{escape(footer)}</p>
</main>
</body>
</html>
"""
