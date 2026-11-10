"""Web API for portable plans: preview, export, submit, idempotency and archive."""

import json
import os
from pathlib import Path

import pytest

flask = pytest.importorskip('flask')

from evalscope.perf.plans import compile_plan_text
from evalscope.service.app import create_app
from evalscope.service.perf_plans import build_preview
from evalscope.service.utils import log

FIXTURES = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'perf_plan'
TASK_ID = 'plan-task'


def _text(name: str = 'managed-small.json') -> str:
    return (FIXTURES / name).read_text()


def _document(name: str = 'managed-small.json') -> dict:
    return json.loads(_text(name))


@pytest.fixture
def outputs(tmp_path: Path):
    root = tmp_path / 'outputs'
    root.mkdir()
    return root


@pytest.fixture
def client(outputs: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(outputs))
    app = create_app(outputs=str(outputs))
    return app, app.test_client()


def _acks(http, text: str) -> list[str]:
    preview = http.post('/api/v1/perf/plans/preview', json={'content': text})
    assert preview.status_code == 200
    return preview.json['requires_acknowledgement']


def _plan_hash(http, text: str) -> str:
    preview = http.post('/api/v1/perf/plans/preview', json={'content': text})
    assert preview.status_code == 200
    return preview.json['plan_hash']


def _invoke(http, text: str, *, task_id: str = TASK_ID, acknowledge: bool = True, plan_hash: str | None = None):
    body = {'content': text, 'expected_plan_hash': plan_hash or _plan_hash(http, text)}
    if acknowledge:
        body['acknowledged'] = _acks(http, text)
    return http.post(
        '/api/v1/perf/plans/invoke?async=1',
        headers={'EvalScope-Task-Id': task_id},
        json=body,
    )


def _fake_aiperf(tmp_path: Path) -> Path:
    script = tmp_path / 'fake-aiperf'
    script.write_text('#!/usr/bin/env python3\nimport sys\nif "--version" in sys.argv:\n    print("0.13.0")\n')
    script.chmod(0o755)
    return script


class _Queue:
    """Minimal task queue double that records submissions."""

    def __init__(self, *, fail_first: Exception | None = None) -> None:
        self.jobs: list[tuple[str, object]] = []
        self.fail_first = fail_first

    def submit(self, task_id, kind, meta, job):
        if self.fail_first is not None:
            error, self.fail_first = self.fail_first, None
            raise error
        self.jobs.append((task_id, job))
        return {'enqueued_at': '2026-01-01T00:00:00+00:00', 'queue_position': 0}

    def get(self, task_id):
        for stored, _ in self.jobs:
            if stored == task_id:
                return {'status': 'queued'}
        return None


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch):
    from evalscope.service.blueprints import perf as perf_blueprint

    double = _Queue()
    monkeypatch.setattr(perf_blueprint, 'get_task_queue', lambda: double)
    return double


def test_preview_is_bounded_and_explains_its_basis(client) -> None:
    _, http = client
    response = http.post('/api/v1/perf/plans/preview?limit=1', json={'content': _text()})
    assert response.status_code == 200
    body = response.json
    assert body['total_runs'] == 2
    assert len(body['runs']) == 1
    assert body['limit'] == 1 and body['offset'] == 0
    assert body['workload_semantics'] == 'managed_token_prefix'
    assert body['runs'][0]['message_structure'] == 'user_only'
    assert body['budgets']['formal_requests'] == 32
    assert body['alignment']['overall'] == 'unknown'
    assert {item['dimension'] for item in body['alignment']['dimensions']} == {
        'parameters',
        'request_semantics',
        'cache',
        'environment',
        'version',
        'statistics',
    }
    assert body['engine_available'] is False
    assert body['can_submit'] is True
    assert all(item['status'] == 'fits' for item in body['context'])
    text = response.get_data(as_text=True)
    assert 'api_key' not in text
    assert 'fixture-key' not in text


