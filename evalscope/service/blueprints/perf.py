import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

from flask import Blueprint, current_app, jsonify, make_response, request, send_file, stream_template_string
from tabulate import tabulate

from evalscope.metrics.semantics import resolve_perf_semantics
from evalscope.perf.arguments import Arguments as PerfArguments
from evalscope.perf.executors.arguments import AIPerfArguments, parse_perf_task_arguments
from evalscope.perf.executors.external import supports_external_runner
from evalscope.perf.executors.models import ExecutionContext, ExecutionOutcome, ExternalRuntime
from evalscope.perf.experiments.planner import CacheSubrunPlan, plan_cache_experiment
from evalscope.perf.experiments.storage import write_experiment_json
from evalscope.perf.experiments.tokenizer import HuggingFacePrefixTokenizer
from evalscope.perf.integrations.aiperf.live import LiveSnapshot
from evalscope.perf.integrations.aiperf.metrics import analyze_node_metrics, covers_node_ids
from evalscope.perf.integrations.aiperf.preflight import probe_nodes
from evalscope.perf.integrations.aiperf.single_run import (
    AIPerfSingleRunRequest,
    probe_version,
    validate_single_run_configuration,
)
from evalscope.perf.plugin.api.base import ApiPluginBase
from evalscope.perf.plugin.registry import ApiRegistry, DatasetRegistry
from evalscope.perf.results.aiperf_import import (
    import_aiperf_run,
    query_aiperf_requests,
    query_node_metrics,
    request_cache_groups,
)
from evalscope.perf.results.models import AIPerfImportArguments, MetricTargetReference, PerfRequestRecord
from evalscope.perf.results.single_run import SingleRunManifest, write_single_manifest
from evalscope.perf.sla.sla_run import SLAResult, format_sla_tables
from evalscope.perf.utils.benchmark_util import Metrics
from evalscope.perf.utils.rich_display import EmbeddingResultAnalyzer, LLMResultAnalyzer
from evalscope.service.api_models import (
    CacheExperimentListResponse,
    CacheExperimentResponse,
    CacheNodeMetricsResponse,
    CacheRequestGroupsResponse,
    CacheRequestsResponse,
    CacheScenarioResponse,
    DeletePerfRunResponse,
    EvalInvokeResponse,
    ListPerfRunsResponse,
    LogResponse,
    PerfDetailResponse,
    PerfEnginesResponse,
    PerfInvokeResponse,
    PerfLiveResponse,
    PerfPlanResponse,
    PerfRequestsResponse,
    PerfRunsListResponse,
    ProgressResponse,
    TaskStatusResponse,
)
from evalscope.utils.logger import get_logger
from evalscope.utils.uri_utils import sanitize_endpoint, validate_http_endpoint

from .. import perf_archive
from ..perf_archive import PerfArchiveError
from ..responses import json_response
from ..utils import (
    OUTPUT_DIR,
    TaskStoppedError,
    active_task_ids,
    build_task_info,
    create_log_file,
    finish_task,
    get_log_content,
    list_active_tasks,
    read_task_manifest,
    run_in_subprocess,
    run_perf_wrapper,
    serialize_result,
    stop_process,
    task_file_path,
    validate_task_id,
)
from ..utils.process import run_cache_experiment_in_subprocess, run_single_aiperf_in_subprocess, task_is_cancelling

logger = get_logger()


def _root_path() -> str:
    """Resolve the outputs root: query param > app config > OUTPUT_DIR."""
    return request.args.get('root_path', current_app.config.get('OUTPUTS_ROOT') or OUTPUT_DIR)


def _build_perf_table(result, api_type: str = None) -> str:
    """Build a Markdown pipe-table from perf benchmark results with Chinese headers.

    Returns an empty string when no valid results are found.
    """
    if isinstance(result, SLAResult):
        return format_sla_tables(result)
    try:
        is_emb = Metrics.is_embedding_or_rerank(api_type)
        analyzer = EmbeddingResultAnalyzer() if is_emb else LLMResultAnalyzer()
        analysis = analyzer.analyze(result)
        if not analysis.rows:
            return ''
        if is_emb:
            headers = [
                '并发数',
                '请求速率',
                '每秒请求数',
                '平均延迟(s)',
                'P99延迟(s)',
                '平均输入TPS',
                'P99输入TPS',
                '平均输入Token数',
                '成功率',
            ]
        else:
            headers = [
                '并发数',
                '请求速率',
                '请求数',
                '每秒请求数',
                '平均延迟(s)',
                'P99延迟(s)',
                '平均首字延迟(s)',
                'P99首字延迟(s)',
                '平均每Token延迟(s)',
                'P99每Token延迟(s)',
                '生成速度(toks/s)',
                '成功率',
            ]
        return tabulate([list(r.values()) for r in analysis.rows], headers=headers, tablefmt='pipe')
    except Exception as e:
        logger.warning(f'Failed to build perf table: {e}')
        return ''


bp_perf = Blueprint('perf', __name__, url_prefix='/api/v1/perf')


@bp_perf.route('/live/<task_id>', methods=['GET'])
def get_live_perf_metrics(task_id: str):
    """Read only the current run's bounded, node-scoped scrape snapshot."""
    from ..perf_experiments import load_experiment

    try:
        validate_task_id(task_id)
        root = task_file_path(task_id, 'perf')
        from ..utils import log

        if root != os.path.join(os.path.realpath(os.path.join(log.OUTPUT_DIR, task_id)), 'perf'):
            raise ValueError('Live metrics directory cannot be redirected')
        plan_path = task_file_path(task_id, 'perf/experiment_plan.json')
        result = PerfLiveResponse(task_id=task_id)
        snapshot_path = os.path.join(root, 'live_metrics.json')
        if os.path.isfile(plan_path):
            experiment = load_experiment(task_id)
            current = experiment.state.current_subrun
            if current is None:
                return json_response(PerfLiveResponse, result.model_dump(mode='json'))
            item = next((row for row in experiment.plan.subruns if row.subrun_id == current), None)
            if item is None:
                raise ValueError('Unknown current subrun')
            runs = task_file_path(task_id, 'perf/runs')
            run = task_file_path(task_id, f'perf/runs/{current}')
            if runs != os.path.join(root, 'runs') or run != os.path.join(runs, current):
                raise ValueError('Live metrics run directory cannot be redirected')
            result.current_subrun = current
            result.target_ratio = item.target_ratio
            result.load_mode = item.load_mode
            result.concurrency = item.load_point.concurrency
            result.rate = item.load_point.rate
            result.repetition = item.repetition
            snapshot_path = os.path.join(run, 'live_metrics.json')
        if os.path.islink(snapshot_path):
            raise ValueError('Live snapshot cannot be a symlink')
        if os.path.isfile(snapshot_path):
            if os.path.getsize(snapshot_path) > 2 * 1024 * 1024:
                raise ValueError('Live snapshot exceeds size limit')
            with open(snapshot_path, encoding='utf-8') as file:
                snapshot = LiveSnapshot.model_validate_json(file.read())
                result.nodes = snapshot.nodes
                result.expected_interval_seconds = snapshot.expected_interval_seconds
        return json_response(PerfLiveResponse, result.model_dump(mode='json'))
    except (OSError, ValueError):
        return jsonify({'error': 'Live metrics unavailable'}), 400


