"""Read-only adapter for the AIPerf single-run archive format."""

import html
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from evalscope.metrics.semantics import resolve_perf_semantics
from evalscope.perf.results.aiperf_import import load_aiperf_run, observed_cache_usage, query_aiperf_requests
from evalscope.perf.results.single_run import SingleRunManifest, load_single_manifest
from evalscope.perf.utils.perf_constants import Metrics, PercentileMetrics
from evalscope.utils.uri_utils import sanitize_endpoint

from .perf_archive import PerfArchiveError, extract_timestamp, wrap_chart_html

_MARKER = '<!-- evalscope-aiperf-single-report:1 -->'
_RUN_ID = 'single'


def is_single_archive(directory: str) -> bool:
    """Recognize the new format before the native archive markers."""
    return os.path.lexists(os.path.join(directory, 'single_run.json'))


def _read(directory: str):
    root = Path(directory).resolve(strict=True)
    try:
        manifest = load_single_manifest(str(root))
        if manifest.normalized != 'normalized':
            raise PerfArchiveError('Normalized AIPerf results are unavailable', 404)
        normalized = (root / manifest.normalized).resolve(strict=True)
        if not normalized.is_relative_to(root) or normalized == root or not normalized.is_dir():
            raise PerfArchiveError('Invalid normalized result path', 400)
        result = load_aiperf_run(str(normalized))
    except PerfArchiveError:
        raise
    except FileNotFoundError:
        raise PerfArchiveError('AIPerf results are unavailable', 404) from None
    except (ValueError, OSError):
        raise PerfArchiveError('Invalid AIPerf archive', 400) from None
    return manifest, result, normalized


def _metric(result: Any, key: str, statistic: str = 'avg') -> Optional[float]:
    item = next((value for value in result.metrics if value.key == key), None)
    if item is None:
        return None
    return item.value if statistic == 'avg' else item.statistics.get(statistic)


def _count(result: Any, key: str) -> Optional[int]:
    item = next((value for value in result.metrics if value.key == key), None)
    return item.count if item is not None else None


def build_summary(rel_path: str, directory: str) -> dict:
    """Project a single run without fabricating missing numeric metrics."""
    manifest, result, _ = _read(directory)
    total = result.profiling_requests
    return {
        'path': rel_path,
        'model': manifest.model,
        'alias': manifest.alias,
        'api_type': 'openai',
        'dataset': 'inline',
        'num_runs': 1,
        'total_requests': total,
        'success_rate': 100 * result.profiling_successes / total if total else None,
        'best_rps': _metric(result, 'request_throughput'),
        'best_latency': _metric(result, 'request_latency'),
        'avg_input_tokens': _metric(result, 'input_sequence_length'),
        'avg_output_tokens': _metric(result, 'output_sequence_length'),
        'is_embedding': False,
        'has_html': True,
        'timestamp': extract_timestamp(rel_path, directory),
        'api_host': sanitize_endpoint(manifest.endpoint),
        'protocol': 'openai',
        'concurrency': [manifest.concurrency] if manifest.concurrency is not None else None,
        'engine': 'aiperf',
        'executor_version': result.executor_version,
        'quality': result.quality,
    }


def build_detail(rel_path: str, directory: str) -> dict:
    """Build the existing detail contract from a provenance-aware run."""
    manifest, result, normalized = _read(directory)
    source = {
        'request_throughput': ('request_throughput', Metrics.REQUEST_THROUGHPUT, 1),
        'avg_latency': ('request_latency', Metrics.AVERAGE_LATENCY, 1),
        'p99_latency': ('request_latency', PercentileMetrics.LATENCY, 1),
        'avg_ttft': ('time_to_first_token', Metrics.AVERAGE_TIME_TO_FIRST_TOKEN, 1000),
        'p99_ttft': ('time_to_first_token', PercentileMetrics.TTFT, 1000),
    }
    labels = {
        'concurrency': 'Conc.',
        'request_rate': 'Rate',
        'request_throughput': 'RPS',
        'avg_latency': 'Avg Lat.(s)',
        'p99_latency': 'P99 Lat.(s)',
        'avg_ttft': 'Avg TTFT(ms)',
        'p99_ttft': 'P99 TTFT(ms)',
        'success_rate': 'Success Rate',
    }
    semantics = resolve_perf_semantics([entry[1] for entry in source.values()] + ['success_rate'])
    values = {'concurrency': manifest.concurrency, 'request_rate': manifest.rate}
    counts = {}
    for key, (metric_key, _, scale) in source.items():
        value = _metric(result, metric_key, 'p99' if key.startswith('p99') else 'avg')
        values[key] = value * scale if value is not None else None
        count = _count(result, metric_key)
        if count is not None:
            counts[key] = count
    total = result.profiling_requests
    values['success_rate'] = 100 * result.profiling_successes / total if total else None
    if total:
        counts['success_rate'] = total
    columns = []
    for key in values:
        metric_key = source[key][1] if key in source else ('success_rate' if key == 'success_rate' else None)
        columns.append({'key': key, 'label': labels[key], 'semantics': semantics.get(metric_key)})
    cache = observed_cache_usage(str(normalized))
    basic = {
        'Model': manifest.model,
        'Engine': 'AIPerf',
        'Executor Version': result.executor_version,
        'Status': manifest.status,
        'Quality': result.quality,
        'Total Requests': str(total),
        'Succeed Requests': str(result.profiling_successes),
        'Server Cache Reads': str(cache.ratio) if cache.ratio is not None else 'unavailable',
    }
    endpoint = sanitize_endpoint(manifest.endpoint)
    if endpoint:
        basic['Endpoint'] = endpoint
    return {
        'path': rel_path,
        'model': manifest.model,
        'alias': manifest.alias,
        'api_type': 'openai',
        'dataset': 'inline',
        'generated_at': extract_timestamp(rel_path, directory),
        'basic_info': basic,
        'configuration': {
            'load_mode': manifest.load_mode,
            'planned_requests': str(manifest.planned_requests),
            'output_token_limit': str(manifest.output_token_limit),
            'input_sha256': manifest.input_sha256,
        },
        'summary_columns': columns,
        'summary_rows': [{'values': values, 'sample_counts': counts}],
        'total_requests': total,
        'best_config': {},
        'recommendations': [],
        'num_runs': 1,
        'is_embedding': False,
        'has_html': True,
        'engine': 'aiperf',
        'executor_version': result.executor_version,
        'quality': result.quality,
        'quality_reasons': result.quality_reasons,
    }


