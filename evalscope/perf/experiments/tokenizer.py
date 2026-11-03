"""Local Hugging Face tokenizer adapter for prefix workload validation."""

import hashlib
import json
from typing import List, Optional, Sequence

from evalscope.perf.executors.external import Cancellation
from evalscope.perf.experiments.prefix_workload import WorkloadCancelled


class HuggingFacePrefixTokenizer:
    """Render the locally supplied model's chat template without remote code."""

    def __init__(self, tokenizer_path: str) -> None:
        from transformers import AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True, trust_remote_code=False)
        template = self._tokenizer.chat_template
        if not isinstance(template, str) or not template:
            raise ValueError('A single explicit chat template is required for prefix experiments')
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