@bp_perf.route('/experiments', methods=['GET'])
def list_cache_experiments():
    """List experiments in the service outputs root without exposing artifact paths."""
    from ..perf_experiments import load_experiment
    from ..utils import log

    try:
        offset = int(request.args.get('offset', '0'))
        limit = int(request.args.get('limit', '100'))
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError('Invalid page')
        rows = []
        if os.path.isdir(log.OUTPUT_DIR):
            with os.scandir(log.OUTPUT_DIR) as entries:
                names = sorted((entry.name for entry in entries if entry.is_dir(follow_symlinks=False)), reverse=True)
            for name in names:
                try:
                    experiment = load_experiment(name)
                except (OSError, ValueError):
                    continue
                state = experiment.state
                rows.append(
                    {
                        'task_id': name,
                        'status': state.status,
                        'quality': state.quality,
                        'completed_subruns': state.completed_subruns,
                        'total_subruns': state.total_subruns,
                    }
                )
        return json_response(
            CacheExperimentListResponse,
            {'experiments': rows[offset : offset + limit], 'total': len(rows), 'offset': offset, 'limit': limit},
        )
    except (ValueError, OSError):
        return jsonify({'error': 'Invalid experiment page'}), 400


@bp_perf.route('/experiments/<task_id>', methods=['GET'])
def get_cache_experiment(task_id: str):
    """Read the saved plan and experiment status, including partial runs."""
    from ..perf_experiments import load_experiment

    try:
        return json_response(CacheExperimentResponse, load_experiment(task_id))
    except FileNotFoundError:
        return jsonify({'error': 'Experiment not found'}), 404
    except (ValueError, OSError):
        return jsonify({'error': 'Invalid experiment data or task ID'}), 400


@bp_perf.route('/experiments/<task_id>', methods=['DELETE'])
def delete_cache_experiment(task_id: str):
    """Delete an inactive experiment and all of its subruns."""
    from ..perf_experiments import load_experiment

    try:
        load_experiment(task_id)
        if task_id in active_task_ids():
            return jsonify({'error': 'Cannot delete an active task'}), 409
        run_dir = task_file_path(task_id, 'perf')
        expected = os.path.join(os.path.realpath(os.path.join(OUTPUT_DIR, task_id)), 'perf')
        if run_dir != expected:
            raise ValueError('Invalid experiment directory')
        shutil.rmtree(run_dir)
        return json_response(DeletePerfRunResponse, {'success': True, 'path': f'{task_id}/perf'})
    except FileNotFoundError:
        return jsonify({'error': 'Experiment not found'}), 404
    except (ValueError, OSError):
        return jsonify({'error': 'Invalid experiment data or task ID'}), 400


