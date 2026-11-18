"""AIPerf GPU telemetry and DCGM Prometheus import coverage."""

import json
import shutil
from pathlib import Path

import pytest

from evalscope.perf.integrations.aiperf.metrics import analyze_node_metrics
from evalscope.perf.results.aiperf_import import import_aiperf_run, query_node_metric_series, query_node_metrics
from evalscope.perf.results.models import AIPerfImportArguments, NodeMetricSeriesQuery

FIXTURES = Path(__file__).parent / 'fixtures' / 'aiperf'
_PROFILING_START_NS = 1793613173340081000
_PROFILING_END_NS = 1793613173343018000


def _base_artifacts(raw: Path, nested: bool = False) -> None:
    target = raw / 'exports' / 'conc_1' if nested else raw
    target.mkdir(parents=True, exist_ok=True)
    for name in ('profile_export_aiperf.json', 'profile_export.jsonl', 'phase_manifest.json'):
        shutil.copyfile(FIXTURES / name, target / name)


def _gpu_telemetry_line(timestamp_ns: int, index: int = 0) -> dict:
    return {
        'timestamp_ns': timestamp_ns,
        'gpu_index': index,
        'gpu_uuid': f'GPU-{index}',
        'gpu_model_name': 'NVIDIA RTX 6000D',
        'pci_bus_id': f'00000000:0{index}:00.0',
        'device': f'nvidia{index}',
        'hostname': 'head01-04',
        'telemetry_data': {
            'gpu_power_usage': 300.5,
            'energy_consumption': 12.5,
            'gpu_utilization': 88.0,
            'gpu_memory_used': 49.5,
            'gpu_memory_free': 1.5,
            'gpu_memory_total': 51.0,
            'memory_temperature': 45.0,
            'gpu_temperature': 62.0,
            'memory_copy_utilization': 70.0,
            'xid_errors': 0.0,
            'sm_clock_frequency': 2100.0,
            'memory_clock_frequency': 9500.0,
        },
        'telemetry_source_url': 'http://localhost:9400/metrics',
    }


def _write_gpu_telemetry(raw: Path, lines: list[dict], nested: str = '') -> None:
    directory = raw / nested if nested else raw
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'gpu_telemetry_export.jsonl').write_text(
        ''.join(json.dumps(line) + '\n' for line in lines), encoding='utf-8'
    )


def _dcgm_payload(metric_name: str, values: list[tuple[float, str]]) -> dict:
    return {
        'status': 'success',
        'data': {
            'resultType': 'matrix',
            'result': [
                {
                    'metric': {
                        'gpu': '0',
                        'UUID': 'GPU-0',
                        'device': 'nvidia0',
                        'modelName': 'NVIDIA RTX 6000D',
                        'hostname': 'head01-04',
                    },
                    'values': [[epoch, value] for epoch, value in values],
                }
            ],
        },
    }


def _write_dcgm(raw: Path, metric_name: str, payload: dict) -> None:
    directory = raw / 'dcgm_prometheus'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{metric_name}.json').write_text(json.dumps(payload), encoding='utf-8')


def _import(raw: Path, normalized: Path):
    return import_aiperf_run(AIPerfImportArguments(raw_dir=str(raw), normalized_dir=str(normalized)))


