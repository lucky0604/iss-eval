"""Hugging Face tokenizer adapter for prefix workload validation."""

import hashlib
import json
import re
from pathlib import Path
from typing import List, Optional, Sequence

from evalscope.perf.executors.external import Cancellation
from evalscope.perf.experiments.prefix_workload import WorkloadCancelled

_MODEL_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$')
# Tokenizer-only files keep a ModelScope download small: weights are never fetched.
_TOKENIZER_FILES = [
    'tokenizer.json',
    'tokenizer_config.json',
    'special_tokens_map.json',
    'added_tokens.json',
    'vocab.json',
    'merges.txt',
    'tokenizer.model',
    'chat_template.jinja',
    'chat_template.json',
    '*.tiktoken',
]


def _looks_like_local_path(reference: str) -> bool:
    return (
        reference.startswith(('/', './', '../', '~'))
        or '\\' in reference
        or re.match(r'^[A-Za-z]:[\\/]', reference) is not None
    )


def _snapshot_tokenizer_files(model_id: str, **options: object) -> str:
    """Ask ModelScope for tokenizer-only files, tolerating older keyword names."""
    try:
        from modelscope import snapshot_download
    except ImportError as error:  # pragma: no cover - modelscope is a framework dependency
        raise ImportError('ModelScope is required to download a remote tokenizer') from error
    try:
        return snapshot_download(model_id, allow_patterns=_TOKENIZER_FILES, **options)
    except TypeError:
        return snapshot_download(model_id, allow_file_pattern=_TOKENIZER_FILES, **options)


def _download_tokenizer_files(model_id: str) -> str:
    """Fetch only tokenizer files for a ModelScope model ID and return the cache directory."""

    def fetch(**options: object) -> str:
        directory = _snapshot_tokenizer_files(model_id, **options)
        if not isinstance(directory, str) or not Path(directory).is_dir():
            raise OSError(f'ModelScope returned no tokenizer directory for {model_id}')
        return directory

    try:
        return fetch(local_files_only=True)
    except Exception:
        pass
    try:
        return fetch()
    except Exception as error:
        raise OSError(f'ModelScope tokenizer download failed for {model_id}: {error}') from error


def resolve_tokenizer_directory(source: str) -> str:
    """Return a local tokenizer directory, downloading tokenizer files for a ModelScope ID."""
    reference = source.strip()
    if not reference:
        raise ValueError('Tokenizer source must not be blank')
    directory = Path(reference).expanduser()
    if directory.is_dir():
        return str(directory)
    if _looks_like_local_path(reference):
        raise OSError(f'Tokenizer directory is not readable by the service process: {reference}')
    if not _MODEL_ID.fullmatch(reference):
        raise ValueError(
            'Tokenizer source must be a readable local directory or a ModelScope model ID '
            f'such as Qwen/Qwen2.5-0.5B-Instruct: {reference}'
        )
    return _download_tokenizer_files(reference)


class HuggingFacePrefixTokenizer:
    """Render the supplied model's chat template without remote code."""

    def __init__(self, tokenizer_path: str) -> None:
        from transformers import AutoTokenizer

        directory = resolve_tokenizer_directory(tokenizer_path)
        # AIPerf receives this directory so it never re-resolves the source itself.
        self.directory = directory
        self._tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        template = self._tokenizer.chat_template
        if isinstance(template, dict):
            raise ValueError(f'Multiple chat templates are configured for the tokenizer: {sorted(map(str, template))}')
        if isinstance(template, (list, tuple)):
            names = [str(item.get('name')) for item in template if isinstance(item, dict)]
            raise ValueError(f'Multiple chat templates are configured for the tokenizer: {names}')
        if not isinstance(template, str) or not template:
            raise ValueError(
                'A single explicit chat template is required for prefix experiments; '
                f'add chat_template to tokenizer_config.json in {directory}'
            )
        backend = getattr(self._tokenizer, 'backend_tokenizer', None)
        if backend is None:
            raise ValueError('A fast tokenizer with a serializable backend is required')
        identity = {
            'backend': backend.to_str(),
            'special_tokens': self._tokenizer.special_tokens_map,
        }
        self.tokenizer_hash = hashlib.sha256(
            json.dumps(identity, sort_keys=True, default=str, ensure_ascii=False).encode('utf-8')
        ).hexdigest()
        self.template_hash = hashlib.sha256(template.encode('utf-8')).hexdigest()

    def decode(self, token_ids: Sequence[int]) -> str:
        """Decode candidate IDs without stripping spaces or special tokens."""
        return self._tokenizer.decode(list(token_ids), skip_special_tokens=False, clean_up_tokenization_spaces=False)

    def render(self, user_text: str) -> Sequence[int]:
        """Tokenize the complete user turn and assistant generation prompt."""
        result = self._tokenizer.apply_chat_template(
            [{'role': 'user', 'content': user_text}], tokenize=True, add_generation_prompt=True, return_dict=False
        )
        if not isinstance(result, list) or any(type(item) is not int for item in result):
            raise ValueError('Tokenizer did not produce a token ID sequence')
        return result

    def candidate_ids(self, limit: int = 16384, cancel_event: Optional[Cancellation] = None) -> List[int]:
        """Return distinct non-special tokens that round-trip independently."""
        if type(limit) is not int or limit < 2:
            raise ValueError('Candidate limit must be an integer of at least two')
        special = set(self._tokenizer.all_special_ids)
        seen_text = set()
        candidates = []
        for token in sorted(set(self._tokenizer.get_vocab().values())):
            if cancel_event is not None and cancel_event.is_set():
                raise WorkloadCancelled()
            if token in special or token < 0:
                continue
            text = self.decode([token])
            if not text or text in seen_text or self._tokenizer.encode(text, add_special_tokens=False) != [token]:
                continue
            seen_text.add(text)
            candidates.append(token)
            if len(candidates) >= limit:
                break
        if len(candidates) < 2:
            raise ValueError('Tokenizer has fewer than two independently round-trippable tokens')
        return candidates