def list_items(directory: str) -> list[dict]:
    """Return one run descriptor and its measured percentile values."""
    manifest, result, normalized = _read(directory)
    total, _ = query_aiperf_requests(str(normalized), phase='profiling', limit=1)
    rows = []
    for metric in result.metrics:
        if any(key in metric.statistics for key in ('p50', 'p95', 'p99')):
            rows.append(
                [
                    metric.key,
                    metric.statistics.get('p50'),
                    metric.statistics.get('p95'),
                    metric.statistics.get('p99'),
                    metric.unit,
                    metric.count,
                ]
            )
    return [
        {
            'dir_name': _RUN_ID,
            'name': 'AIPerf',
            'parallel': manifest.concurrency,
            'number': manifest.planned_requests,
            'rate': manifest.rate,
            'total_requests': result.profiling_requests,
            'succeed_requests': result.profiling_successes,
            'success_rate': 100 * result.profiling_successes / result.profiling_requests
            if result.profiling_requests
            else None,
            'num_requests': total,
            'has_requests': total > 0,
            'percentile_columns': ['Metric', 'P50', 'P95', 'P99', 'Unit', 'Count'] if rows else [],
            'percentile_rows': rows,
        }
    ]


def query_requests(directory: str, run_name: str, status: Optional[str], page: int, page_size: int) -> dict:
    """Page only records belonging to the declared single-run identity."""
    if run_name != _RUN_ID:
        raise PerfArchiveError('Run not found', 404)
    _, _, normalized = _read(directory)
    status = status if status in ('success', 'failed') else None
    page = max(1, page)
    page_size = max(1, min(500, page_size))
    offset = (page - 1) * page_size
    total, records = query_aiperf_requests(
        str(normalized), offset=offset, limit=page_size, phase='profiling', status=status
    )
    rows = []
    for index, row in enumerate(records):
        rows.append(
            {
                '#': offset + index + 1,
                'Latency(s)': row.latency_seconds,
                'TTFT(ms)': row.ttft_seconds * 1000 if row.ttft_seconds is not None else None,
                'TPOT(ms)': None,
                'Prompt': row.prompt_tokens,
                'Completion': row.output_tokens,
                'Success': {'success': 'OK', 'error': 'FAIL', 'cancelled': 'CANCELLED', 'incomplete': 'INCOMPLETE'}[
                    row.status
                ],
                'Cached': row.cached_tokens,
                'Cache source': row.cached_tokens_source,
                'Input source': row.prompt_tokens_source,
                'Request ID': row.request_id,
            }
        )
    return {
        'columns': [
            '#',
            'Latency(s)',
            'TTFT(ms)',
            'TPOT(ms)',
            'Prompt',
            'Completion',
            'Success',
            'Cached',
            'Cache source',
            'Input source',
            'Request ID',
        ],
        'rows': rows,
        'total': total,
        'page': page,
        'page_size': page_size,
        'has_db': True,
    }