@bp_perf.route('/experiments/<task_id>/report', methods=['GET'])
def get_cache_experiment_report(task_id: str):
    """Stream a complete standalone report using the same projections as the API."""
    from ..perf_experiments import _load_planned_scenario, _scenario_normalized_path, load_experiment

    try:
        experiment = load_experiment(task_id)

        def rows() -> Iterator[tuple]:
            for item, state in zip(experiment.plan.subruns, experiment.state.subruns):
                scenario = None
                request_groups = None
                if state.scenario:
                    try:
                        scenario = _load_planned_scenario(task_id, item)
                        if scenario.cache and scenario.cache.availability == 'available':
                            request_groups = request_cache_groups(_scenario_normalized_path(task_id, item))
                    except (OSError, ValueError, sqlite3.Error):
                        pass

                def request_rows(run: CacheSubrunPlan) -> Iterator[Optional[PerfRequestRecord]]:
                    offset = 0
                    try:
                        path = _scenario_normalized_path(task_id, run)
                        while True:
                            total, records = query_aiperf_requests(path, offset=offset, limit=500)
                            yield from records
                            offset += len(records)
                            if not records or offset >= total:
                                return
                    except (OSError, ValueError, sqlite3.Error):
                        yield None

                yield item, state, scenario, request_groups, request_rows(item) if scenario and scenario.result else ()

        html = stream_template_string(
            """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Prefix cache experiment</title></head>
<body><h1>Prefix cache experiment: {{ task_id }}</h1>
<p>Status: {{ experiment.state.status }}; quality: {{ experiment.state.quality or 'unavailable' }};
completed: {{ experiment.state.completed_subruns }}/{{ experiment.state.total_subruns }}</p>
<p>Target reuse, generated reuse and server-reported cache reads are distinct. Missing values are unavailable.
Node windows include shared traffic and are not per-request P/D timings. Deployment metric semantics,
chat-template match and routing coverage are unverified.</p>
<p>Plan: {{ experiment.plan.plan_hash }}; configuration: {{ experiment.plan.configuration_hash }};
tokenizer: {{ experiment.plan.tokenizer_id }}; template: {{ experiment.plan.template_id }}.</p>
<table border="1"><thead><tr><th>Run</th><th>Load mode</th><th>Load</th><th>Repetition</th>
<th>Status</th><th>Target reuse</th><th>Generated reuse</th><th>Server cache reads</th>
<th>Observed requests</th><th>Quality reasons / client metrics / node windows</th></tr></thead><tbody>
{% for item, state, scenario, request_groups, requests in rows %}<tr><td>{{ item.subrun_id }}</td><td>{{ item.load_mode }}</td>
<td>{{ item.load_point.concurrency if item.load_point.concurrency is not none else item.load_point.rate }}</td>
<td>{{ item.repetition + 1 }}</td><td>{{ state.status }}</td><td>{{ item.target_ratio }}</td>
<td>{{ scenario.generated_ratio if scenario and scenario.generated_ratio is not none else 'unavailable' }}</td>
<td>{{ scenario.cache.ratio if scenario and scenario.cache and scenario.cache.ratio is not none else 'unavailable' }}</td>
<td>{{ scenario.cache.observed_requests ~ '/' ~ scenario.cache.successful_requests if scenario and scenario.cache and scenario.cache.successful_requests is not none else 'unavailable' }}</td>
<td>{{ scenario.quality_reasons|join(', ') if scenario else 'unavailable' }}
<p>Execution order={{ item.order + 1 }}, seed={{ item.seed }}, namespace={{ item.namespace }},
planned requests={{ item.request_count }}, output limit={{ item.output_token_limit if item.output_token_limit is not none else 'unavailable' }};
generated input tokens per request={{ scenario.input_tokens_per_request if scenario and scenario.input_tokens_per_request is not none else 'unavailable' }};
request-hash list SHA-256={{ scenario.request_hashes_sha256 if scenario and scenario.request_hashes_sha256 else 'unavailable' }}.
A fresh namespace does not prove the server cache was cleared.</p>
{% if scenario and scenario.result %}<p>Executor: {{ scenario.result.engine }} {{ scenario.result.executor_version }};
requests / successes / errors: {{ scenario.result.profiling_requests }} / {{ scenario.result.profiling_successes }} /
{{ scenario.result.profiling_errors }}; quality: {{ scenario.result.quality }}</p>
<ul>{% for metric in scenario.result.metrics %}<li>{{ metric.key }}: {{ metric.value if metric.value is not none else 'unavailable' }}
{{ metric.unit }} ({{ metric.source }}, {{ metric.scope }}, n={{ metric.count if metric.count is not none else 'unavailable' }},
{{ metric.availability }}, {{ metric.reason or 'no reason' }})</li>{% endfor %}</ul>{% endif %}
{% if request_groups and request_groups.availability == 'available' %}<p>Request cache groups: server_usage,
coverage {{ request_groups.observed_requests }}/{{ request_groups.successful_requests if request_groups.successful_requests is not none else 'unavailable' }};
percentiles are exact nearest-rank per group and not pooled across runs.</p><ul>
{% for group in request_groups.groups %}<li>{{ group.group }}: requests={{ group.count }};
latency seconds mean/P50/P95/P99={{ group.latency.mean if group.latency.mean is not none else 'unavailable' }}/
{{ group.latency.p50 if group.latency.p50 is not none else 'unavailable' }}/{{ group.latency.p95 if group.latency.p95 is not none else 'unavailable' }}/
{{ group.latency.p99 if group.latency.p99 is not none else 'unavailable' }} (n={{ group.latency.count }});
TTFT seconds mean/P50/P95/P99={{ group.ttft.mean if group.ttft.mean is not none else 'unavailable' }}/
{{ group.ttft.p50 if group.ttft.p50 is not none else 'unavailable' }}/{{ group.ttft.p95 if group.ttft.p95 is not none else 'unavailable' }}/
{{ group.ttft.p99 if group.ttft.p99 is not none else 'unavailable' }} (n={{ group.ttft.count }})</li>{% endfor %}</ul>
{% elif request_groups %}<p>Request cache groups unavailable: {{ request_groups.reason or 'no reason' }}</p>{% endif %}
{% if scenario and scenario.node_analysis %}<p>Node analysis: {{ scenario.node_analysis.availability }},
window: {{ scenario.node_analysis.window_source or 'unavailable' }},
{{ scenario.node_analysis.reason or 'no reason' }}</p>
<ul>{% for ratio in scenario.node_analysis.cache_ratios %}<li>Node {{ ratio.node_id }} {{ ratio.role }} lookup-token hit ratio:
{{ ratio.ratio if ratio.ratio is not none else 'unavailable' }} ({{ ratio.hit_tokens if ratio.hit_tokens is not none else 'unavailable' }} /
{{ ratio.lookup_tokens if ratio.lookup_tokens is not none else 'unavailable' }}; {{ ratio.availability }};
{{ ratio.reason or 'no reason' }}; labels={{ ratio.labels }})</li>{% endfor %}
{% for node in scenario.node_analysis.series %}<li>{{ node.node_id }} {{ node.role }} {{ node.metric.name }}
({{ node.engine }} {{ node.metric.upstream_version }}, {{ node.attribution }}, {{ node.window_source }}, labels={{ node.labels }}):
{% if node.statistic %}{{ node.statistic.value if node.statistic.value is not none else 'unavailable' }} {{ node.metric.unit }};
sample peak={{ node.statistic.sample_peak if node.statistic.sample_peak is not none else 'unavailable' }};
n={{ node.statistic.sample_count }}; coverage={{ node.statistic.coverage if node.statistic.coverage is not none else 'unavailable' }};
{{ node.statistic.availability }}, {{ node.statistic.boundary }}, {{ node.statistic.reason or 'no reason' }}
{% elif node.histogram %}mean={{ node.histogram.mean if node.histogram.mean is not none else 'unavailable' }} {{ node.histogram.unit }};
approximate P50/P95/P99={{ node.histogram.p50 if node.histogram.p50 is not none else 'unavailable' }}/
{{ node.histogram.p95 if node.histogram.p95 is not none else 'unavailable' }}/{{ node.histogram.p99 if node.histogram.p99 is not none else 'unavailable' }};
n={{ node.histogram.count if node.histogram.count is not none else 'unavailable' }};
{{ node.histogram.availability }}, {{ node.histogram.boundary }}, {{ node.histogram.reason or 'no reason' }}
{% else %}unavailable: {{ node.reason or 'no reason' }}{% endif %}</li>{% endfor %}</ul>{% endif %}
{% if scenario and scenario.result %}<details><summary>Request records (all phases)</summary><table border="1">
<thead><tr><th>Index / request / session / turn</th><th>Phase / status</th><th>Latency / TTFT seconds</th>
<th>Input / output tokens (source)</th><th>Cached tokens (source)</th></tr></thead><tbody>
{% for record in requests %}{% if record %}<tr><td>{{ record.index }} / {{ record.request_id or 'unavailable' }} /
{{ record.session_id or 'unavailable' }} / {{ record.turn_index if record.turn_index is not none else 'unavailable' }}</td>
<td>{{ record.phase }} / {{ record.status }}</td><td>{{ record.latency_seconds if record.latency_seconds is not none else 'unavailable' }} /
{{ record.ttft_seconds if record.ttft_seconds is not none else 'unavailable' }}</td>
<td>{{ record.prompt_tokens if record.prompt_tokens is not none else 'unavailable' }} ({{ record.prompt_tokens_source or 'unavailable' }}) /
{{ record.output_tokens if record.output_tokens is not none else 'unavailable' }} ({{ record.output_tokens_source or 'unavailable' }})</td>
<td>{{ record.cached_tokens if record.cached_tokens is not none else 'unavailable' }} ({{ record.cached_tokens_source or 'unavailable' }})</td></tr>
{% else %}<tr><td colspan="5">Request records unavailable or incomplete.</td></tr>{% endif %}{% endfor %}</tbody></table></details>{% endif %}
</td></tr>{% endfor %}
</tbody></table></body></html>""",
            task_id=task_id,
            experiment=experiment,
            rows=rows(),
        )
        response = make_response(html)
        response.headers['Content-Security-Policy'] = "default-src 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    except FileNotFoundError:
        return jsonify({'error': 'Experiment not found'}), 404
    except (ValueError, OSError):
        return jsonify({'error': 'Invalid experiment data or task ID'}), 400


