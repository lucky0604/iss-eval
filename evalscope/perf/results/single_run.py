"""Validated metadata for one independently archived AIPerf run."""

import json
import os
import tempfile
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, StrictInt, model_validator

from evalscope.utils import BaseArgument


class SingleRunManifest(BaseArgument):
    """Service-owned, credential-free identity and result reference."""

    schema_version: Literal['1.0'] = '1.0'
    engine: Literal['aiperf'] = 'aiperf'
    task_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    alias: Optional[str] = None
    endpoint: Optional[str] = None
    executor_version: Literal['0.13.0'] = '0.13.0'
    load_mode: Literal['closed_loop', 'open_loop']
    concurrency: Optional[StrictInt] = Field(default=None, gt=0)
    rate: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    planned_requests: StrictInt = Field(gt=0)
    output_token_limit: StrictInt = Field(gt=0)
    input_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    status: Literal['running', 'completed', 'stopped', 'error'] = 'running'
    artifact_status: Literal['available', 'partial', 'missing'] = 'missing'
    normalized: Optional[Literal['normalized']] = None
    quality: Optional[Literal['complete', 'partial', 'invalid']] = None
    quality_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_result(self) -> 'SingleRunManifest':
        if self.load_mode == 'closed_loop' and (self.concurrency is None or self.rate is not None):
            raise ValueError('Invalid closed-loop load point')
        if self.load_mode == 'open_loop' and (self.rate is None or self.concurrency is not None):
            raise ValueError('Invalid open-loop load point')
        if (self.normalized is None) != (self.quality is None):
            raise ValueError('Normalized result and quality must be recorded together')
        return self


def write_single_manifest(directory: str, manifest: SingleRunManifest) -> None:
    """Atomically publish a manifest without including credentials or prompts."""
    manifest = SingleRunManifest.model_validate(manifest.model_dump())
    root = Path(directory).resolve(strict=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, delete=False) as file:
            temporary = Path(file.name)
            json.dump(manifest.model_dump(mode='json'), file, allow_nan=False)
        os.replace(temporary, root / 'single_run.json')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_single_manifest(directory: str) -> SingleRunManifest:
    """Load a manifest contained in the given archive directory."""
    root = Path(directory).resolve(strict=True)
    path = (root / 'single_run.json').resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Invalid single-run manifest path or size')
    with path.open(encoding='utf-8') as file:
        return SingleRunManifest.model_validate_json(file.read())
