from __future__ import annotations

from typing import Any

from ...domain.specs import TaskCapabilitySpec


AUTHENTICITY_SCOPE_FIELD = "hard_constraints.authenticity_scope"
MIXED_POLICY_FIELD = "preferences.mixed_policy"
UNKNOWN_POLICY_FIELD = "preferences.unknown_policy"
PRESERVE_SOURCE_FIELD = "hard_constraints.preserve_source"
OUTPUT_LAYOUT_FIELD = "preferences.output_layout"


def infer_task_ambiguities(
    capabilities: tuple[TaskCapabilitySpec, ...],
    *,
    hard_constraints: dict[str, Any],
    semantic_requirements: tuple[str, ...],
    exclusion_requirements: tuple[str, ...],
    preferences: dict[str, Any],
) -> tuple[str, ...]:
    names = {item.capability for item in capabilities}
    missing: list[str] = []

    if "authenticity_assessment" in names:
        has_scope = bool(hard_constraints.get("authenticity_scope")) or bool(
            semantic_requirements or exclusion_requirements
        )
        if not has_scope:
            missing.append(AUTHENTICITY_SCOPE_FIELD)

    if "class_resolution" in names:
        if preferences.get("mixed_policy") is None:
            missing.append(MIXED_POLICY_FIELD)
        if preferences.get("unknown_policy") is None:
            missing.append(UNKNOWN_POLICY_FIELD)

    if "dataset_partition" in names:
        if hard_constraints.get("preserve_source") is None:
            missing.append(PRESERVE_SOURCE_FIELD)
        if not preferences.get("output_layout"):
            missing.append(OUTPUT_LAYOUT_FIELD)

    return tuple(missing)


def recommended_clarification_patch(
    ambiguities: tuple[str, ...],
) -> dict[str, Any]:
    hard_constraints: dict[str, Any] = {}
    preferences: dict[str, Any] = {}
    missing = set(ambiguities)
    if AUTHENTICITY_SCOPE_FIELD in missing:
        hard_constraints["authenticity_scope"] = {
            "uncertain_policy": "review"
        }
    if MIXED_POLICY_FIELD in missing:
        preferences["mixed_policy"] = "review"
    if UNKNOWN_POLICY_FIELD in missing:
        preferences["unknown_policy"] = "review"
    if PRESERVE_SOURCE_FIELD in missing:
        hard_constraints["preserve_source"] = True
    if OUTPUT_LAYOUT_FIELD in missing:
        preferences["output_layout"] = "versioned_class_directories"
    patch: dict[str, Any] = {}
    if hard_constraints:
        patch["hard_constraints"] = hard_constraints
    if preferences:
        patch["preferences"] = preferences
    return patch


__all__ = [
    "AUTHENTICITY_SCOPE_FIELD",
    "MIXED_POLICY_FIELD",
    "OUTPUT_LAYOUT_FIELD",
    "PRESERVE_SOURCE_FIELD",
    "UNKNOWN_POLICY_FIELD",
    "infer_task_ambiguities",
    "recommended_clarification_patch",
]
