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
