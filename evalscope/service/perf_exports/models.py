"""Export specifications, records and statuses for the perf report.

An export is addressed by its own ``export_id`` and stored under its own
directory: it never shares the evaluation task's manifest, ledger or result
files, so rendering a report cannot change the parent load-test's state.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ExportKind = Literal['html', 'pdf', 'csv', 'requests', 'bundle']
ExportStatus = Literal['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted']
ExportLocale = Literal['en', 'zh']

EXPORT_TEMPLATE_VERSION = '1'


class ExportCancelled(Exception):
    """A running export noticed the user's cancel request between stages."""


class ExportSpec(BaseModel):
    """Everything that identifies one export; the id is derived from it."""

    model_config = ConfigDict(extra='forbid')

    kind: ExportKind
    task_id: Optional[str] = None
    path: Optional[str] = None
    root_path: Optional[str] = None
    group: Optional[str] = None
    preset: Literal['core', 'all'] = 'core'
    locale: ExportLocale = 'en'
    status: Optional[Literal['success', 'failed']] = None
    search: Optional[str] = None
    run_key: Optional[str] = None
    phase: Optional[Literal['warmup', 'profiling', 'all']] = 'profiling'
    pretty: bool = False

    def location(self) -> dict:
        return {
            'task_id': self.task_id,
            'root_path': self.root_path,
            'rel_path': self.path,
        }


class ExportArtifact(BaseModel):
    """One downloadable file of a completed export."""

    name: str
    filename: str
    media_type: str
    size: int = Field(ge=0)
    sha256: str


class ExportRecord(BaseModel):
    """The independently persisted state of one export."""

    export_id: str
    spec_key: str
    spec: ExportSpec
    outputs_root: Optional[str] = None
    status: ExportStatus = 'queued'
    created_at: str
    queued_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    updated_at: Optional[str] = None
    attempts: int = 0
    progress: Optional[str] = None
    error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    snapshot_id: Optional[str] = None
    snapshot_created_at: Optional[str] = None
    source_kind: Optional[str] = None
    source_identity: Optional[str] = None
    run_count: Optional[int] = None
    request_count: Optional[int] = None
    artifacts: List[ExportArtifact] = Field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.status in ('completed', 'failed', 'cancelled', 'interrupted')
