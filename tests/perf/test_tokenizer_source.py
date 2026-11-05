"""Tokenizer sources resolve to a local directory or a ModelScope tokenizer download."""

from pathlib import Path

import pytest

from evalscope.perf.experiments.tokenizer import HuggingFacePrefixTokenizer, resolve_tokenizer_directory


def _save_tokenizer(path: Path) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    vocab = {'[UNK]': 0, 'header': 1, 'tail': 2, 'assistant': 3, **{f't{i}': i + 4 for i in range(200)}}
    backend = Tokenizer(models.WordLevel(vocab, unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]')
    tokenizer.chat_template = "header {{ messages[0]['content'] }} tail assistant"
    tokenizer.save_pretrained(path)


def test_local_directory_is_used_without_downloading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def download(*args, **kwargs) -> str:
        raise AssertionError('A local directory must not trigger a download')

    monkeypatch.setattr('modelscope.snapshot_download', download)
    assert resolve_tokenizer_directory(str(tmp_path)) == str(tmp_path)


def test_model_scope_id_downloads_tokenizer_files_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def download(model_id: str, allow_patterns=None, **kwargs) -> str:
        calls.append((model_id, allow_patterns))
        return str(tmp_path)

    monkeypatch.setattr('modelscope.snapshot_download', download)
    assert resolve_tokenizer_directory('Qwen/Qwen2.5-0.5B-Instruct') == str(tmp_path)
    model_id, patterns = calls[0]
    assert model_id == 'Qwen/Qwen2.5-0.5B-Instruct'
    assert {'tokenizer.json', 'tokenizer_config.json', 'chat_template.jinja'} <= set(patterns)
    assert not any(pattern.endswith(('.safetensors', '.bin', '.pt')) for pattern in patterns)


def test_cached_snapshot_is_preferred_over_the_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def download(model_id: str, local_files_only: bool = False, **kwargs) -> str:
        calls.append(local_files_only)
        if local_files_only:
            raise OSError('no cached snapshot')
        return str(tmp_path)

    monkeypatch.setattr('modelscope.snapshot_download', download)
    assert resolve_tokenizer_directory('Qwen/Qwen2.5-0.5B-Instruct') == str(tmp_path)
    assert calls == [True, False]


def test_missing_local_paths_are_never_treated_as_model_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    def download(*args, **kwargs) -> str:
        raise AssertionError('A local-looking path must not trigger a download')

    monkeypatch.setattr('modelscope.snapshot_download', download)
    for reference in ('/missing/tokenizer', './missing', '../missing', r'C:\missing', '~/.cache/missing'):
        with pytest.raises(OSError, match='not readable'):
            resolve_tokenizer_directory(reference)


def test_download_failures_are_reported_with_the_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def download(model_id: str, **kwargs) -> str:
        raise RuntimeError('repository not found')

    monkeypatch.setattr('modelscope.snapshot_download', download)
    with pytest.raises(OSError, match='ModelScope tokenizer download failed for Qwen/unknown'):
        resolve_tokenizer_directory('Qwen/unknown')


@pytest.mark.parametrize('reference', ['', '   ', 'single-name', 'a/b/c', 'org/'])
def test_unsupported_sources_are_rejected(reference: str) -> None:
    with pytest.raises(ValueError):
        resolve_tokenizer_directory(reference)


def test_downloaded_tokenizer_is_loaded_and_rendered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / 'cache'
    _save_tokenizer(directory)
    monkeypatch.setattr('modelscope.snapshot_download', lambda model_id, **kwargs: str(directory))
    adapter = HuggingFacePrefixTokenizer('Qwen/Qwen2.5-0.5B-Instruct')
    assert adapter.directory == str(directory)
    assert len(adapter.render('t4')) == 4
    assert adapter.candidate_ids(2) == [1, 2]


def test_local_tokenizer_exposes_its_directory(tmp_path: Path) -> None:
    _save_tokenizer(tmp_path)
    assert HuggingFacePrefixTokenizer(str(tmp_path)).directory == str(tmp_path)


def test_modelscope_without_allow_patterns_still_returns_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def download(model_id: str, allow_patterns=None, allow_file_pattern=None, **kwargs) -> str:
        if allow_patterns is not None:
            raise TypeError('unexpected keyword argument allow_patterns')
        return str(tmp_path)

    monkeypatch.setattr('modelscope.snapshot_download', download)
    assert resolve_tokenizer_directory('Qwen/Qwen2.5-0.5B-Instruct') == str(tmp_path)