def write_report(rel_path: str, directory: str) -> str:
    """Render a standalone escaped HTML summary from the same detail projection."""
    detail = build_detail(rel_path, directory)

    def escape(value: object) -> str:
        return html.escape(str(value), quote=True)

    rows = ''.join(
        f'<tr><th>{escape(key)}</th><td>{escape(value)}</td></tr>' for key, value in detail['basic_info'].items()
    )
    values = detail['summary_rows'][0]['values']
    metrics = ''.join(
        f'<tr><th>{escape(column["label"])}</th><td>{escape(values[column["key"]]) if values[column["key"]] is not None else "unavailable"}</td></tr>'
        for column in detail['summary_columns']
    )
    body = (
        '<!DOCTYPE html>\n' + _MARKER + '\n<html lang="en"><head><meta charset="utf-8">'
        '<title>AIPerf report</title></head><body><h1>AIPerf report</h1>'
        '<p>Values come from the AIPerf client and server-reported usage; missing values are unavailable.</p>'
        f'<table>{rows}</table><h2>Metrics</h2><table>{metrics}</table></body></html>'
    )
    root = Path(directory).resolve(strict=True)
    report = root / 'aiperf_report.html'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, delete=False) as file:
            temporary = Path(file.name)
            file.write(body)
        os.replace(temporary, report)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return str(report)


def is_current_report(path: str) -> bool:
    """Recognize only a renderer-owned AIPerf report."""
    try:
        with open(path, encoding='utf-8') as file:
            return file.readline().strip() == '<!DOCTYPE html>' and file.readline().strip() == _MARKER
    except OSError:
        return False


def render_chart(directory: str, chart_type: str, run_name: Optional[str], theme: str) -> str:
    """Render a bounded chart using measured AIPerf values only."""
    import plotly.graph_objects as go
    from plotly.offline import plot

    _, result, normalized = _read(directory)
    per_run = {'percentile_latency', 'percentile_token', 'req_latency', 'req_ttft_tpot', 'req_tokens', 'req_success'}
    if chart_type in per_run and run_name != _RUN_ID:
        raise PerfArchiveError('Run not found', 404)
    figure = go.Figure()
    title = chart_type
    if chart_type in ('latency', 'ttft', 'rps', 'success'):
        values = {
            'latency': _metric(result, 'request_latency'),
            'ttft': _metric(result, 'time_to_first_token'),
            'rps': _metric(result, 'request_throughput'),
            'success': (
                100 * result.profiling_successes / result.profiling_requests if result.profiling_requests else None
            ),
        }
        value = values[chart_type]
        if value is None:
            raise PerfArchiveError('Chart metric unavailable', 404)
        figure.add_bar(x=['AIPerf'], y=[value], name=chart_type)
        title = {
            'latency': 'Mean request latency (s)',
            'ttft': 'Mean TTFT (s)',
            'rps': 'Request throughput (requests/s)',
            'success': 'Successful requests (%)',
        }[chart_type]
    elif chart_type == 'throughput':
        for label, key in [
            ('Input tokens/s', 'input_token_throughput'),
            ('Output tokens/s', 'output_token_throughput'),
            ('Total tokens/s', 'total_token_throughput'),
        ]:
            value = _metric(result, key)
            if value is not None:
                figure.add_bar(x=['AIPerf'], y=[value], name=label)
        if not figure.data:
            raise PerfArchiveError('Chart metric unavailable', 404)
        title = 'Token throughput (tokens/s)'
    elif chart_type in ('percentile_latency', 'percentile_token'):
        key = 'request_latency' if chart_type == 'percentile_latency' else 'time_to_first_token'
        values = [_metric(result, key, statistic) for statistic in ('p50', 'p95', 'p99')]
        if all(value is None for value in values):
            raise PerfArchiveError('Percentile metric unavailable', 404)
        figure.add_bar(x=['P50', 'P95', 'P99'], y=values, name=key)
        title = 'Request latency (s)' if key == 'request_latency' else 'TTFT (s)'
    elif chart_type in ('req_latency', 'req_ttft_tpot', 'req_tokens', 'req_success'):
        total, records = query_aiperf_requests(str(normalized), phase='profiling', limit=500)
        if not records:
            raise PerfArchiveError('Request data unavailable', 404)
        if chart_type == 'req_success':
            labels = ['success', 'error', 'cancelled', 'incomplete']
            figure.add_bar(x=labels, y=[sum(row.status == label for row in records) for label in labels])
        else:
            x = [row.index for row in records]
            if chart_type == 'req_latency':
                series = [('Latency (s)', [row.latency_seconds for row in records])]
            elif chart_type == 'req_ttft_tpot':
                series = [('TTFT (s)', [row.ttft_seconds for row in records])]
            else:
                series = [
                    ('Input tokens', [row.prompt_tokens for row in records]),
                    ('Output tokens', [row.output_tokens for row in records]),
                ]
            for label, values in series:
                if any(value is not None for value in values):
                    figure.add_scatter(x=x, y=values, mode='markers', name=label, connectgaps=False)
        if not figure.data:
            raise PerfArchiveError('Request metric unavailable', 404)
        title = f'First {len(records)} of {total} profiling requests'
    else:
        raise PerfArchiveError('Chart metric unavailable', 404)
    figure.update_layout(title=title, template='plotly_dark' if theme == 'dark' else 'plotly_white')
    return wrap_chart_html(plot(figure, output_type='div', include_plotlyjs=False, config={'responsive': True}))
