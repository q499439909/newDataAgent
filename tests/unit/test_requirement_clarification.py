from __future__ import annotations

from dataagent.agents.requirement.clarification import (
    infer_task_ambiguities,
    recommended_clarification_patch,
)
from dataagent.domain.specs import TaskCapabilitySpec


def _capability(name: str) -> TaskCapabilitySpec:
    return TaskCapabilitySpec(
        id=name,
        capability=name,
        description=name,
    )


def test_ambiguities_are_structured_missing_fields_not_fixed_questions() -> None:
    missing = infer_task_ambiguities(
        (
            _capability("authenticity_assessment"),
            _capability("class_resolution"),
            _capability("dataset_partition"),
        ),
        hard_constraints={},
        semantic_requirements=(),
        exclusion_requirements=(),
        preferences={"unknown_policy": "reject"},
    )

    assert missing == (
        "hard_constraints.authenticity_scope",
        "preferences.mixed_policy",
        "hard_constraints.preserve_source",
        "preferences.output_layout",
    )


def test_recommended_defaults_are_structured_and_task_neutral() -> None:
    patch = recommended_clarification_patch(
        (
            "hard_constraints.authenticity_scope",
            "preferences.mixed_policy",
            "preferences.unknown_policy",
            "hard_constraints.preserve_source",
            "preferences.output_layout",
        )
    )

    assert patch == {
        "hard_constraints": {
            "authenticity_scope": {"uncertain_policy": "review"},
            "preserve_source": True,
        },
        "preferences": {
            "mixed_policy": "review",
            "unknown_policy": "review",
            "output_layout": "versioned_class_directories",
        },
    }
