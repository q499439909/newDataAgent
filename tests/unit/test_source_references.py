from pathlib import Path

import pytest

from dataagent.application.source_references import (
    AmbiguousSourceReference,
    extract_explicit_data_sources,
)


def test_extracts_the_reported_existing_source_before_adjacent_text(
    monkeypatch,
) -> None:
    expected = Path(r"D:\data\mix0")
    monkeypatch.setattr(
        Path,
        "exists",
        lambda candidate: str(candidate) == str(expected),
    )

    assert extract_explicit_data_sources(
        r"D:\data\mix0把这个数据集里的猫狗分类"
    )[0]["uri"] == str(expected)


def test_extracts_longest_existing_windows_source_before_adjacent_text(
    tmp_path,
) -> None:
    source = tmp_path / "mix0"
    source.mkdir()

    assert extract_explicit_data_sources(
        f"{source}把这个数据集里的内容分类"
    ) == [
        {
            "type": "local_directory",
            "uri": str(source),
            "mapping": {},
        }
    ]


def test_extracts_existing_source_before_different_adjacent_semantics(
    tmp_path,
) -> None:
    source = tmp_path / "batch7"
    source.mkdir()

    assert extract_explicit_data_sources(
        f"{source}transcribe_every_record"
    )[0]["uri"] == str(source)


def test_extracts_windows_source_without_task_keyword_rules() -> None:
    assert extract_explicit_data_sources(
        r"D:\records\incoming apply the requested policy."
    ) == [
        {
            "type": "local_directory",
            "uri": r"D:\records\incoming",
            "mapping": {},
        }
    ]


def test_extracts_typed_sources_across_modalities() -> None:
    assert extract_explicit_data_sources(
        "Analyze s3://bucket/audio and milvus://cluster/collection."
    ) == [
        {
            "type": "object_storage",
            "uri": "s3://bucket/audio",
            "mapping": {},
        },
        {
            "type": "milvus",
            "uri": "milvus://cluster/collection",
            "mapping": {},
        },
    ]


def test_supports_quoted_windows_paths_with_spaces() -> None:
    assert extract_explicit_data_sources(
        r'Process "D:\incoming records\batch 7" under the supplied contract.'
    )[0]["uri"] == r"D:\incoming records\batch 7"


def test_preserves_a_quoted_nonexistent_windows_path_verbatim() -> None:
    assert extract_explicit_data_sources(
        r'Process "D:\尚未创建的数据 批次\batch 7" later.'
    )[0]["uri"] == r"D:\尚未创建的数据 批次\batch 7"


def test_rejects_an_unquoted_nonexistent_source_without_a_boundary() -> None:
    with pytest.raises(AmbiguousSourceReference):
        extract_explicit_data_sources(
            r"Z:\not-created\batch处理这些文件"
        )


def test_does_not_infer_a_source_from_ordinary_requirement_text() -> None:
    assert extract_explicit_data_sources(
        "Keep ratios between 0.3 and 3.5 and ask if a source is missing."
    ) == []
