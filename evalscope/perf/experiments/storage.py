"""Atomic writes for local experiment state."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict


def write_experiment_json(path: Path, value: Dict[str, Any]) -> None:
    """Atomically replace a private JSON document in its existing directory."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            os.fchmod(file.fileno(), 0o600)
            json.dump(value, file, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
