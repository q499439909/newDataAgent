from __future__ import annotations

from typing import Any

from ...domain.specs import TaskCapabilitySpec


AUTHENTICITY_SCOPE_QUESTION = (
    "请明确“不真实/非实拍直出”的范围：是否排除插画、截图、明显合成、"
    "美颜滤镜和后期调色照片？"
)
CLASS_POLICY_QUESTION = (
    "猫狗同时出现或无法判断类别的图片，应保留、进入人工复核还是直接排除？"
)
OUTPUT_POLICY_QUESTION = (
    "是否按默认安全方式复制到新的版本化分类目录，并保持源目录只读？"
)


def infer_task_ambiguities(
    capabilities: tuple[TaskCapabilitySpec, ...],
    *,
    hard_constraints: dict[str, Any],
    semantic_requirements: tuple[str, ...],
    exclusion_requirements: tuple[str, ...],
    preferences: dict[str, Any],
) -> tuple[str, ...]:
    names = {item.capability for item in capabilities}
    ambiguities: list[str] = []

    if "authenticity_assessment" in names:
        has_scope = bool(hard_constraints.get("authenticity_scope")) or bool(
            semantic_requirements or exclusion_requirements
        )
        if not has_scope:
            ambiguities.append(AUTHENTICITY_SCOPE_QUESTION)

    if "class_resolution" in names:
        if not all(key in preferences for key in ("mixed_policy", "unknown_policy")):
            ambiguities.append(CLASS_POLICY_QUESTION)

    if "dataset_partition" in names:
        output_defined = (
            "preserve_source" in hard_constraints
            and (
                bool(hard_constraints.get("output_directory"))
                or bool(preferences.get("output_layout"))
            )
        )
        if not output_defined:
            ambiguities.append(OUTPUT_POLICY_QUESTION)

    return tuple(ambiguities)


def recommended_clarification_patch(ambiguities: tuple[str, ...]) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "hard_constraints": {},
        "preferences": {},
        "semantic_requirements": [],
        "exclusion_requirements": [],
    }
    if AUTHENTICITY_SCOPE_QUESTION in ambiguities:
        patch["hard_constraints"]["authenticity_scope"] = (
            "Exclude AI-generated, composited, illustrated, screenshot, heavily filtered, "
            "beautified, or materially retouched images; uncertain cases require review."
        )
        patch["exclusion_requirements"].append(
            "排除 AI 生成、明显合成、插画、截图、重度滤镜、美颜或明显后期修改的图片；"
            "无法判断时进入复核。"
        )
    if CLASS_POLICY_QUESTION in ambiguities:
        patch["preferences"].update(
            {"mixed_policy": "review", "unknown_policy": "review"}
        )
    if OUTPUT_POLICY_QUESTION in ambiguities:
        patch["hard_constraints"]["preserve_source"] = True
        patch["preferences"]["output_layout"] = "versioned_class_directories"
        patch["semantic_requirements"].append(
            "将保留图片复制到新的版本化分类目录，源目录保持只读。"
        )
    return patch


__all__ = [
    "AUTHENTICITY_SCOPE_QUESTION",
    "CLASS_POLICY_QUESTION",
    "OUTPUT_POLICY_QUESTION",
    "infer_task_ambiguities",
    "recommended_clarification_patch",
]