@bp_perf.route('/experiments/<task_id>/runs/<subrun_id>', methods=['GET'])
def get_cache_scenario(task_id: str, subrun_id: str):
    """Read generated reuse, observed cache usage and run quality."""
    from ..perf_experiments import load_scenario

    try:
        return json_response(CacheScenarioResponse, load_scenario(task_id, subrun_id))
    except FileNotFoundError:
        return jsonify({'error': 'Scenario not found'}), 404
    except (ValueError, OSError, sqlite3.Error):
        return jsonify({'error': 'Invalid scenario data or identity'}), 400


@bp_perf.route('/experiments/<task_id>/runs/<subrun_id>/requests', methods=['GET'])
def get_cache_scenario_requests(task_id: str, subrun_id: str):
    """Page normalized request records for a planned subrun."""
    from ..perf_experiments import scenario_path

    try:
        _, path = scenario_path(task_id, subrun_id)
        offset = int(request.args.get('offset', '0'))
        limit = int(request.args.get('limit', '100'))
        total, rows = query_aiperf_requests(path, offset=offset, limit=limit, phase=request.args.get('phase'))
        return json_response(
            CacheRequestsResponse, {'total': total, 'offset': offset, 'limit': limit, 'requests': rows}
        )
    except FileNotFoundError:
        return jsonify({'error': 'Scenario results not found'}), 404
    except (ValueError, OSError):
        return jsonify({'error': 'Invalid request page or scenario identity'}), 400


@bp_perf.route('/experiments/<task_id>/runs/<subrun_id>/request-cache-groups', methods=['GET'])
def get_cache_scenario_request_groups(task_id: str, subrun_id: str):
    """Compare request latency only where the server reported cache usage."""
    from ..perf_experiments import scenario_path

    try:
        _, path = scenario_path(task_id, subrun_id)
        return json_response(CacheRequestGroupsResponse, request_cache_groups(path).model_dump(mode='json'))
    except FileNotFoundError:
        return jsonify({'error': 'Scenario results not found'}), 404
    except (ValueError, OSError, sqlite3.Error):
        return jsonify({'error': 'Invalid scenario data or identity'}), 400


def _node_metrics_page(path: str):
    offset = int(request.args.get('offset', '0'))
    limit = int(request.args.get('limit', '500'))
    start = request.args.get('start_ns')
    end = request.args.get('end_ns')
    node_id = request.args.get('node_id')
    metric_name = request.args.get('metric_name')
    if node_id is not None or metric_name is not None:
        count, _ = query_node_metrics(path, node_id=node_id, metric_name=metric_name, limit=1)
        if not count:
            return jsonify({'error': 'Node or metric not found in this scenario'}), 404
    total, rows = query_node_metrics(
        path,
        node_id=node_id,
        metric_name=metric_name,
        start_ns=int(start) if start is not None else None,
        end_ns=int(end) if end is not None else None,
        offset=offset,
        limit=limit,
    )
    return json_response(
        CacheNodeMetricsResponse,
        {
            'total': total,
            'offset': offset,
            'limit': limit,
            'samples': rows,
            'aggregation': 'raw',
            'downsample': 'none',
        },
    )


@bp_perf.route('/experiments/<task_id>/runs/<subrun_id>/metrics', methods=['GET'])
def get_cache_scenario_metrics(task_id: str, subrun_id: str):
    """Page raw node-window samples with explicitly unverified semantics."""
    from ..perf_experiments import scenario_path

    try:
        _, path = scenario_path(task_id, subrun_id)
        return _node_metrics_page(path)
    except FileNotFoundError:
        return jsonify({'error': 'Scenario results not found'}), 404
    except (ValueError, OSError):
        return jsonify({'error': 'Invalid metrics page or scenario identity'}), 400


@bp_perf.route('/server-metrics', methods=['GET'])
def get_server_metrics():
    """Read a planned subrun's node samples within the configured outputs root."""
    from ..utils import log

    root = request.args.get('root_path', log.OUTPUT_DIR)
    relative = request.args.get('path', '')
    subrun_id = request.args.get('subrun_id', '')
    try:
        if os.path.realpath(root) != os.path.realpath(log.OUTPUT_DIR):
            raise ValueError('Invalid outputs root')
        parts = relative.split('/')
        if len(parts) != 2 or parts[1] != 'perf':
            raise ValueError('Invalid experiment path')
        validate_task_id(parts[0])
        if subrun_id:
            return get_cache_scenario_metrics(parts[0], subrun_id)
        from ..perf_archive_aiperf import normalized_path

        return _node_metrics_page(normalized_path(task_file_path(parts[0], 'perf')))
    except PerfArchiveError as error:
        return jsonify({'error': str(error)}), error.status
    except (OSError, ValueError):
        return jsonify({'error': 'Invalid experiment path or outputs root'}), 400


@lru_cache(maxsize=16)
def _probe_aiperf_runtime(path: str, identity: tuple, epoch: int) -> bool:
    try:
        with tempfile.TemporaryDirectory(prefix='evalscope-aiperf-probe-') as directory:
            runtime = ExternalRuntime(executable=path, graceful_timeout=1, terminate_timeout=1, kill_timeout=2)
            return (
                probe_version(
                    ExecutionContext(task_id='capability-probe', output_dir=directory, runtime=runtime),
                    timeout_seconds=2,
                )
                == runtime.expected_version
            )
    except (OSError, RuntimeError, ValueError):
        return False


