"""Unified results, task detail and progress for portable plans (F02/U01-U05)."""

import json
import os
from pathlib import Path

import pytest

from evalscope.perf.plans import compile_plan_text
from evalscope.perf.plans.execution import PerfPlanManifest, PlanRunState
from evalscope.perf.plans.provenance import EffectiveIdentity, TokenizerIdentity, plan_dir, write_effective_identity
from evalscope.service import perf_entries, task_detail
from evalscope.service.utils import log

FIXTURES = Path(__file__).parents[1] / 'perf' / 'fixtures' / 'perf_plan'
TASK_ID = 'plan-task'


def _text(name: str = 'managed-small.json') -> str:
    return (FIXTURES / name).read_text()


def _manifest(plan, *, status: str = 'completed', quality: str = 'complete', completed: int | None = None):
    rows = [
        PlanRunState(
            run_id=run.run_id,
            order=run.order,
            workload_kind=run.workload_kind,
            message_structure=run.message_structure,
            concurrency=run.concurrency,
            formal_requests=run.request_count,
        )
        for run in plan.runs
    ]
    completed_runs = plan.run_count if completed is None else completed
    for row in rows[:completed_runs]:
        row.status = 'completed'
        row.quality = quality
    return PerfPlanManifest(
        plan_hash=plan.plan_hash,
        normalized_hash=plan.normalized_hash,
        semantic_fingerprint=plan.semantic_fingerprint,
        workload_semantics=plan.workload_semantics,
        status=status,
        quality=quality,
        completed_runs=completed_runs,
        total_runs=plan.run_count,
        runs=rows,
    )


def _archive(root: Path, *, manifest: PerfPlanManifest | None = None, effective: bool = False) -> Path:
    perf_root = root / TASK_ID / 'perf'
    directory = plan_dir(perf_root)
    directory.mkdir(parents=True)
    plan = compile_plan_text(_text())
    (directory / 'compiled.json').write_text(plan.model_dump_json())
    (directory / 'normalized.json').write_text(plan.normalized.model_dump_json())
    (directory / 'manifest.json').write_text((manifest or _manifest(plan)).model_dump_json())
    runs = perf_root / 'runs'
    runs.mkdir()
    for run in plan.runs:
        normalized = runs / run.run_id / 'normalized'
        (normalized / 'generations' / 'gen-0001').mkdir(parents=True)
        (normalized / 'current.json').write_text(json.dumps({'generation': 'gen-0001'}))
        (normalized / 'generations' / 'gen-0001' / 'requests.db').write_bytes(b'')
    if effective:
        write_effective_identity(
            perf_root,
            EffectiveIdentity(
                plan_hash=plan.plan_hash,
                contract_version=plan.contract_version,
                compiler_version=plan.compiler_version,
                tokenizer=TokenizerIdentity(
                    status='resolved', content_hash='tok-hash', template_hash='tpl-hash', path='/models/tokenizer'
                ),
                aiperf_version='0.13.0',
            ),
        )
    return perf_root


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    monkeypatch.setattr(log, 'OUTPUT_DIR', str(outputs))
    return outputs


def test_entries_list_a_plan_container_once(root: Path) -> None:
    _archive(root)
    page = perf_entries.list_entries(str(root), None, None, '', 'time', 0, 50)
    entries = page['entries']
    assert len(entries) == 1
    entry = entries[0]
    assert entry['entry_id'] == f'plan:{TASK_ID}'
    assert entry['result_kind'] == 'experiment'
    assert entry['test_type'] == 'cache_experiment'
    assert entry['engine'] == 'aiperf'
    assert entry['num_runs'] == compile_plan_text(_text()).run_count
    assert entry['execution_status'] == 'completed'
    assert entry['quality'] == 'complete'
    assert entry['task_id'] == TASK_ID
    assert entry['detail_path'].startswith('/perf-plan?')
    assert entry['capabilities']['cache_experiment']['availability'] == 'available'
    assert entry['capabilities']['requests']['availability'] == 'available'
    assert entry['capabilities']['node_metrics']['availability'] == 'unsupported'
    assert page['completeness'] == 'complete'

    # The per-run archives never surface as separate rows.
    assert page['scanned'] == 1


def test_entries_mark_a_running_plan_as_not_finished(root: Path) -> None:
    plan = compile_plan_text(_text())
    _archive(root, manifest=_manifest(plan, status='running', quality='partial', completed=1))
    entry = perf_entries.list_entries(str(root), None, None, '', 'time', 0, 50)['entries'][0]
    assert entry['execution_status'] == 'running'
    assert entry['capabilities']['overview']['availability'] == 'available'


