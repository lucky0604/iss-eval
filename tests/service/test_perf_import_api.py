"""POST /perf/import turns a local AIPerf artifact directory into an archive."""

import json
import shutil
from pathlib import Path

import pytest

from evalscope.service.app import create_app
from evalscope.service.blueprints import perf
from evalscope.service.utils import log

FIXTURES = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf'
_START = 1793613173340081000


def _raw_artifacts(base: Path) -> Path:
    raw = base / 'aiperf_run'
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.mkdir()
    for name in ('profile_export_aiperf.json', 'profile_export.jsonl', 'phase_manifest.json'):
        shutil.copyfile(FIXTURES / name, raw / name)
    (raw / 'gpu_telemetry_export.jsonl').write_text(
        ''.join(
            json.dumps(
                {
                    'timestamp_ns': _START + offset,
                    'gpu_index': 0,
                    'gpu_uuid': 'GPU-0',
                    'gpu_model_name': 'NVIDIA RTX 6000D',
                    'pci_bus_id': '00000000:00:00.0',
                    'device': 'nvidia0',
                    'hostname': 'head01-04',
                    'telemetry_source_url': 'pynvml://localhost',
                    'telemetry_data': {
                        'nvidia_gpu_utilization': 88.0,
                        'nvidia_memory_used': 49.5,
                        'nvidia_power_usage': 300.5,
                    },
                }
            )
            + '\n'
            for offset in (1_000_000, 2_000_000)
        ),
        encoding='utf-8',
    )
    return raw


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    return create_app(outputs=str(tmp_path)).test_client()


def test_import_creates_a_discoverable_single_run_archive(client, tmp_path: Path) -> None:
    raw = _raw_artifacts(tmp_path.parent / f'{tmp_path.name}_raw')
    response = client.post(
        '/api/v1/perf/import',
        json={
            'raw_dir': str(raw),
            'model': 'GLM-5.3-Flash-NVFP4',
            'load_mode': 'closed_loop',
            'concurrency': 8,
            'output_token_limit': 1024,
        },
    )
    assert response.status_code == 201, response.json
    payload = response.json
    assert payload['root_path'] == str(tmp_path)
    assert payload['path'] == f"imports/{payload['task_id']}/perf"
    assert payload['quality'] in {'complete', 'partial'}
    assert payload['profiling_requests'] > 0

    archive = tmp_path / 'imports' / payload['task_id'] / 'perf'
    assert (archive / 'single_run.json').is_file()
    assert (archive / 'normalized').is_dir()
    assert list((archive / 'normalized').rglob('requests.db'))

    listed = client.get('/api/v1/perf/list', query_string={'root_path': str(tmp_path)})
    assert listed.status_code == 200
    assert any(run['path'] == payload['path'] for run in listed.json['runs'])

    detail = client.get(
        '/api/v1/perf/detail', query_string={'root_path': str(tmp_path), 'path': payload['path']}
    )
    assert detail.status_code == 200, detail.json
    assert detail.json['node_analysis'] is not None

    html = client.get(
        '/api/v1/perf/history/report', query_string={'root_path': str(tmp_path), 'path': payload['path']}
    )
    assert html.status_code == 200, html.data[:200]
    assert b'<' in html.data

    raw_list = client.get('/api/v1/perf/raw', query_string={'root_path': str(tmp_path), 'path': payload['path']})
    assert raw_list.status_code == 200
    assert any(item['name'] == 'profile_export.jsonl' for item in raw_list.json['files'])
    download = client.get(
        '/api/v1/perf/raw/download',
        query_string={'root_path': str(tmp_path), 'path': payload['path'], 'name': 'profile_export.jsonl'},
    )
    assert download.status_code == 200 and download.data.startswith(b'{')
    assert client.get(
        '/api/v1/perf/raw/download',
        query_string={'root_path': str(tmp_path), 'path': payload['path'], 'name': '../../../etc/passwd'},
    ).status_code == 404

    duplicate = client.post(
        '/api/v1/perf/import',
        json={
            'raw_dir': str(raw),
            'task_id': payload['task_id'],
            'model': 'GLM-5.3-Flash-NVFP4',
            'load_mode': 'closed_loop',
            'concurrency': 8,
            'output_token_limit': 1024,
        },
    )
    assert duplicate.status_code == 409, duplicate.json


def test_import_rejects_invalid_requests_and_path_traversal(client, tmp_path: Path) -> None:
    raw = _raw_artifacts(tmp_path.parent / f'{tmp_path.name}_raw2')
    base = {'raw_dir': str(raw), 'model': 'm', 'output_token_limit': 1024}
    assert client.post('/api/v1/perf/import', json=base).status_code == 400
    assert client.post('/api/v1/perf/import', json={**base, 'concurrency': 8, 'rate': 1.0}).status_code == 400
    assert client.post('/api/v1/perf/import', json={**base, 'load_mode': 'open_loop'}).status_code == 400
    assert client.post('/api/v1/perf/import', json={**base, 'concurrency': 8, 'task_id': '..'}).status_code == 400
    assert client.post('/api/v1/perf/import', json={**base, 'concurrency': 8, 'raw_dir': '/does/not/exist'}).status_code == 400
