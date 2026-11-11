"""Strict JSON decoding for portable performance plans.

The browser must not parse the uploaded file first: doing so would silently
drop a duplicate object key before the server ever sees it. This module keeps
the raw text authoritative and rejects duplicate keys, non-finite numbers,
credential-shaped fields and command fields before any model validation runs.
"""

import json
import math
from typing import Any, Dict, List, Tuple

MAX_DOCUMENT_BYTES = 1024 * 1024
"""Portable document limit, measured on the UTF-8 bytes of the uploaded file."""

CREDENTIAL_FIELDS = frozenset(
    {
        'api_key',
        'apikey',
        'authorization',
        'auth',
        'headers',
        'bearer',
        'bearer_token',
        'access_token',
        'password',
        'secret',
        'token',
    }
)
"""Field names that would put a runtime secret into a portable document."""

COMMAND_FIELDS = frozenset(
    {'argv', 'command', 'cmd', 'shell', 'script', 'executable', 'extra_args', 'output_dir', 'working_dir'}
)
"""Field names that would turn the document into an executable command."""


class PlanParseError(ValueError):
    """A rejected document with a stable code and an optional field path."""

    def __init__(self, code: str, message: str, field_path: str = '') -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_path = field_path


def _join(prefix: str, key: str) -> str:
    return f'{prefix}.{key}' if prefix else key


def _object_pairs(duplicates: List[str]):
    def hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result and key not in duplicates:
                duplicates.append(key)
            result[key] = value
        return result

    return hook


def _reject_constant(value: str) -> Any:
    raise PlanParseError('invalid_type', f'Non-finite JSON number {value} is not allowed')


def scan_document(node: Any, path: str = '') -> None:
    """Reject credentials and command fields anywhere in the parsed document."""
    if isinstance(node, dict):
        for key, value in node.items():
            field_path = _join(path, key)
            lowered = str(key).lower()
            if lowered in CREDENTIAL_FIELDS:
                raise PlanParseError(
                    'credential_in_document',
                    'Portable plans must not contain credentials; configure the API key through the connection form '
                    f'({field_path}).',
                    field_path,
                )
            if lowered in COMMAND_FIELDS:
                raise PlanParseError(
                    'unsupported_feature',
                    f'Field {field_path} would turn the document into a command; portable plans describe parameters only.',
                    field_path,
                )
            scan_document(value, field_path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            scan_document(value, f'{path}[{index}]')


def _reject_numbers(node: Any, path: str = '') -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_numbers(value, _join(path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_numbers(value, f'{path}[{index}]')
    elif isinstance(node, float) and not math.isfinite(node):
        raise PlanParseError('invalid_type', f'Non-finite number at {path or "document"}', path)


def parse_document_text(text: Any, max_bytes: int = MAX_DOCUMENT_BYTES) -> Tuple[Dict[str, Any], str]:
    """Decode a portable plan from raw text without trusting client-side parsing.

    Returns the parsed object and the original text, which is preserved for
    ``source_hash`` and archival. Raises :class:`PlanParseError` for every
    rejection so callers can map it to a typed diagnostic.
    """
    if not isinstance(text, str):
        raise PlanParseError('invalid_type', 'Plan content must be the original JSON text, not a parsed object')
    raw = text.encode('utf-8')
    if len(raw) > max_bytes:
        raise PlanParseError('too_large_document', f'Plan document exceeds the {max_bytes} byte limit', 'content')
    if not text.strip():
        raise PlanParseError('invalid_json', 'Plan document is empty', 'content')
    duplicates: List[str] = []
    try:
        document = json.loads(text, object_pairs_hook=_object_pairs(duplicates), parse_constant=_reject_constant)
    except PlanParseError:
        raise
    except json.JSONDecodeError as error:
        raise PlanParseError(
            'invalid_json', f'Invalid JSON at line {error.lineno} column {error.colno}', 'content'
        ) from None
    if duplicates:
        raise PlanParseError(
            'duplicate_json_key',
            f'Duplicate JSON key {duplicates[0]!r}; the later value would silently replace the earlier one.',
            duplicates[0],
        )
    if not isinstance(document, dict):
        raise PlanParseError('invalid_json', 'Plan document must be a JSON object', 'content')
    scan_document(document)
    _reject_numbers(document)
    return document, text


def classify_document(document: Dict[str, Any]) -> str:
    """Return ``perf_plan``, ``config_library`` or raise for an unknown format.

    Detection is explicit (kind / version), never based on the file extension.
    """
    if document.get('kind') == 'iss-eval.perf-plan':
        return 'perf_plan'
    if 'kind' in document:
        raise PlanParseError(
            'unsupported_schema',
            f"Unknown document kind {document.get('kind')!r}; only 'iss-eval.perf-plan' is a portable plan.",
            'kind',
        )
    if isinstance(document.get('version'), int) and 'connections' in document:
        return 'config_library'
    raise PlanParseError(
        'unsupported_schema',
        'Document has neither a perf-plan kind nor a configuration-library version; '
        'arbitrary backend payloads are not portable plans.',
        '',
    )