def _aiperf_runtime() -> ExternalRuntime | None:
    configured = current_app.config.get('AIPERF_EXECUTABLE') or os.environ.get('EVALSCOPE_AIPERF_EXECUTABLE')
    if not supports_external_runner() or not isinstance(configured, str) or not os.path.isabs(configured):
        return None
    try:
        path = os.path.realpath(configured)
        stat = os.stat(path)
        if not os.path.isfile(path) or not os.access(path, os.X_OK):
            return None
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if not _probe_aiperf_runtime(path, identity, int(time.monotonic() // 60)):
            return None
    except OSError:
        return None
    return ExternalRuntime(executable=path)


@bp_perf.route('/engines', methods=['GET'])
def get_perf_engines():
    """Describe execution engines without exposing local runtime paths."""
    available = _aiperf_runtime() is not None
    return json_response(
        PerfEnginesResponse,
        {
            'engines': [
                {'engine': 'evalscope', 'available': True},
                {
                    'engine': 'aiperf',
                    'available': available,
                    'version': '0.13.0' if available else None,
                    'protocols': ['openai_chat'],
                    'reason': None if available else 'AIPerf 0.13.0 runtime is unavailable',
                },
            ]
        },
    )


@bp_perf.route('/validate', methods=['POST'])
def validate_perf_experiment():
    """Plan a local cache experiment without sending inference or Metrics requests."""
    task_id = request.headers.get('EvalScope-Task-Id')
    try:
        validate_task_id(task_id)
        task = parse_perf_task_arguments(request.get_json())
        if task.engine != 'aiperf' or task.aiperf is None:
            raise ValueError('AIPerf configuration is required')
        tokenizer = HuggingFacePrefixTokenizer(task.aiperf.tokenizer_path)
        plan = plan_cache_experiment(task.aiperf, task_id, tokenizer.tokenizer_hash, tokenizer.template_hash)
    except (ValueError, TypeError, OSError, ImportError):
        return jsonify({'error': 'Invalid cache experiment configuration or tokenizer'}), 400
    flag = request.args.get('probe_metrics', '0')
    if flag not in ('0', '1'):
        return jsonify({'error': 'Invalid metrics probe option'}), 400
    try:
        nodes = probe_nodes(task.aiperf.telemetry.targets) if flag == '1' else []
    except (ImportError, RuntimeError):
        return jsonify({'error': 'Metrics preflight is unavailable'}), 503
    available = _aiperf_runtime() is not None
    warnings = ([] if available else ['AIPerf 0.13.0 runtime is unavailable']) + [
        'Model chat template match is unverified',
        'Node metric semantics and routing coverage are unverified',
    ]
    if task.aiperf.telemetry.required and not task.aiperf.telemetry.targets:
        warnings.append('Required node metrics targets are missing')
    elif task.aiperf.telemetry.targets and flag == '0':
        warnings.append('Node metrics have not been probed')
    elif any(node.status != 'available' for node in nodes):
        warnings.append('One or more node metrics are unavailable or invalid')
    return json_response(
        PerfPlanResponse,
        {
            'plan': plan,
            'engine_available': available,
            'metrics_probe': 'completed' if flag == '1' else 'not_requested',
            'nodes': nodes,
            'warnings': warnings,
        },
    )


def _send_safe_perf_report(report_file: str):
    from evalscope.perf.utils.report.generate_report import is_current_perf_report

    from ..perf_archive_aiperf import is_current_report

    aiperf_report = is_current_report(report_file)
    response = send_file(
        report_file, mimetype='text/html', as_attachment=not (aiperf_report or is_current_perf_report(report_file))
    )
    if aiperf_report:
        response.headers['Content-Security-Policy'] = "default-src 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def _validate_performance_config(args: PerfArguments) -> None:
    plugin = ApiRegistry.get_class(args.api)
    if plugin.process_request is ApiPluginBase.process_request:
        raise ValueError('This API protocol does not implement HTTP benchmarking; use a supported protocol')
    DatasetRegistry.get_class(args.dataset)
    if any(not math.isfinite(rate) or (rate != -1 and rate <= 0) for rate in args.rate):
        raise ValueError('Rate must be -1 (unlimited) or positive')
    if any(number <= 0 for number in args.number):
        raise ValueError('Request counts must be positive integers')
    if args.min_tokens is not None and args.min_tokens < 0:
        raise ValueError('Min Tokens cannot be negative')
    if isinstance(args.max_tokens, int):
        if args.max_tokens <= 0:
            raise ValueError('Max Tokens must be positive')
        if args.min_tokens is not None and args.min_tokens > args.max_tokens:
            raise ValueError('Min Tokens cannot exceed Max Tokens')
    if args.min_prompt_length < 0 or args.max_prompt_length < args.min_prompt_length:
        raise ValueError('Invalid prompt length range')
    if args.dataset == 'random' and not args.tokenizer_path:
        raise ValueError('The random dataset requires tokenizer_path')


def _invoke_aiperf(arguments: AIPerfArguments, task_id: str):
    runtime = _aiperf_runtime()
    if runtime is None:
        return jsonify({'error': 'AIPerf 0.13.0 runtime is unavailable'}), 503
    if arguments.cache_experiment is None:
        return _invoke_single_aiperf(arguments, task_id, runtime)
    try:
        tokenizer = HuggingFacePrefixTokenizer(arguments.tokenizer_path)
        plan = plan_cache_experiment(arguments, task_id, tokenizer.tokenizer_hash, tokenizer.template_hash)
        expected = request.headers.get('EvalScope-Plan-Hash')
        if expected is not None and expected != plan.plan_hash:
            return jsonify({'error': 'Experiment plan changed; validate again'}), 409
        rejection = _required_metrics_preflight(arguments)
        if rejection is not None:
            return rejection
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.mkdir(os.path.join(OUTPUT_DIR, task_id))
    except FileExistsError:
        return jsonify({'error': 'Task ID already exists'}), 409
    except (ValueError, TypeError, OSError, ImportError):
        return jsonify({'error': 'Invalid cache experiment configuration or tokenizer'}), 400
    try:
        output_dir = task_file_path(task_id, 'perf')
        create_log_file(task_id, os.path.join('perf', 'benchmark.log'))
        meta = build_task_info(task_id, 'perf', arguments, arguments.alias, engine='aiperf')
        context = ExecutionContext(
            task_id=task_id, output_dir=output_dir, runtime=runtime, expected_plan_hash=plan.plan_hash
        )
        outcome = run_cache_experiment_in_subprocess(
            context, arguments, task_meta=meta, finalize=lambda result: finish_task(task_id, result.status)
        )
        return json_response(
            PerfInvokeResponse,
            {'status': outcome.status, 'task_id': task_id, 'result': outcome.model_dump(mode='json')},
        )
    except Exception as error:
        finish_task(task_id, 'error')
        logger.error(f'[{task_id}] AIPerf task failed ({type(error).__name__})')
        return jsonify({'status': 'error', 'task_id': task_id, 'error': 'Task failed; check the task log'}), 500


def _required_metrics_preflight(arguments: AIPerfArguments):
    if not arguments.telemetry.required:
        return None
    if not arguments.telemetry.targets:
        return jsonify({'error': 'Required node metrics have no configured targets'}), 400
    try:
        nodes = probe_nodes(arguments.telemetry.targets)
    except (ImportError, RuntimeError):
        return jsonify({'error': 'Metrics preflight is unavailable'}), 503
    if any(node.status != 'available' for node in nodes):
        return jsonify(
            {
                'error': 'Required node metrics preflight failed',
                'nodes': [node.model_dump(mode='json') for node in nodes],
            }
        ), 400
    return None


def _invoke_single_aiperf(arguments: AIPerfArguments, task_id: str, runtime: ExternalRuntime):
    try:
        validate_single_run_configuration(arguments)
        if arguments.input_prompts is None:
            raise ValueError('Single-run inputs are required')
        rejection = _required_metrics_preflight(arguments)
        if rejection is not None:
            return rejection
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.mkdir(os.path.join(OUTPUT_DIR, task_id))
    except FileExistsError:
        return jsonify({'error': 'Task ID already exists'}), 409
    except (ValueError, OSError):
        return jsonify({'error': 'Invalid single-run configuration or inputs'}), 400
    try:
        output_dir = task_file_path(task_id, 'perf')
        create_log_file(task_id, os.path.join('perf', 'benchmark.log'))
        input_file = os.path.join(output_dir, 'single_inputs.jsonl')
        fd = os.open(input_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        input_hash = hashlib.sha256()
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            for prompt in arguments.input_prompts:
                line = json.dumps({'text': prompt, 'output_length': arguments.load.output_token_limit}) + '\n'
                file.write(line)
                input_hash.update(line.encode('utf-8'))
        meta = build_task_info(task_id, 'perf', arguments, arguments.alias, engine='aiperf')
        point = arguments.load.points[0]
        manifest = SingleRunManifest(
            task_id=task_id,
            model=arguments.model,
            alias=arguments.alias,
            endpoint=sanitize_endpoint(arguments.url),
            load_mode=arguments.load.mode,
            concurrency=point.concurrency,
            rate=point.rate,
            planned_requests=arguments.load.request_count,
            output_token_limit=arguments.load.output_token_limit,
            input_sha256=input_hash.hexdigest(),
            telemetry_sample_interval_seconds=arguments.telemetry.sample_interval_seconds
            if arguments.telemetry.targets
            else None,
            telemetry_max_gap_seconds=(
                arguments.telemetry.max_scrape_gap_seconds or 2 * arguments.telemetry.sample_interval_seconds
            )
            if arguments.telemetry.targets
            else None,
        )
        write_single_manifest(output_dir, manifest)
        context = ExecutionContext(task_id=task_id, output_dir=output_dir, runtime=runtime)
        final_status: list[str] = []

        def finalize(outcome: ExecutionOutcome) -> None:
            status = {'completed': 'completed', 'cancelled': 'stopped', 'error': 'error'}[outcome.exit_reason]
            if not outcome.cleanup_confirmed:
                status = 'error'
            normalized = None
            quality = None
            quality_reasons = []
            raw_dir = os.path.realpath(os.path.join(output_dir, 'raw', 'aiperf'))
            valid_raw = (
                outcome.raw_artifact_dir is not None
                and os.path.realpath(outcome.raw_artifact_dir) == raw_dir
                and os.path.commonpath([os.path.realpath(output_dir), raw_dir]) == os.path.realpath(output_dir)
            )
            if (
                outcome.cleanup_confirmed
                and valid_raw
                and os.path.isfile(os.path.join(raw_dir, 'profile_export.jsonl'))
            ):
                try:
                    result = import_aiperf_run(
                        AIPerfImportArguments(
                            raw_dir=raw_dir,
                            normalized_dir=os.path.join(output_dir, 'normalized'),
                            allow_partial_tail=outcome.exit_reason != 'completed',
                            metrics_required=arguments.telemetry.required,
                            targets=[
                                MetricTargetReference(
                                    node_id=target.node_id,
                                    role=target.role,
                                    engine=target.engine,
                                    url=target.url,
                                    labels=target.labels,
                                )
                                for target in arguments.telemetry.targets
                            ],
                        )
                    )
                    normalized, quality, quality_reasons = 'normalized', result.quality, result.quality_reasons
                    if arguments.telemetry.targets:
                        gap = (
                            arguments.telemetry.max_scrape_gap_seconds
                            or 2 * arguments.telemetry.sample_interval_seconds
                        )
                        analysis = analyze_node_metrics(os.path.join(output_dir, 'normalized'), max(1, int(gap * 1e9)))
                        write_experiment_json(Path(output_dir) / 'node_analysis.json', analysis.model_dump(mode='json'))
                        if quality == 'complete':
                            quality = 'partial'
                        quality_reasons = [*quality_reasons, *analysis.quality_reasons]
                        if arguments.telemetry.required and not covers_node_ids(
                            analysis, [target.node_id for target in arguments.telemetry.targets]
                        ):
                            quality = 'invalid'
                            quality_reasons.append('required_telemetry_missing')
                except (OSError, ValueError):
                    if normalized is None:
                        quality_reasons = ['result_import_failed']
                    else:
                        quality_reasons = [*quality_reasons, 'node_analysis_failed']
                        quality = 'invalid' if arguments.telemetry.required else 'partial'
            if status == 'completed' and (normalized is None or quality == 'invalid'):
                status = 'error'
            final_manifest = manifest.model_copy(
                update={
                    'status': status,
                    'artifact_status': outcome.artifact_status,
                    'normalized': normalized,
                    'quality': quality,
                    'quality_reasons': quality_reasons,
                }
            )
            write_single_manifest(output_dir, final_manifest)
            finish_task(task_id, status)
            final_status.append(status)

        outcome = run_single_aiperf_in_subprocess(
            context,
            AIPerfSingleRunRequest(arguments=arguments, input_file=input_file),
            task_meta=meta,
            finalize=finalize,
        )
        status = final_status[0]
        return json_response(
            PerfInvokeResponse,
            {
                'status': status,
                'task_id': task_id,
                'result': outcome.model_dump(mode='json', exclude={'raw_artifact_dir'}),
            },
        )
    except Exception as error:
        finish_task(task_id, 'error')
        logger.error(f'[{task_id}] AIPerf task failed ({type(error).__name__})')
        return jsonify({'status': 'error', 'task_id': task_id, 'error': 'Task failed; check the task log'}), 500


@bp_perf.route('/invoke', methods=['POST'])
def run_performance_test():
    """Run a performance benchmark task (blocking).

    Returns the benchmark result when the task completes.
    """
    data = request.get_json()
    if not isinstance(data, dict) or not data:
        return jsonify({'error': 'Request body must be a JSON object'}), 400
    data = dict(data)

    if 'engine' in data:
        task_id = request.headers.get('EvalScope-Task-Id')
        if not task_id:
            return jsonify({'error': 'EvalScope-Task-Id header is required'}), 400
        try:
            validate_task_id(task_id)
        except ValueError as error:
            return jsonify({'error': str(error)}), 400
        try:
            task_args = parse_perf_task_arguments(data)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid performance configuration'}), 400
        if task_args.engine == 'aiperf':
            return _invoke_aiperf(task_args.aiperf, task_id)
        data = dict(data['native'])

    required_fields = ['model']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'{field} is required'}), 400

    task_id = request.headers.get('EvalScope-Task-Id')
    if not task_id:
        return jsonify({'error': 'EvalScope-Task-Id header is required'}), 400
    try:
        validate_task_id(task_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    # Default to openai API
    if 'api' not in data:
        data['api'] = 'openai'

    requires_url = data['api'] not in ('local', 'local_vllm') or bool(data.get('url'))
    if requires_url:
        try:
            validate_http_endpoint(data.get('url'))
        except ValueError as error:
            return jsonify({'error': str(error)}), 400
        data['url'] = data['url'].strip()

    try:
        perf_args = PerfArguments.from_dict(data)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid performance configuration'}), 400
    try:
        _validate_performance_config(perf_args)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    perf_args.no_timestamp = True
    perf_args.outputs_dir = os.path.join(OUTPUT_DIR, task_id)
    perf_args.name = 'perf'
    perf_args.enable_progress_tracker = True
    perf_args.no_test_connection = True

    logger.info(f'[{task_id}] Running performance benchmark for model: {perf_args.model}')
    logger.info(f'[{task_id}] Endpoint: {sanitize_endpoint(perf_args.url)}')

    create_log_file(task_id, os.path.join('perf', 'benchmark.log'))

    task_meta = build_task_info(task_id, 'perf', perf_args, perf_args.alias)
    try:
        result = run_in_subprocess(run_perf_wrapper, perf_args, task_id=task_id, task_meta=task_meta)
        table_str = _build_perf_table(result, api_type=perf_args.api)
        finish_task(task_id, 'completed')
        logger.info(f'[{task_id}] Task completed successfully')
        return json_response(
            EvalInvokeResponse,
            {'status': 'completed', 'task_id': task_id, 'result': serialize_result(result), 'table': table_str},
        )
    except TaskStoppedError:
        finish_task(task_id, 'stopped')
        logger.info(f'[{task_id}] Task stopped by user.')
        return json_response(EvalInvokeResponse, {'status': 'stopped', 'task_id': task_id})
    except Exception as e:
        finish_task(task_id, 'error')
        logger.error(f'[{task_id}] Task failed: {e}')
        return jsonify({'status': 'error', 'task_id': task_id, 'error': 'Task failed; check the task log'}), 500


@bp_perf.route('/stop', methods=['POST'])
def stop_performance_test():
    """Stop a running performance benchmark task.

    Query params:
        task_id (str): the task identifier
    """
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'error': 'task_id is required'}), 400

    try:
        validate_task_id(task_id)
        stopped = stop_process(task_id)
    except ValueError:
        return jsonify({'error': 'Invalid task_id'}), 400
    if stopped:
        return json_response(TaskStatusResponse, {'status': 'stopped', 'task_id': task_id})
    elif task_is_cancelling(task_id):
        return json_response(TaskStatusResponse, {'status': 'cancelling', 'task_id': task_id}, status=202)
    else:
        return jsonify({'error': f'No running task found for task_id: {task_id}'}), 404


