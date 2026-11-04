"""Check token-level target and generated reuse without claiming server hits."""

from pathlib import Path
from typing import Sequence

import pytest

from evalscope.perf.experiments.prefix_workload import PrefixWorkloadArguments, build_prefix_workload
from evalscope.perf.experiments.tokenizer import HuggingFacePrefixTokenizer


class _Tokenizer:
    def decode(self, token_ids: Sequence[int]) -> str:
        return ' '.join(f't{item}' for item in token_ids)

    def render(self, user_text: str) -> list[int]:
        content = [int(token[1:]) for token in user_text.split()] if user_text else []
        return [10000, 10001, *content, 10002]


def _arguments(ratio: float, namespace: str = 'run-a') -> PrefixWorkloadArguments:
    return PrefixWorkloadArguments(
        target_ratio=ratio, total_input_tokens=20, request_count=6, prefix_pool_size=2, seed=42,
        namespace=namespace, tokenizer_id='fixture', template_id='fixture-chat',
    )


@pytest.mark.parametrize('ratio', [0.0, 0.8, 0.9])
def test_fixed_total_and_generated_prefix_evidence(ratio: float) -> None:
    workload = build_prefix_workload(_arguments(ratio), _Tokenizer(), list(range(200)))
    assert len(workload.requests) == 6
    assert len({request.sha256 for request in workload.requests}) == 6
    assert all(request.input_tokens == 20 for request in workload.requests)
    assert 'cached_tokens' not in workload.model_dump()
    if ratio == 0:
        assert not workload.primers
        assert {request.shared_tokens for request in workload.requests} == {2}
        assert workload.target_shared_tokens == 0
    else:
        assert len(workload.primers) == 2
        assert {request.shared_tokens for request in workload.requests} == {round(ratio * 20)}
        assert {request.prefix_id for request in workload.requests} == {0, 1}


def test_namespace_and_seed_are_reproducible() -> None:
    first = build_prefix_workload(_arguments(0.8), _Tokenizer(), list(range(200)))
    assert first == build_prefix_workload(_arguments(0.8), _Tokenizer(), list(range(200)))
    other = build_prefix_workload(_arguments(0.8, namespace='run-b'), _Tokenizer(), list(range(200)))
    assert {item.sha256 for item in first.requests}.isdisjoint(item.sha256 for item in other.requests)


def test_invalid_or_unverifiable_tokenization_is_rejected() -> None:
    with pytest.raises(ValueError, match='suffix'):
        build_prefix_workload(_arguments(1.0), _Tokenizer(), list(range(200)))
    with pytest.raises(ValueError, match='distinct leading token'):
        build_prefix_workload(_arguments(0), _Tokenizer(), [1, 2])

    class _NonRoundTrip(_Tokenizer):
        def render(self, user_text: str) -> list[int]:
            rendered = super().render(user_text)
            return rendered[:-1] if user_text else rendered

    with pytest.raises(ValueError, match='Tokenizer/template'):
        build_prefix_workload(_arguments(0.8), _NonRoundTrip(), list(range(200)))


def test_real_bpe_merges_cannot_silently_change_length() -> None:
    from tokenizers import Tokenizer, decoders, models

    backend = Tokenizer(models.BPE({'a': 0, 'b': 1, 'aa': 2, 'ab': 3, 'ba': 4, 'bb': 5},
                                  [('a', 'a'), ('a', 'b'), ('b', 'a'), ('b', 'b')]))
    backend.decoder = decoders.Fuse()

    class _BPE:
        def decode(self, token_ids: Sequence[int]) -> str:
            return backend.decode(list(token_ids))

        def render(self, user_text: str) -> list[int]:
            return backend.encode(user_text).ids

    with pytest.raises(ValueError, match='Tokenizer/template'):
        build_prefix_workload(_arguments(0.8), _BPE(), [0, 1])


def test_local_huggingface_chat_template_is_rendered_and_hashed(tmp_path: Path) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    vocab = {'[UNK]': 0, 'header': 1, 'tail': 2, 'assistant': 3, **{f't{i}': i + 4 for i in range(200)}}
    backend = Tokenizer(models.WordLevel(vocab, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]')
    tokenizer.chat_template = "header {{ messages[0]['content'] }} tail assistant"
    tokenizer.save_pretrained(tmp_path)
    adapter = HuggingFacePrefixTokenizer(str(tmp_path))
    args = _arguments(0.8).model_copy(update={'template_id': adapter.template_hash})
    workload = build_prefix_workload(args, adapter, adapter.candidate_ids())
    assert len(adapter.template_hash) == 64
    assert workload.template_id == adapter.template_hash
    assert all(len(adapter.render(item.text)) == 20 and item.shared_tokens == 16 for item in workload.requests)


def test_distinct_prefix_pool_is_checked_after_tokenizer_normalization() -> None:
    class CollapsingTokenizer:
        def decode(self, token_ids: Sequence[int]) -> str:
            return ' '.join(f't{item}' for item in token_ids)

        def render(self, text: str) -> list[int]:
            return [10000, *(int(token[1:]) % 2 for token in text.split()), 10001]

    arguments = PrefixWorkloadArguments(
        target_ratio=.2, total_input_tokens=10, request_count=3, prefix_pool_size=3, seed=42,
        namespace='normalization', tokenizer_id='fixture', template_id='template',
    )
    with pytest.raises(ValueError, match='distinct token prefix pool'):
        build_prefix_workload(arguments, CollapsingTokenizer(), list(range(20)))

    possible = arguments.model_copy(update={'prefix_pool_size': 2})
    workload = build_prefix_workload(possible, CollapsingTokenizer(), list(range(20)))
    prefixes = [tuple(CollapsingTokenizer().render(item.text)[:2]) for item in workload.primers]
    assert len(set(prefixes)) == 2
    rendered = [tuple(CollapsingTokenizer().render(item.text)) for item in workload.requests]
    assert len(set(rendered)) == len(workload.requests)


def test_distinct_texts_cannot_hide_duplicate_rendered_requests() -> None:
    class CollapsingSuffixTokenizer:
        def decode(self, token_ids: Sequence[int]) -> str:
            return ' '.join(f't{item}' for item in token_ids)

        def render(self, text: str) -> list[int]:
            content = [int(token[1:]) % 2 for token in text.split()]
            if len(content) > 1:
                content[1:] = [0] * (len(content) - 1)
            return [10000, *content, 10001]

    arguments = PrefixWorkloadArguments(
        target_ratio=.5, total_input_tokens=4, request_count=3, prefix_pool_size=2, seed=42,
        namespace='duplicate', tokenizer_id='fixture', template_id='template',
    )
    with pytest.raises(ValueError, match='Tokenizer/template cannot produce'):
        build_prefix_workload(arguments, CollapsingSuffixTokenizer(), list(range(20)))


def test_content_dependent_template_header_cannot_change_reuse_silently() -> None:
    class ConditionalHeaderTokenizer:
        def decode(self, token_ids: Sequence[int]) -> str:
            return ' '.join(f't{item}' for item in token_ids)

        def render(self, text: str) -> list[int]:
            content = [int(token[1:]) for token in text.split()]
            header = 10001 if content and content[0] == 2 else 10000
            return [header, *content, 10002]

    arguments = PrefixWorkloadArguments(
        target_ratio=0, total_input_tokens=5, request_count=3, prefix_pool_size=1, seed=42,
        namespace='dynamic-header', tokenizer_id='fixture', template_id='template',
    )
    with pytest.raises(ValueError, match='Tokenizer/template cannot produce'):
        build_prefix_workload(arguments, ConditionalHeaderTokenizer(), [0, 1, 2])
