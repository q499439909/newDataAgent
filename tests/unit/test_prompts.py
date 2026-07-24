from __future__ import annotations

import hashlib

import pytest

from dataagent.prompts import builtin_prompt_registry


def test_builtin_prompt_registry_resolves_versioned_classification_prompt() -> None:
    resolved = builtin_prompt_registry().resolve(
        "closed-set-image-classification",
        1,
        variables={"allowed_labels": "pig, dog, mixed, unknown"},
    )

    assert "pig, dog, mixed, unknown" in resolved.text
    assert resolved.binding.template_id == "closed-set-image-classification"
    assert resolved.binding.template_version == 1
    assert resolved.binding.resolved_sha256 == hashlib.sha256(
        resolved.text.encode("utf-8")
    ).hexdigest()
    assert resolved.output_contract["type"] == "tag_enum"


def test_builtin_prompt_registry_resolves_shared_visual_tagging_prompt() -> None:
    resolved = builtin_prompt_registry().resolve(
        "image-task-visual-tagging",
        1,
        variables={
            "task_scope_instruction": "Exclude screenshots.",
            "allowed_labels": "pig, dog, mixed, unknown",
            "semantic_requirements": "Subject is a pig or dog.",
            "exclusion_requirements": "No other animals.",
        },
    )

    assert "Exclude screenshots." in resolved.text
    assert "pig, dog, mixed, unknown" in resolved.text
    assert resolved.binding.template_id == "image-task-visual-tagging"
    assert resolved.output_contract["type"] == "tag_set"


def test_shared_visual_tagging_v3_uses_task_specific_tag_groups() -> None:
    resolved = builtin_prompt_registry().resolve(
        "image-task-visual-tagging",
        3,
        variables={
            "task_objective": "Keep authentic black-clothing photos.",
            "semantic_requirements": "The person wears black clothing.",
            "exclusion_requirements": "Exclude synthetic images.",
            "classification_contract": [],
            "task_scope_instruction": "Exclude edited photos.",
            "tag_contract_instruction": (
                "Group 1: exactly one of authentic, synthetic, uncertain. "
                "Group 2: exactly one of semantic_match, semantic_mismatch, "
                "semantic_uncertain."
            ),
        },
    )

    assert "Group 1: exactly one of authentic" in resolved.text
    assert "semantic_match" in resolved.text
    assert resolved.binding.template_version == 3


def test_semantic_selection_v2_keeps_objective_and_label_context() -> None:
    resolved = builtin_prompt_registry().resolve(
        "image-semantic-selection",
        2,
        variables={
            "task_objective": "筛选出穿黑色衣服的图片",
            "semantic_requirements": "筛选出穿黑色衣服的图片",
            "exclusion_requirements": "排除不属于上述类别的图片",
            "classification_contract": [
                {
                    "id": "black_clothing",
                    "display_name": "穿了黑色衣服",
                    "aliases": ["黑色衣物"],
                }
            ],
        },
    )

    assert "筛选出穿黑色衣服的图片" in resolved.text
    assert '"black_clothing"' in resolved.text
    assert "Required visual conditions: none" not in resolved.text


def test_prompt_registry_rejects_missing_or_extra_variables() -> None:
    registry = builtin_prompt_registry()

    with pytest.raises(ValueError, match="Prompt variables"):
        registry.resolve(
            "closed-set-image-classification",
            1,
            variables={},
        )
    with pytest.raises(ValueError, match="Prompt variables"):
        registry.resolve(
            "closed-set-image-classification",
            1,
            variables={"allowed_labels": "cat, dog", "unexpected": True},
        )