def test_preview_rejects_invalid_pagination_and_credentials(client) -> None:
    _, http = client
    assert http.post('/api/v1/perf/plans/preview?limit=0', json={'content': _text()}).status_code == 400
    document = _document()
    document['connection']['api_key'] = 'sk-secret'
    response = http.post('/api/v1/perf/plans/preview', json={'content': json.dumps(document)})
    assert response.status_code == 400
    assert response.json['diagnostics'][0]['code'] == 'credential_in_document'
    assert 'sk-secret' not in response.get_data(as_text=True)


def test_preview_rejects_required_telemetry_as_an_unsupported_field(client) -> None:
    """F05: a required collection is refused, never silently downgraded."""
    _, http = client
    document = _document()
    document['telemetry'] = {'required': True, 'targets': []}
    response = http.post('/api/v1/perf/plans/preview', json={'content': json.dumps(document)})
    assert response.status_code == 400
    diagnostic = response.json['diagnostics'][0]
    assert diagnostic['code'] == 'unsupported_feature'
    assert diagnostic['field_path'] == 'telemetry.required'
    assert 'required=true' in diagnostic['message']


def test_oversized_document_returns_413(client) -> None:
    _, http = client
    response = http.post('/api/v1/perf/plans/preview', json={'content': ' ' * (1024 * 1024 + 1)})
    assert response.status_code == 413


def test_exported_plan_reimports_to_the_same_identity(client) -> None:
    _, http = client
    exported = http.post('/api/v1/perf/plans/export', json={'content': _text()})
    assert exported.status_code == 200
    body = exported.json
    preview = http.post('/api/v1/perf/plans/preview', json={'content': body['content']})
    assert preview.status_code == 200
    assert preview.json['plan_hash'] == body['plan_hash']
    assert preview.json['normalized_hash'] == body['normalized_hash']
    assert body['content'].endswith('\n')


def test_invoke_without_a_runtime_is_503(client) -> None:
    _, http = client
    response = _invoke(http, _text())
    assert response.status_code == 503
    assert not (Path(log.OUTPUT_DIR) / TASK_ID).exists()


def test_invoke_requires_the_previewed_plan_hash(client, tmp_path: Path) -> None:
    """S02: a submission without the previewed hash is unbound and refused."""
    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    response = http.post(
        '/api/v1/perf/plans/invoke?async=1',
        headers={'EvalScope-Task-Id': TASK_ID},
        json={'content': _text(), 'acknowledged': _acks(http, _text())},
    )
    assert response.status_code == 400
    assert response.json['plan_hash'] == _plan_hash(http, _text())
    assert not (Path(log.OUTPUT_DIR) / TASK_ID).exists()


def test_invoke_blocks_context_overflow_even_with_acknowledgements(client, tmp_path: Path) -> None:
    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    response = _invoke(http, _text('legacy-context-rejected.json'))
    assert response.status_code == 400
    codes = {item['code'] for item in response.json['diagnostics']}
    assert 'context_overflow' in codes
    assert not (Path(log.OUTPUT_DIR) / TASK_ID).exists()


def test_warmup_context_overflow_blocks_submission(client, tmp_path: Path) -> None:
    """F04: the ordinary warmup is its own context requirement."""
    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    document = _document()
    document['deployment']['declared_max_model_len'] = 8192
    document['warmup'] = {
        'enabled': True,
        'input_tokens': 16000,
        'output_token_limit': 1000,
        'request_count': 1,
        'concurrency': 1,
    }
    text = json.dumps(document)
    preview = http.post('/api/v1/perf/plans/preview', json={'content': text})
    assert preview.status_code == 200
    warmup = next(item for item in preview.json['context'] if item['run_id'] == 'warmup')
    assert warmup['status'] == 'overflow' and warmup['required_tokens'] == 17000
    response = _invoke(http, text)
    assert response.status_code == 400
    assert {item['code'] for item in response.json['diagnostics']} >= {'context_overflow'}
    assert not (Path(log.OUTPUT_DIR) / TASK_ID).exists()