def test_entries_never_link_a_task_outside_the_service_root(root: Path, tmp_path: Path) -> None:
    external = tmp_path / 'external'
    external.mkdir()
    _archive(external)
    entry = perf_entries.list_entries(str(external), None, None, '', 'time', 0, 50)['entries'][0]
    assert entry['task_id'] is None
    assert entry['detail_path'].startswith('/perf-plan?')


def test_task_detail_reports_plan_capabilities_and_snapshot(root: Path) -> None:
    _archive(root, effective=True)
    record = {'task_id': TASK_ID, 'kind': 'perf', 'engine': 'aiperf', 'test_type': 'sweep', 'status': 'completed'}
    availability, capabilities = task_detail.detect_capabilities(record)
    assert availability.availability == 'available'
    assert capabilities.overview.availability == 'available'
    assert capabilities.sweep.availability == 'available'
    assert capabilities.requests.availability == 'available'
    assert capabilities.cache_experiment.availability == 'available'
    assert capabilities.configuration.availability == 'available'
    assert capabilities.html.availability == 'available'
    assert capabilities.node_metrics.availability == 'unsupported'

    snapshot, level = task_detail.build_config_snapshot(record)
    assert level == 'full'
    assert snapshot.snapshot_type == 'perf_plan'
    assert snapshot.plan_hash == compile_plan_text(_text()).plan_hash
    assert snapshot.tokenizer_id == 'tok-hash'
    assert snapshot.template_id == 'tpl-hash'
    assert snapshot.aiperf_version == '0.13.0'
    assert snapshot.deployment_evidence == 'declared'
    assert snapshot.deployment_measured is False
    assert snapshot.total_runs == compile_plan_text(_text()).run_count
    assert set(snapshot.input_lengths) == {1024}

    detail = task_detail.build_detail(record)
    assert detail.report_url.startswith(f'/perf-plan/{TASK_ID}?')
    assert detail.html_report_url == f'/api/v1/perf/plans/{TASK_ID}/report'
    assert detail.test_type in ('sweep', 'cache_experiment')


def test_task_detail_marks_an_unfinished_plan_as_pending(root: Path) -> None:
    perf_root = root / TASK_ID / 'perf'
    directory = plan_dir(perf_root)
    directory.mkdir(parents=True)
    plan = compile_plan_text(_text())
    (directory / 'compiled.json').write_text(plan.model_dump_json())
    record = {'task_id': TASK_ID, 'kind': 'perf', 'engine': 'aiperf', 'test_type': 'cache_experiment', 'status': 'running'}
    _, capabilities = task_detail.detect_capabilities(record)
    assert capabilities.overview.availability == 'pending'
    assert capabilities.html.availability == 'pending'
    assert capabilities.requests.availability == 'unavailable'


def test_progress_reads_the_plan_manifest(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = compile_plan_text(_text())
    _archive(root, manifest=_manifest(plan, status='completed', quality='complete'))
    from evalscope.service.blueprints import tasks as tasks_blueprint

    monkeypatch.setattr(tasks_blueprint, 'OUTPUT_DIR', str(root))
    progress = tasks_blueprint._aiperf_progress(TASK_ID)
    assert progress['percent'] == 100.0
    assert progress['completed_subruns'] == plan.run_count
    assert progress['total_subruns'] == plan.run_count
    assert progress['quality'] == 'complete'


def test_progress_of_a_reserved_plan_is_zero_not_a_native_run(root: Path) -> None:
    from evalscope.perf.plans.provenance import reserve_submission
    from evalscope.service.perf_plans import plan_progress

    perf_root = root / TASK_ID / 'perf'
    perf_root.mkdir(parents=True)
    reserve_submission(perf_root, compile_plan_text(_text()), task_id=TASK_ID)
    progress = plan_progress(perf_root)
    assert progress is not None
    assert progress['percent'] == 0.0
    assert progress['plan_state'] == 'reserved'
    assert progress['total_subruns'] == 0


def test_a_lost_queued_plan_is_reported_rather_than_replayed(root: Path) -> None:
    from evalscope.perf.plans.provenance import reserve_submission, write_submission
    from evalscope.service.perf_plans import plan_progress

    perf_root = root / TASK_ID / 'perf'
    perf_root.mkdir(parents=True)
    submission = reserve_submission(perf_root, compile_plan_text(_text()), task_id=TASK_ID)
    write_submission(perf_root, submission.model_copy(update={'state': 'queued'}))
    assert plan_progress(perf_root, queued=False)['plan_state'] == 'lost'
    assert plan_progress(perf_root, queued=True)['plan_state'] == 'queued'
