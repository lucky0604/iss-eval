"""POST /perf/import archives AIPerf artifacts, reading identity from the summary."""

import json
import shutil
from pathlib import Path

import pytest

from evalscope.service.app import create_app
from evalscope.service.blueprints import perf
from evalscope.service.utils import log

FIXTURES = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'aiperf'
_START = 1793613173340081000
_COPIED = ('profile_export_aiperf.json', 'profile_export.jsonl', 'phase_manifest.json')


def _gpu_telemetry() -> str:
    return ''.join(
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
    )


def _raw_artifacts(base: Path, name: str = 'aiperf_run') -> Path:
    raw = base / name
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.mkdir()
    for item in _COPIED:
        shutil.copyfile(FIXTURES / item, raw / item)
    (raw / 'gpu_telemetry_export.jsonl').write_text(_gpu_telemetry(), encoding='utf-8')
    return raw


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(perf, 'OUTPUT_DIR', str(tmp_path))
    return create_app(outputs=str(tmp_path)).test_client()


def test_import_reads_identity_from_the_artifacts(client, tmp_path: Path) -> None:
    raw = _raw_artifacts(tmp_path.parent / f'{tmp_path.name}_raw')
    response = client.post('/api/v1/perf/import', json={'raw_dir': str(raw)})
    assert response.status_code == 201, response.json

    payload = response.json
    assert payload['root_path'] == str(tmp_path)
    assert payload['failed'] == []
    assert len(payload['imported']) == 1
    run = payload['imported'][0]
    assert run['path'] == f"imports/{run['task_id']}/perf"
    assert run['source_dir'] == '.'
    assert run['model'] == 'mock-model'
    assert run['load_mode'] == 'closed_loop'
    assert run['concurrency'] == 1
    assert run['profiling_requests'] == 2
    assert run['quality'] in {'complete', 'partial'}
    assert {'model', 'load_mode', 'concurrency', 'output_token_limit'} <= set(run['inferred_fields'])

    archive = tmp_path / 'imports' / run['task_id'] / 'perf'
    assert (archive / 'single_run.json').is_file()
    assert list((archive / 'normalized').rglob('requests.db'))
    manifest = json.loads((archive / 'single_run.json').read_text(encoding='utf-8'))
    assert manifest['model'] == 'mock-model'
    assert manifest['concurrency'] == 1

    listed = client.get('/api/v1/perf/list', query_string={'root_path': str(tmp_path)})
    assert listed.status_code == 200
    assert any(item['path'] == run['path'] for item in listed.json['runs'])

    detail = client.get('/api/v1/perf/detail', query_string={'root_path': str(tmp_path), 'path': run['path']})
    assert detail.status_code == 200, detail.json
    assert detail.json['node_analysis'] is not None

    html = client.get('/api/v1/perf/history/report', query_string={'root_path': str(tmp_path), 'path': run['path']})
    assert html.status_code == 200, html.data[:200]
    assert b'<' in html.data

    raw_list = client.get('/api/v1/perf/raw', query_string={'root_path': str(tmp_path), 'path': run['path']})
    assert raw_list.status_code == 200
    assert any(item['name'] == 'profile_export.jsonl' for item in raw_list.json['files'])
    download = client.get(
        '/api/v1/perf/raw/download',
        query_string={'root_path': str(tmp_path), 'path': run['path'], 'name': 'profile_export.jsonl'},
    )
    assert download.status_code == 200 and download.data.startswith(b'{')
    assert client.get(
        '/api/v1/perf/raw/download',
        query_string={'root_path': str(tmp_path), 'path': run['path'], 'name': '../../../etc/passwd'},
    ).status_code == 404

    duplicate = client.post(
        '/api/v1/perf/import',
        json={'raw_dir': str(raw), 'task_id': run['task_id']},
    )
    assert duplicate.status_code == 409, duplicate.json


def test_import_archives_every_run_under_a_batch_directory(client, tmp_path: Path) -> None:
    batch = tmp_path.parent / f'{tmp_path.name}_batch'
    names = ('conc_1_inputlen_8192_osl_1024', 'conc_2_inputlen_8192_osl_1024')
    for name in names:
        _raw_artifacts(batch / 'exports', name)

    response = client.post('/api/v1/perf/import', json={'raw_dir': str(batch)})
    assert response.status_code == 201, response.json

    payload = response.json
    assert payload['failed'] == []
    assert {run['source_dir'] for run in payload['imported']} == {f'exports/{name}' for name in names}
    assert len({run['task_id'] for run in payload['imported']}) == 2
    for run in payload['imported']:
        assert (tmp_path / 'imports' / run['task_id'] / 'perf' / 'single_run.json').is_file()


def test_import_rejects_conflicting_identity_and_invalid_requests(client, tmp_path: Path) -> None:
    raw = _raw_artifacts(tmp_path.parent / f'{tmp_path.name}_raw')

    conflict = client.post('/api/v1/perf/import', json={'raw_dir': str(raw), 'model': 'other-model'})
    assert conflict.status_code == 400
    assert 'conflicts with the artifacts' in conflict.json['error']

    base = {'raw_dir': str(raw)}
    assert client.post('/api/v1/perf/import', json={**base, 'concurrency': 8, 'rate': 1.0}).status_code == 400
    assert client.post('/api/v1/perf/import', json={**base, 'load_mode': 'open_loop', 'concurrency': 8}).status_code == 400
    assert client.post('/api/v1/perf/import', json={**base, 'task_id': '..'}).status_code == 400
    assert client.post('/api/v1/perf/import', json={**base, 'raw_dir': '/does/not/exist'}).status_code == 400

    batch = tmp_path.parent / f'{tmp_path.name}_batch_reject'
    for name in ('run_a', 'run_b'):
        _raw_artifacts(batch, name)
    assert client.post('/api/v1/perf/import', json={'raw_dir': str(batch), 'task_id': 'one'}).status_code == 400