def test_warnings_require_an_acknowledgement_bound_to_the_plan_hash(client, tmp_path: Path, queue) -> None:
    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    document = _document()
    document['intent'] = 'diagnostic'
    document['load']['request_count'] = 2
    document['load']['points'] = [{'concurrency': 8}]
    text = json.dumps(document)
    required = _acks(http, text)
    assert 'request_count_below_concurrency' in required

    rejected = _invoke(http, text, acknowledge=False)
    assert rejected.status_code == 400
    assert rejected.json['missing_acknowledgements'] == required

    # An acknowledgement from an older preview cannot authorize an edited plan.
    edited = json.loads(text)
    edited['load']['points'] = [{'concurrency': 16}]
    stale = http.post(
        '/api/v1/perf/plans/invoke?async=1',
        headers={'EvalScope-Task-Id': TASK_ID},
        json={'content': json.dumps(edited), 'acknowledged': required, 'expected_plan_hash': _plan_hash(http, text)},
    )
    assert stale.status_code == 409
    assert not (Path(log.OUTPUT_DIR) / TASK_ID).exists()

    accepted = _invoke(http, text)
    assert accepted.status_code == 202
    assert accepted.json['state'] == 'queued'
    assert queue.jobs[0][0] == TASK_ID


def test_submit_enqueues_the_compiled_plan_and_reserves_the_identity(client, tmp_path: Path, queue) -> None:
    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    response = _invoke(http, _text())
    assert response.status_code == 202
    assert response.json['state'] == 'queued' and response.json['duplicate'] is False

    task_id, job = queue.jobs[0]
    assert task_id == TASK_ID
    assert job.entrypoint == 'perf_plan'
    plan = job.args[2].plan
    assert plan.plan_hash == compile_plan_text(_text()).plan_hash
    task_dir = Path(log.OUTPUT_DIR) / TASK_ID
    assert (task_dir / 'perf' / 'benchmark.log').is_file()
    assert (task_dir / 'perf' / 'plan' / 'compiled.json').is_file()
    assert (task_dir / 'perf' / 'plan' / 'submission.json').is_file()
    submission = json.loads((task_dir / 'perf' / 'plan' / 'submission.json').read_text())
    assert submission['state'] == 'queued' and submission['plan_hash'] == plan.plan_hash


def test_plan_hash_mismatch_is_409(client, tmp_path: Path, queue) -> None:
    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    response = _invoke(http, _text(), plan_hash='a' * 64)
    assert response.status_code == 409
    assert not (Path(log.OUTPUT_DIR) / TASK_ID).exists()
    assert queue.jobs == []


def test_same_task_id_and_plan_is_idempotent_but_a_different_plan_conflicts(
    client, tmp_path: Path, queue
) -> None:
    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    first = _invoke(http, _text())
    assert first.status_code == 202

    duplicate = _invoke(http, _text())
    assert duplicate.status_code == 202
    assert duplicate.json['duplicate'] is True
    assert len(queue.jobs) == 1

    document = _document()
    document['workload']['target_ratios'] = [0.5]
    conflict = _invoke(http, json.dumps(document))
    assert conflict.status_code == 409


def test_enqueue_failure_keeps_the_identity_retryable(client, tmp_path: Path, monkeypatch) -> None:
    """F01: a definite enqueue failure must not leave a task id that always 409s."""
    from evalscope.service.blueprints import perf as perf_blueprint
    from evalscope.service.utils.queue_config import QueueUnavailableError

    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    double = _Queue(fail_first=QueueUnavailableError('redis down'))
    monkeypatch.setattr(perf_blueprint, 'get_task_queue', lambda: double)

    failed = _invoke(http, _text())
    assert failed.status_code == 503
    assert failed.json['retryable'] is True
    submission_path = Path(log.OUTPUT_DIR) / TASK_ID / 'perf' / 'plan' / 'submission.json'
    assert json.loads(submission_path.read_text())['state'] == 'failed'

    retried = _invoke(http, _text())
    assert retried.status_code == 202
    assert retried.json['state'] == 'queued'
    assert json.loads(submission_path.read_text())['state'] == 'queued'
    assert len(double.jobs) == 1


