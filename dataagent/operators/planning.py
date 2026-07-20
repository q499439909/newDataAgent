from __future__ import annotations

from dataclasses import dataclass

from ..domain.operators import (
    OperatorCategory,
    OperatorSpecVersion,
    OperatorStatus,
    RuntimeBackend,
)
from .registry import OperatorRegistry
from ..domain.specs import TaskCapabilitySpec


class CapabilityGapError(LookupError):
    pass


@dataclass(frozen=True)
class OperatorRequirement:
    capability: str
    category: OperatorCategory | None = None
    secondary_category: str | None = None
    runtime_backend: RuntimeBackend | None = None
    allow_drafts: bool = False


_STATUS_RANK = {
    OperatorStatus.PUBLIC_RELEASE: 0,
    OperatorStatus.PERSONAL_RELEASE: 1,
    OperatorStatus.EVALUATED: 2,
    OperatorStatus.DRAFT: 3,
    OperatorStatus.DEPRECATED: 99,
}


class OperatorSelector:
    def __init__(self, registry: OperatorRegistry) -> None:
        self.registry = registry

    def select(self, requirement: OperatorRequirement) -> OperatorSpecVersion:
        candidates = self.registry.search(
            category=requirement.category,
            secondary_category=requirement.secondary_category,
            tags={requirement.capability},
            include_drafts=requirement.allow_drafts,
        )
        candidates = [item for item in candidates if item.status != OperatorStatus.DEPRECATED]
        if requirement.runtime_backend is not None:
            candidates = [
                item
                for item in candidates
                if requirement.runtime_backend
                in {profile.backend for profile in item.supported_runtime_profiles}
            ]
        if not candidates:
            raise CapabilityGapError(
                "No operator satisfies capability "
                f"'{requirement.capability}' with category={requirement.category}, "
                f"secondary={requirement.secondary_category}, "
                f"backend={requirement.runtime_backend}"
            )
        return min(
            candidates,
            key=lambda item: (
                _STATUS_RANK[item.status],
                item.version * -1,
                item.id,
            ),
        )


_CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "aesthetic_score": ("美学", "审美", "aesthetic"),
    "segmentation": (
        "图像分割",
        "图片分割",
        "目标分割",
        "segmentation",
        "segment",
        "mask",
    ),
    "watermark_detection": ("水印", "watermark"),
    "face_identity": (
        "人像id",
        "人物id",
        "人脸id",
        "face id",
        "face identity",
        "identity match",
    ),
}


_TASK_CAPABILITY_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "image_quality",
        "Reject unclear, blurred, corrupted, or low-quality images.",
        (
            "不清晰",
            "模糊",
            "清晰",
            "低质量",
            "图片质量",
            "image quality",
            "blurry",
            "blurred",
            "clear images",
        ),
    ),
    (
        "authenticity_assessment",
        "Assess whether image content is authentic or artificially generated.",
        (
            "不真实",
            "真实性",
            "虚假图片",
            "ai生成",
            "ai 生成",
            "aigc",
            "synthetic image",
            "authenticity",
            "fake image",
        ),
    ),
    (
        "image_classification",
        "Assign task-specific semantic classes to each image.",
        (
            "猫和狗",
            "猫狗",
            "图片分类",
            "图像分类",
            "按类别",
            "classify images",
            "cats and dogs",
            "cat and dog",
        ),
    ),
    (
        "perceptual_deduplication",
        "Remove perceptually duplicate images at dataset scope.",
        ("去重", "重复图片", "重复图", "deduplicate", "duplicate images"),
    ),
)


