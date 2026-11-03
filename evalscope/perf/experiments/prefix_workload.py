"""Deterministic prefix workloads validated after full chat rendering."""

import hashlib
import random
from typing import List, Optional, Protocol, Sequence

from pydantic import Field, StrictInt, field_validator

from evalscope.perf.executors.external import Cancellation
from evalscope.utils import BaseArgument


class PrefixTokenizer(Protocol):
    """Tokenizer adapter using the exact model chat template under test."""

    def decode(self, token_ids: Sequence[int]) -> str:
        """Decode candidate content tokens to user text."""

    def render(self, user_text: str) -> Sequence[int]:
        """Return the complete tokenized chat prompt, including template tokens."""


class WorkloadCancelled(Exception):
    """Generation was cancelled before the workload was published."""


class PrefixWorkloadArguments(BaseArgument):
    """Inputs for one prefix-ratio scenario, not a server cache-hit claim."""

    target_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    total_input_tokens: StrictInt = Field(gt=0)
    request_count: StrictInt = Field(gt=0)
    prefix_pool_size: StrictInt = Field(gt=0)
    seed: StrictInt
    namespace: str = Field(min_length=1)
    tokenizer_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    tolerance_tokens: StrictInt = Field(default=0, ge=0)

    @field_validator('target_ratio', mode='before')
    @classmethod
    def reject_boolean_ratio(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError('Prefix ratio must be numeric')
        return value


class PrefixWorkloadItem(BaseArgument):
    """One primer or profiling input and its generated-prefix evidence."""

    index: int
    prefix_id: Optional[int] = None
    text: str
    sha256: str
    input_tokens: int
    shared_tokens: Optional[int] = None


class PrefixWorkload(BaseArgument):
    """Generated input and evidence; observed cache hits are deliberately absent."""

    target_ratio: float
    target_shared_tokens: int
    total_input_tokens: int
    tokenizer_id: str
    template_id: str
    namespace: str
    seed: int
    tolerance_tokens: int
    primers: List[PrefixWorkloadItem]
    requests: List[PrefixWorkloadItem]


def _common_prefix(left: Sequence[int], right: Sequence[int]) -> int:
    for index, (first, second) in enumerate(zip(left, right)):
        if first != second:
            return index
    return min(len(left), len(right))


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_prefix_workload(
    arguments: PrefixWorkloadArguments,
    tokenizer: PrefixTokenizer,
    token_pool: Sequence[int],
    cancel_event: Optional[Cancellation] = None,
) -> PrefixWorkload:
    """Build fixed-length inputs, failing if tokenization invalidates the target.

    ``render`` must match the service's actual chat template. The caller must
    supply non-special, decodable token IDs and verify that template identity.
    Shared counts describe generated token sequences, not observed cache hits.
    """
    tokens = list(dict.fromkeys(token_pool))
    if len(tokens) < 2 or any(type(token) is not int or token < 0 for token in tokens):
        raise ValueError('At least two distinct non-negative candidate token IDs are required')
    if arguments.target_ratio > 0 and arguments.prefix_pool_size > arguments.request_count:
        raise ValueError('Every requested prefix must have at least one profiling request')
    seed = hashlib.sha256(f'{arguments.seed}\0{arguments.namespace}'.encode()).digest()
    randomizer = random.Random(int.from_bytes(seed, 'big'))
    left, right = tokenizer.decode([tokens[0]]), tokenizer.decode([tokens[1]])
    if not left or not right or left == right:
        raise ValueError('Candidate tokens do not decode to distinct text')
    left_rendered, right_rendered = tokenizer.render(left), tokenizer.render(right)
    if left_rendered == right_rendered:
        raise ValueError('Candidate tokens are indistinguishable after chat rendering')
    header = _common_prefix(left_rendered, right_rendered)
    overhead = len(tokenizer.render(''))
    budget = arguments.total_input_tokens - overhead
    target = round(arguments.target_ratio * arguments.total_input_tokens)
    prefix_length = max(0, target - header) if arguments.target_ratio else 0
    if budget <= 0 or prefix_length >= budget:
        raise ValueError('Template and prefix leave no room for a distinct suffix')
    if arguments.target_ratio > 0 and target < header - arguments.tolerance_tokens:
        raise ValueError('The fixed template already exceeds the requested shared length')
    if arguments.target_ratio > 0 and prefix_length == 0:
        raise ValueError('The requested shared length does not include a primer content token')
    if not arguments.target_ratio and arguments.request_count > len(tokens):
        raise ValueError('Zero-ratio inputs need a distinct leading token for each request')

    primers = []
    requests = []
    rendered_requests: list[Sequence[int]] = []
    zero_ratio_prefixes: set[tuple[int, ...]] = set()
    seen_texts: set[str] = set()
    used_prefixes: set[str] = set()
    pool_size = arguments.prefix_pool_size if arguments.target_ratio else 0
    prefix_texts: list[str] = []
    primer_tokens: list[Sequence[int]] = []
    zero_ratio_leaders = randomizer.sample(tokens, arguments.request_count) if not arguments.target_ratio else []
    for prefix_id in range(pool_size):
        if cancel_event is not None and cancel_event.is_set():
            raise WorkloadCancelled()
        for _ in range(128):
            if cancel_event is not None and cancel_event.is_set():
                raise WorkloadCancelled()
            text = tokenizer.decode([randomizer.choice(tokens) for _ in range(prefix_length)])
            if text and text not in used_prefixes:
                break
        else:
            raise ValueError('Unable to generate a distinct prefix pool')
        used_prefixes.add(text)
        prefix_texts.append(text)
        rendered = tokenizer.render(text)
        primer_tokens.append(rendered)
        primers.append(
            PrefixWorkloadItem(
                index=prefix_id, prefix_id=prefix_id, text=text, sha256=_hash_text(text), input_tokens=len(rendered)
            )
        )
    for index in range(arguments.request_count):
        if cancel_event is not None and cancel_event.is_set():
            raise WorkloadCancelled()
        prefix_id = index % pool_size if pool_size else None
        prefix = prefix_texts[prefix_id] if prefix_id is not None else ''
        for _ in range(128):
            if cancel_event is not None and cancel_event.is_set():
                raise WorkloadCancelled()
            suffix_ids = [randomizer.choice(tokens) for _ in range(budget - prefix_length)]
            if prefix_id is None:
                suffix_ids[0] = zero_ratio_leaders[index]
            suffix = tokenizer.decode(suffix_ids)
            text = f'{prefix} {suffix}' if prefix else suffix
            rendered = tokenizer.render(text)
            if not text or text in seen_texts or len(rendered) != arguments.total_input_tokens:
                continue
            shared = _common_prefix(primer_tokens[prefix_id], rendered) if prefix_id is not None else None
            if shared is not None and abs(shared - target) > arguments.tolerance_tokens:
                continue
            if prefix_id is None and tuple(rendered[: header + 1]) in zero_ratio_prefixes:
                continue
            break
        else:
            raise ValueError('Tokenizer/template cannot produce the requested length and prefix within tolerance')
        seen_texts.add(text)
        if prefix_id is None:
            zero_ratio_prefixes.add(tuple(rendered[: header + 1]))
        rendered_requests.append(rendered)
        requests.append(
            PrefixWorkloadItem(
                index=index,
                prefix_id=prefix_id,
                text=text,
                sha256=_hash_text(text),
                input_tokens=len(rendered),
                shared_tokens=shared,
            )
        )
    if not pool_size and len(requests) > 1:
        for index, request in enumerate(requests):
            other = 1 if index == 0 else index - 1
            request.shared_tokens = _common_prefix(rendered_requests[index], rendered_requests[other])
    return PrefixWorkload(
        target_ratio=arguments.target_ratio,
        target_shared_tokens=target,
        total_input_tokens=arguments.total_input_tokens,
        tokenizer_id=arguments.tokenizer_id,
        template_id=arguments.template_id,
        namespace=arguments.namespace,
        seed=arguments.seed,
        tolerance_tokens=arguments.tolerance_tokens,
        primers=primers,
        requests=requests,
    )