@bp_perf.route('/report', methods=['GET'])
def get_performance_report():
    """Get the HTML performance report for a completed task.

    Query params:
        task_id (str): the task identifier
    """
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'error': 'task_id is required'}), 400

    try:
        validate_task_id(task_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    try:
        report_file = perf_archive.ensure_history_report(OUTPUT_DIR, os.path.join(task_id, 'perf'))
        return _send_safe_perf_report(report_file)
    except PerfArchiveError as e:
        return jsonify({'error': e.message}), e.status


@bp_perf.route('/log', methods=['GET'])
def get_performance_log():
    """Get performance benchmark log content with pagination.

    Query params:
        task_id    (str): the task identifier
        start_line (int, optional): if not provided, read last `page` lines from end
        page       (int): number of lines to read (default 500)

    Returns:
        dict with text, head_line, tail_line, total_lines
    """
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'error': 'task_id is required'}), 400

    start_line = request.args.get('start_line', type=int)
    page = request.args.get('page', 500, type=int)

    try:
        result = get_log_content(task_id, os.path.join('perf', 'benchmark.log'), start_line, page)
        return json_response(LogResponse, result)
    except Exception as e:
        logger.error(f'Failed to get performance log: {str(e)}')
        return jsonify({'error': str(e)}), 500


@bp_perf.route('/progress', methods=['GET'])
def get_performance_progress():
    """Get the real-time hierarchical progress of a running perf benchmark task.

    Query params:
        task_id (str): the task identifier
    """
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'error': 'task_id is required'}), 400

    try:
        validate_task_id(task_id)
    except ValueError:
        return jsonify({'error': 'Invalid task_id'}), 400
    try:
        from .tasks import _aiperf_progress, _with_progress

        active = next((item for item in list_active_tasks() if item['task_id'] == task_id), None)
        saved = read_task_manifest(os.path.join(OUTPUT_DIR, task_id)) or {}
        if (active and active.get('engine') == 'aiperf') or saved.get('engine') == 'aiperf':
            details = _with_progress(active) if active else _aiperf_progress(task_id)
            status = details.get('status') if active else saved.get('status', 'unknown')
            if not active and status not in ('completed', 'stopped', 'error'):
                status = 'unknown'
            if status not in ('running', 'cancelling', 'finalizing', 'completed', 'stopped', 'error'):
                status = 'unknown'
            return json_response(
                ProgressResponse,
                {
                    'percent': 100.0 if status == 'completed' else details.get('percent'),
                    'status': status,
                    **{
                        key: details.get(key)
                        for key in ('phase', 'current_subrun', 'completed_subruns', 'total_subruns', 'quality')
                    },
                },
            )
        progress_file = task_file_path(task_id, os.path.join('perf', 'progress.json'))
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
        return json_response(ProgressResponse, progress)
    except ValueError:
        return jsonify({'error': 'Invalid progress data or path'}), 400
    except FileNotFoundError:
        return json_response(ProgressResponse, {'percent': 0.0})
    except Exception as e:
        logger.error(f'Failed to get progress for task {task_id}: {e}')
        return jsonify({'error': str(e)}), 500