def decompose_task_capabilities(requirement: str) -> tuple[TaskCapabilitySpec, ...]:
    normalized = " ".join(requirement.lower().split())
    selected = [
        (capability, description)
        for capability, description, keywords in _TASK_CAPABILITY_RULES
        if any(keyword in normalized for keyword in keywords)
    ]
    specialized = [
        capability
        for capability, keywords in _CAPABILITY_KEYWORDS.items()
        if any(keyword in normalized for keyword in keywords)
    ]
    descriptions = {
        "aesthetic_score": "Score image aesthetics using a governed semantic evaluator.",
        "segmentation": "Produce structured image segmentation annotations.",
        "watermark_detection": "Detect images that contain watermarks.",
        "face_identity": "Match person or face identity using governed references.",
    }
    selected.extend((item, descriptions[item]) for item in specialized)

    capabilities: list[TaskCapabilitySpec] = [
        TaskCapabilitySpec(
            id="image_decode",
            capability="image_decode",
            description="Decode images and reject corrupt or unsupported assets.",
        )
    ]
    previous = "image_decode"
    seen = {previous}
    for capability, description in selected:
        if capability in seen:
            continue
        capabilities.append(
            TaskCapabilitySpec(
                id=capability,
                capability=capability,
                description=description,
                depends_on=(previous,),
            )
        )
        previous = capability
        seen.add(capability)

    classification_requested = "image_classification" in seen
    separation_requested = any(
        keyword in normalized
        for keyword in ("分开", "归类", "分类目录", "separate", "partition")
    )
    if classification_requested and separation_requested:
        capabilities.append(
            TaskCapabilitySpec(
                id="class_resolution",
                capability="class_resolution",
                description="Resolve labels into task classes including mixed and unknown.",
                depends_on=(previous,),
            )
        )
        previous = "class_resolution"
        capabilities.append(
            TaskCapabilitySpec(
                id="dataset_partition",
                capability="dataset_partition",
                description="Publish classified assets into deterministic dataset partitions.",
                depends_on=(previous,),
            )
        )
        previous = "dataset_partition"

    capabilities.append(
        TaskCapabilitySpec(
            id="manifest",
            capability="manifest",
            description="Publish an immutable manifest with decisions and provenance.",
            depends_on=(previous,),
        )
    )
    return tuple(capabilities)


def infer_output_actions(
    capabilities: tuple[TaskCapabilitySpec, ...],
) -> tuple[str, ...]:
    names = {item.capability for item in capabilities}
    actions: list[str] = []
    if names.intersection({"image_quality", "authenticity_assessment"}):
        actions.append("filter")
    if "image_classification" in names:
        actions.append("classify")
    if "perceptual_deduplication" in names:
        actions.append("deduplicate")
    if "dataset_partition" in names:
        actions.append("partition")
    actions.append("manifest")
    return tuple(actions)


def infer_required_capabilities(requirement: str) -> tuple[str, ...]:
    normalized = " ".join(requirement.lower().split())
    return tuple(
        capability
        for capability, keywords in _CAPABILITY_KEYWORDS.items()
        if any(keyword in normalized for keyword in keywords)
    )


MODEL_CAPABILITY_REQUIREMENTS: dict[str, OperatorRequirement] = {
    "aesthetic_score": OperatorRequirement(
        capability="aesthetic_score",
        category=OperatorCategory.UNDERSTANDING,
        secondary_category="aesthetic_understanding",
        runtime_backend=RuntimeBackend.MOCK,
        allow_drafts=True,
    ),
    "segmentation": OperatorRequirement(
        capability="segmentation",
        category=OperatorCategory.UNDERSTANDING,
        secondary_category="segmentation",
        runtime_backend=RuntimeBackend.MOCK,
        allow_drafts=True,
    ),
    "watermark_detection": OperatorRequirement(
        capability="watermark_detection",
        category=OperatorCategory.UNDERSTANDING,
        secondary_category="watermark_detection",
        runtime_backend=RuntimeBackend.MOCK,
        allow_drafts=True,
    ),
    "face_identity": OperatorRequirement(
        capability="face_identity",
        category=OperatorCategory.UNDERSTANDING,
        secondary_category="face_and_person",
        runtime_backend=RuntimeBackend.MOCK,
        allow_drafts=True,
    ),
}


__all__ = [
    "CapabilityGapError",
    "MODEL_CAPABILITY_REQUIREMENTS",
    "OperatorRequirement",
    "OperatorSelector",
    "decompose_task_capabilities",
    "infer_required_capabilities",
    "infer_output_actions",
]
