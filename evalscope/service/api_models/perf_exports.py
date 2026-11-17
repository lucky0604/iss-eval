"""Response models for the independent report export lifecycle."""

from typing import List, Literal, Optional

from pydantic import Field

from .common import ApiResponseModel


class PerfExportSpec(ApiResponseModel):
    """What to export: one archive, one group/locale and one artifact kind.

    It is also echoed in every status response so a client can retry exactly
    the same export without keeping its own copy.
    """

    kind: Literal['html', 'pdf', 'csv', 'requests', 'bundle'] = 'html'
    task_id: Optional[str] = None
    path: Optional[str] = None
    root_path: Optional[str] = None
    group: Optional[str] = None
    preset: Literal['core', 'all'] = 'core'
    locale: Literal['en', 'zh'] = 'en'
    status: Optional[Literal['success', 'failed']] = None
    search: Optional[str] = None
    run_key: Optional[str] = None
    phase: Literal['warmup', 'profiling', 'all'] = 'profiling'
    pretty: bool = False


class PerfExportArtifact(ApiResponseModel):
    """One downloaded file; ``url`` is relative to the service origin."""

    name: str
    filename: str
    media_type: str
    size: int = Field(ge=0)
    sha256: str
    url: str


class PerfExportView(ApiResponseModel):
    """The independently persisted state of one export."""

    export_id: str
    status: Literal['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted']
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    attempts: int = Field(ge=0)
    progress: Optional[str] = None
    error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    snapshot_id: Optional[str] = None
    snapshot_created_at: Optional[str] = None
    source_kind: Optional[str] = None
    run_count: Optional[int] = Field(default=None, ge=0)
    request_count: Optional[int] = Field(default=None, ge=0)
    spec: PerfExportSpec
    artifacts: List[PerfExportArtifact] = Field(default_factory=list)


class PerfExportResponse(ApiResponseModel):
    """A newly queued export, or the equivalent one that already existed."""

    export: PerfExportView
    created: bool


class PerfExportStatusResponse(ApiResponseModel):
    export: PerfExportView


class PerfExportCapabilitiesResponse(ApiResponseModel):
    """Which artifact kinds this host can produce right now."""

    pdf_available: bool
    pdf_renderer: Optional[str] = None
    pdf_message: str = ''
    kinds: List[str]
