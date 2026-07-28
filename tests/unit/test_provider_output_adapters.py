import pytest

from dataagent.operators.providers.output_adapters import adapt_provider_output


def test_image_tag_adapter_normalizes_supported_provider_shapes() -> None:
    for value in (
        [["Cat", "natural photo"]],
        "Cat, natural photo",
        {"tags": ["Cat", "natural photo"]},
        {"tag": "Cat, natural photo"},
    ):
        result = adapt_provider_output(
            "image_tagging_vlm_mapper",
            {"tag_field_name": "image_tags"},
            {"image_tags": value},
        )
        assert result.errors == ()
        assert result.fields["image_tags"] == ["cat", "natural-photo"]
        assert result.fields["image_tags__provider_raw"] == value
        assert result.contract_id == "image_tag_set:1"


def test_image_tag_adapter_rejects_empty_semantics_without_guessing() -> None:
    result = adapt_provider_output(
        "image_tagging_vlm_mapper",
        {"tag_field_name": "authenticity_tags"},
        {"authenticity_tags": [[]]},
    )

    assert result.fields["authenticity_tags"] == []
    assert result.errors == ("authenticity_tags is empty or malformed",)


def test_unknown_operator_output_is_preserved_without_global_tag_assumptions() -> None:
    fields = {"score": 0.75, "answer": "free-form"}

    result = adapt_provider_output("another_datajuicer_operator", {}, fields)

    assert result.fields == fields
    assert result.contract_id is None
    assert result.errors == ()


def test_face_count_adapter_exposes_filter_stat_as_constraint_evidence() -> None:
    result = adapt_provider_output(
        "image_face_count_filter",
        {"min_face_count": 0, "max_face_count": 1},
        {"__dj__stats__": {"face_counts": [1]}},
    )

    assert result.errors == ()
    assert result.metrics == {"face_count": 1, "image.face_count": 1}
    assert result.fields["face_count"] == 1
    assert result.contract_id == "detected_face_count:1"


@pytest.mark.parametrize(
    ("operator_ref", "stats", "expected_metrics", "contract_id"),
    [
        (
            "image_shape_filter",
            {"image_width": [80], "image_height": [120]},
            {
                "width": 80,
                "height": 120,
                "image.width": 80,
                "image.height": 120,
            },
            "image_dimensions:1",
        ),
        (
            "image_aspect_ratio_filter",
            {"aspect_ratios": [0.6667]},
            {"aspect_ratio": 0.6667, "image.aspect_ratio": 0.6667},
            "image_aspect_ratio:1",
        ),
        (
            "image_size_filter",
            {"image_sizes": [4096]},
            {"file_size_bytes": 4096, "file.size": 4096},
            "source_file_size_bytes:1",
        ),
    ],
)
def test_metadata_filter_adapters_expose_constraint_evidence(
    operator_ref,
    stats,
    expected_metrics,
    contract_id,
) -> None:
    result = adapt_provider_output(
        operator_ref,
        {},
        {"__dj__stats__": stats},
    )

    assert result.metrics == expected_metrics
    assert result.contract_id == contract_id