def test_archive_endpoint_reads_the_canonical_layout(client, tmp_path: Path, queue) -> None:
    from evalscope.perf.plans.execution import PerfPlanManifest

    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    assert _invoke(http, _text()).status_code == 202

    plan = compile_plan_text(_text())
    manifest_path = Path(log.OUTPUT_DIR) / TASK_ID / 'perf' / 'plan' / 'manifest.json'
    manifest_path.write_text(
        PerfPlanManifest(
            plan_hash=plan.plan_hash,
            normalized_hash=plan.normalized_hash,
            semantic_fingerprint=plan.semantic_fingerprint,
            workload_semantics=plan.workload_semantics,
            status='stopped',
            quality='partial',
            completed_runs=1,
            total_runs=plan.run_count,
        ).model_dump_json()
    )

    response = http.get(f'/api/v1/perf/plans/{TASK_ID}')
    assert response.status_code == 200
    body = response.json
    assert body['plan_hash'] == plan.plan_hash
    assert body['state'] == 'stopped'
    assert body['status'] == 'stopped'
    assert body['quality'] == 'partial'
    assert body['completed_runs'] == 1 and body['total_runs'] == plan.run_count
    assert len(body['runs']) == plan.run_count
    assert body['runs'][0]['input_tokens'] == plan.runs[0].input_tokens
    assert body['runs'][0]['status'] == 'not_started'
    assert 'executable' not in response.get_data(as_text=True)

    report = http.get(f'/api/v1/perf/plans/{TASK_ID}/report')
    assert report.status_code == 200
    html = report.get_data(as_text=True)
    assert 'not_started' in html and plan.plan_hash in html


def test_plan_report_is_readable_from_an_explicit_archive_path(outputs: Path) -> None:
    """A non-service root can open its own plan through ``root_path`` + ``path``."""
    from evalscope.perf.plans.execution import PerfPlanManifest
    from evalscope.perf.plans.provenance import plan_dir, write_plan_archive

    external = outputs / 'external'
    perf_root = external / 'run-a' / 'perf'
    perf_root.mkdir(parents=True)
    plan = compile_plan_text(_text())
    write_plan_archive(perf_root, plan, source_text=_text())
    (plan_dir(perf_root) / 'manifest.json').write_text(
        PerfPlanManifest(
            plan_hash=plan.plan_hash,
            normalized_hash=plan.normalized_hash,
            semantic_fingerprint=plan.semantic_fingerprint,
            workload_semantics=plan.workload_semantics,
            status='completed',
            quality='complete',
            completed_runs=plan.run_count,
            total_runs=plan.run_count,
        ).model_dump_json()
    )
    app = create_app(outputs=str(outputs))
    http = app.test_client()
    query = {'root_path': str(outputs), 'path': 'external/run-a/perf'}
    archive = http.get('/api/v1/perf/plans/archive', query_string=query)
    assert archive.status_code == 200
    assert archive.json['quality'] == 'complete'
    assert archive.json['task_id'] == 'run-a'
    report = http.get('/api/v1/perf/plans/report', query_string=query)
    assert report.status_code == 200
    escape = http.get(
        '/api/v1/perf/plans/archive', query_string={'root_path': str(external), 'path': '../run-a/perf'}
    )
    assert escape.status_code in (400, 404)


def test_archive_and_report_project_the_cache_ratio_verification(outputs: Path) -> None:
    """The typed field reaches the API and the standalone report keeps history honest."""
    from evalscope.perf.plans.execution import CacheRatioVerification, PerfPlanManifest, PlanRunState
    from evalscope.perf.plans.provenance import plan_dir, write_plan_archive

    external = outputs / 'external'
    perf_root = external / 'run-c' / 'perf'
    perf_root.mkdir(parents=True)
    plan = compile_plan_text(_text())
    write_plan_archive(perf_root, plan, source_text=_text())
    rows = [
        PlanRunState(
            run_id=run.run_id,
            order=run.order,
            workload_kind=run.workload_kind,
            message_structure=run.message_structure,
            concurrency=run.concurrency,
            formal_requests=run.request_count,
            primer_requests=run.primer_requests,
        )
        for run in plan.runs
    ]
    rows[0].status = 'completed'
    rows[0].quality = 'partial'
    rows[0].cache_verification = CacheRatioVerification(
        declared_ratio=0.9,
        observed_ratio=0.6,
        delta=-0.3,
        tolerance=0.05,
        required=True,
        status='mismatch',
    )
    (plan_dir(perf_root) / 'manifest.json').write_text(
        PerfPlanManifest(
            plan_hash=plan.plan_hash,
            normalized_hash=plan.normalized_hash,
            semantic_fingerprint=plan.semantic_fingerprint,
            workload_semantics=plan.workload_semantics,
            status='completed',
            quality='partial',
            completed_runs=1,
            total_runs=plan.run_count,
            runs=rows,
        ).model_dump_json()
    )

    app = create_app(outputs=str(outputs))
    http = app.test_client()
    query = {'root_path': str(outputs), 'path': 'external/run-c/perf'}
    body = http.get('/api/v1/perf/plans/archive', query_string=query).json
    verification = body['runs'][0]['cache_verification']
    assert verification['status'] == 'mismatch'
    assert verification['declared_ratio'] == 0.9 and verification['observed_ratio'] == 0.6
    assert verification['required'] is True and verification['tolerance'] == 0.05
    assert body['runs'][1]['cache_verification'] is None

    html = http.get('/api/v1/perf/plans/report', query_string=query).get_data(as_text=True)
    assert 'mismatch' in html and '90.0%' in html and '60.0%' in html and '-30.0 pp' in html
    assert 'not recorded' in html