# ------------------------------------------------------------------
# Historical perf-run archive
#
# These endpoints stay thin: request parsing + JSON/error translation only.
# All discovery / view-model / chart / paging logic lives in
# :mod:`evalscope.service.perf_archive`.
# ------------------------------------------------------------------


@bp_perf.route('/list', methods=['GET'])
def list_perf_runs():
    """List historical performance-benchmark runs discovered under the output root.

    Query params:
        root_path (str): output root directory (optional; falls back to config)
    """
    try:
        root = _root_path()
        if not root or not os.path.isdir(root):
            return jsonify({'error': 'root_path is required and must be an existing directory'}), 400

        runs = perf_archive.list_run_summaries(root)
        # The run list exposes its numbers under stable API paths, so the semantics map is keyed
        # by those paths. The run objects themselves are unchanged.
        semantics = resolve_perf_semantics(('best_rps', 'best_latency', 'success_rate'))
        return json_response(
            ListPerfRunsResponse,
            {
                'runs': runs,
                'total': len(runs),
                'metric_semantics': semantics,
            },
        )
    except Exception as e:
        logger.error(f'Failed to list perf runs: {e}')
        return jsonify({'error': str(e)}), 500


@bp_perf.route('/detail', methods=['GET'])
def get_perf_detail():
    """Return native-render metadata for a single perf-run directory.

    Query params:
        root_path (str): output root directory
        path      (str): run directory path relative to root
    """
    rel_path = request.args.get('path')
    if not rel_path:
        return jsonify({'error': 'path is required'}), 400
    try:
        return json_response(PerfDetailResponse, perf_archive.build_run_detail(_root_path(), rel_path))
    except PerfArchiveError as e:
        return jsonify({'error': e.message}), e.status
    except Exception as e:
        logger.error(f'Failed to load perf detail for {rel_path}: {e}')
        return jsonify({'error': str(e)}), 500


