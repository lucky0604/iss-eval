"""Independent report exports: HTML, PDF, CSV, request CSV/JSONL and a bundle.

The public surface is the runner (create, inspect, cancel, retry, download)
plus the runtime probe the API reports so the UI can explain a missing PDF
renderer instead of failing silently.
"""

from .models import (
    EXPORT_TEMPLATE_VERSION,
    ExportArtifact,
    ExportCancelled,
    ExportKind,
    ExportLocale,
    ExportRecord,
    ExportSpec,
    ExportStatus,
)
from .pdf import PdfUnavailable, pdf_runtime
from .runner import ExportRunner, recover_exports, runner
from .store import ExportStore

__all__ = [
    'EXPORT_TEMPLATE_VERSION',
    'ExportArtifact',
    'ExportCancelled',
    'ExportKind',
    'ExportLocale',
    'ExportRecord',
    'ExportRunner',
    'ExportSpec',
    'ExportStatus',
    'ExportStore',
    'PdfUnavailable',
    'pdf_runtime',
    'recover_exports',
    'runner',
]