def test_archive_endpoint_is_404_without_a_plan(client) -> None:
    _, http = client
    assert http.get('/api/v1/perf/plans/missing-task').status_code == 404
    rejected = http.post(
        '/api/v1/perf/plans/invoke?async=1',
        headers={'EvalScope-Task-Id': '../evil'},
        json={'content': _text()},
    )
    assert rejected.status_code == 400


def test_a_native_perf_task_directory_is_not_reused(client, tmp_path: Path, queue) -> None:
    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    task_dir = Path(log.OUTPUT_DIR) / TASK_ID / 'perf'
    task_dir.mkdir(parents=True)
    response = _invoke(http, _text())
    assert response.status_code == 409
    assert queue.jobs == []


def test_legacy_plan_layout_is_read_only(client, tmp_path: Path, queue) -> None:
    """An archive written by the old artificial layout is readable but never executed."""
    from evalscope.perf.plans.provenance import legacy_plan_dir

    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    task_dir = Path(log.OUTPUT_DIR) / TASK_ID
    task_dir.mkdir()
    legacy = legacy_plan_dir(task_dir)
    legacy.mkdir()
    plan = compile_plan_text(_text())
    (legacy / 'compiled.json').write_text(plan.model_dump_json())
    (legacy / 'normalized.json').write_text(plan.normalized.model_dump_json())

    response = _invoke(http, _text())
    assert response.status_code == 409
    assert queue.jobs == []

    archive = http.get(f'/api/v1/perf/plans/{TASK_ID}')
    assert archive.status_code == 200
    assert archive.json['report_available'] is False


def test_run_requests_reject_a_path_escape(client, tmp_path: Path, queue) -> None:
    from evalscope.service.perf_plans import run_normalized_dir

    app, http = client
    app.config['AIPERF_EXECUTABLE'] = str(_fake_aiperf(tmp_path))
    assert _invoke(http, _text()).status_code == 202
    for candidate in ('..', '../..', 'a/b', '', '.'):
        with pytest.raises(ValueError):
            run_normalized_dir(str(Path(log.OUTPUT_DIR)), TASK_ID, candidate)
    missing = http.get(f'/api/v1/perf/plans/{TASK_ID}/runs/run-0000/requests')
    assert missing.status_code in (400, 404)


def test_preview_helper_reports_runtime_unavailable_as_a_diagnostic() -> None:
    preview = build_preview(_text(), engine_available=False)
    assert preview.can_submit is True
    assert preview.engine_available is False
    diagnostic = next(item for item in preview.diagnostics if item.code == 'runtime_unavailable')
    assert diagnostic.severity == 'warning'


def test_preview_module_never_returns_local_paths_for_runtime() -> None:
    preview = build_preview(_text(), engine_available=True, runtime_version='0.13.0')
    assert preview.runtime.runtime_status == 'available'
    assert preview.runtime.runtime_version == '0.13.0'
    assert os.path.isabs(preview.normalized.connection.tokenizer_path)