@bp_perf.route('/chart', methods=['GET'])
def get_perf_chart():
    """Render a single chart as standalone Plotly HTML for a perf run.

    Query params:
        root_path  (str): output root directory
        path       (str): run directory path relative to root
        chart_type (str): sweep (latency|ttft|tpot|rps|throughput|success) or
                          per-run (percentile_latency|percentile_token|
                          req_latency|req_ttft_tpot|req_tokens|req_success)
        run        (str): required for per-run chart types; the run sub-dir name
        theme      (str): 'dark' (default) or 'light'
    """
    rel_path = request.args.get('path')
    chart_type = request.args.get('chart_type', 'latency')
    run_name = request.args.get('run')
    theme = 'light' if request.args.get('theme') == 'light' else 'dark'
    if not rel_path:
        return jsonify({'error': 'path is required'}), 400
    try:
        html = perf_archive.render_chart(_root_path(), rel_path, chart_type, run_name, theme)
        return html, 200, {'Content-Type': 'text/html'}
    except PerfArchiveError as e:
        return jsonify({'error': e.message}), e.status
    except Exception as e:
        logger.error(f'Failed to render perf chart {chart_type} for {rel_path}: {e}')
        return jsonify({'error': str(e)}), 500


@bp_perf.route('/compare/chart', methods=['GET'])
def get_perf_compare_chart():
    """Overlay a single sweep metric across multiple perf-run directories.

    Query params:
        root_path  (str): output root directory
        paths      (str): ';'-separated run directory paths (relative to root)
        chart_type (str): sweep metric (latency|ttft|tpot|rps|throughput|success)
        theme      (str): 'dark' (default) or 'light'
    """
    paths_raw = request.args.get('paths', '')
    chart_type = request.args.get('chart_type', 'rps')
    theme = 'light' if request.args.get('theme') == 'light' else 'dark'
    rel_paths = [p for p in paths_raw.split(';') if p.strip()]
    if not rel_paths:
        return jsonify({'error': 'paths is required'}), 400
    try:
        html = perf_archive.render_compare_chart(_root_path(), rel_paths, chart_type, theme)
        return html, 200, {'Content-Type': 'text/html'}
    except PerfArchiveError as e:
        return jsonify({'error': e.message}), e.status
    except Exception as e:
        logger.error(f'Failed to render perf compare chart {chart_type}: {e}')
        return jsonify({'error': str(e)}), 500


@bp_perf.route('/runs', methods=['GET'])
def list_perf_run_details():
    """List individual runs (parallel_*/rate_*) within a perf-run directory.

    Query params:
        root_path (str): output root directory
        path      (str): run directory path relative to root
    """
    rel_path = request.args.get('path')
    if not rel_path:
        return jsonify({'error': 'path is required'}), 400
    try:
        items = perf_archive.list_run_items(_root_path(), rel_path)
        return json_response(PerfRunsListResponse, {'runs': items, 'total': len(items)})
    except PerfArchiveError as e:
        return jsonify({'error': e.message}), e.status
    except Exception as e:
        logger.error(f'Failed to list perf run details for {rel_path}: {e}')
        return jsonify({'error': str(e)}), 500


@bp_perf.route('/requests', methods=['GET'])
def get_perf_requests():
    """Return paginated per-request records (from benchmark_data.db) for one run.

    Query params:
        root_path (str): output root directory
        path      (str): run directory path relative to root
        run       (str): the run sub-dir name (parallel_*/rate_*)
        status    (str): optional 'success' | 'failed' filter
        page      (int): 1-based page (default 1)
        page_size (int): rows per page (default 50, max 500)
    """
    rel_path = request.args.get('path')
    run_name = request.args.get('run')
    if not rel_path or not run_name:
        return jsonify({'error': 'path and run are required'}), 400
    try:
        result = perf_archive.query_request_page(
            _root_path(),
            rel_path,
            run_name,
            request.args.get('status'),
            request.args.get('page', 1, type=int),
            request.args.get('page_size', 50, type=int),
        )
        return json_response(PerfRequestsResponse, result)
    except PerfArchiveError as e:
        return jsonify({'error': e.message}), e.status
    except Exception as e:
        logger.error(f'Failed to load perf requests for {rel_path}/{run_name}: {e}')
        return jsonify({'error': str(e)}), 500


@bp_perf.route('/history/report', methods=['GET'])
def get_perf_history_report():
    """Serve (or lazily generate) the full HTML report for a historical perf run.

    Query params:
        root_path (str): output root directory
        path      (str): run directory path relative to root
    """
    rel_path = request.args.get('path')
    if not rel_path:
        return jsonify({'error': 'path is required'}), 400
    try:
        report_file = perf_archive.ensure_history_report(_root_path(), rel_path)
        return _send_safe_perf_report(report_file)
    except PerfArchiveError as e:
        return jsonify({'error': e.message}), e.status
    except Exception as e:
        logger.error(f'Failed to serve perf history report for {rel_path}: {e}')
        return jsonify({'error': str(e)}), 500


@bp_perf.route('/run', methods=['DELETE'])
def delete_perf_run():
    """Delete a historical perf-run directory under the output root.

    Query params:
        root_path (str): output root directory (optional; falls back to config)
        path      (str): run directory path relative to root

    Returns 409 when the run belongs to a task that is still executing.
    """
    rel_path = request.args.get('path')
    if not rel_path:
        return jsonify({'error': 'path is required'}), 400

    # Running-task protection: in the service layout the run path is
    # ``<task_id>/perf``, so refuse deletion while that task is still active.
    segments = set(rel_path.replace('\\', '/').strip('/').split('/'))
    running = segments & active_task_ids()
    if running:
        return jsonify({'error': f'Task is still running: {sorted(running)[0]}'}), 409

    try:
        perf_archive.delete_run(_root_path(), rel_path)
        return json_response(DeletePerfRunResponse, {'success': True, 'path': rel_path})
    except PerfArchiveError as e:
        return jsonify({'error': e.message}), e.status
    except Exception as e:
        logger.error(f'Failed to delete perf run {rel_path}: {e}')
        return jsonify({'error': str(e)}), 500