def test_imports_gpu_telemetry_and_dcgm_prometheus(tmp_path: Path) -> None:
    raw, normalized = tmp_path / 'raw', tmp_path / 'normalized'
    _base_artifacts(raw)
    _write_gpu_telemetry(
        raw,
        [
            _gpu_telemetry_line(_PROFILING_START_NS + 1_000_000, 0),
            _gpu_telemetry_line(_PROFILING_START_NS + 2_000_000, 1),
        ],
    )
    _write_dcgm(
        raw,
        'DCGM_FI_DEV_GPU_UTIL',
        _dcgm_payload(
            'DCGM_FI_DEV_GPU_UTIL',
            [(1793613173.341, '88.0'), (1793613173.342, '91.0')],
        ),
    )

    run = _import(raw, normalized)
    assert run.quality_reasons.count('gpu_telemetry_invalid') == 0

    total, samples = query_node_metrics(str(normalized), limit=500)
    assert total == len(samples) > 0
    by_name = {sample.metric_name: sample for sample in samples}
    assert by_name['dcgm:DCGM_FI_DEV_GPU_UTIL'].engine == 'dcgm'
    assert by_name['dcgm:DCGM_FI_DEV_GPU_UTIL'].role == 'gpu'
    utilization = [
        sample.observation['value'] for sample in samples if sample.metric_name == 'dcgm:DCGM_FI_DEV_GPU_UTIL'
    ]
    assert 88.0 in utilization
    assert 91.0 in utilization
    assert by_name['dcgm:DCGM_FI_DEV_FB_USED'].observation['value'] == pytest.approx(49.5 * 1024)
    assert by_name['dcgm:DCGM_FI_DEV_POWER_USAGE'].observation['value'] == pytest.approx(300.5)
    assert by_name['dcgm:DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION'].metric_type == 'counter'
    assert by_name['dcgm:DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION'].observation['value'] == pytest.approx(12_500_000.0)
    assert by_name['dcgm:DCGM_FI_DEV_GPU_UTIL'].labels['source'] in {'aiperf_gpu_telemetry', 'dcgm_prometheus'}

    analysis = analyze_node_metrics(str(normalized), max_gap_ns=2_000_000)
    gpu_series = [row for row in analysis.series if row.engine == 'dcgm' and row.role == 'gpu']
    assert gpu_series
    assert any(row.metric.key == 'gpu_utilization' and row.statistic is not None for row in gpu_series)
    assert 'gpu_telemetry_source_unverified' in analysis.quality_reasons

    chosen = next(sample for sample in samples if sample.metric_name == 'dcgm:DCGM_FI_DEV_GPU_UTIL')
    plotted = query_node_metric_series(
        str(normalized),
        NodeMetricSeriesQuery(
            node_id=chosen.node_id,
            metric_name=chosen.metric_name,
            labels=chosen.labels,
            max_points=50,
        ),
    )
    assert plotted.total >= 1
    assert all(sample.metric_name == 'dcgm:DCGM_FI_DEV_GPU_UTIL' for sample in plotted.samples)


def test_recursive_artifact_discovery_finds_nested_exports(tmp_path: Path) -> None:
    raw, normalized = tmp_path / 'raw', tmp_path / 'normalized'
    _base_artifacts(raw, nested=True)
    _write_gpu_telemetry(raw, [_gpu_telemetry_line(_PROFILING_START_NS + 1_000_000)], nested='exports/conc_1')

    run = _import(raw, normalized)
    assert run.profiling_requests > 0
    total, samples = query_node_metrics(str(normalized), limit=50)
    assert total > 0
    assert all(sample.engine == 'dcgm' for sample in samples)


def test_invalid_gpu_telemetry_keeps_the_client_run_importable(tmp_path: Path) -> None:
    raw, normalized = tmp_path / 'raw', tmp_path / 'normalized'
    _base_artifacts(raw)
    (raw / 'gpu_telemetry_export.jsonl').write_text('{"timestamp_ns": 1}\n', encoding='utf-8')

    run = _import(raw, normalized)
    assert run.profiling_requests > 0
    assert 'gpu_telemetry_invalid' in run.quality_reasons
    total, samples = query_node_metrics(str(normalized), limit=50)
    assert total == 0
    assert samples == []


def test_gpu_and_server_metric_samples_share_the_node_sample_index(tmp_path: Path) -> None:
    raw, normalized = tmp_path / 'raw', tmp_path / 'normalized'
    _base_artifacts(raw)
    shutil.copyfile(FIXTURES / 'server_metrics_export.json', raw / 'server_metrics_export.json')
    shutil.copyfile(FIXTURES / 'multi_server_metrics_export.jsonl', raw / 'server_metrics_export.jsonl')
    _write_gpu_telemetry(raw, [_gpu_telemetry_line(_PROFILING_START_NS + 1_000_000)])

    _import(raw, normalized)
    total, samples = query_node_metrics(str(normalized), limit=500)
    assert total > 0
    assert 'dcgm' in {sample.engine for sample in samples}
