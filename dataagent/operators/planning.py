from __future__ import annotations

from dataclasses import dataclass

from ..domain.operators import (
    OperatorCategory,
    OperatorSpecVersion,
    OperatorStatus,
    RuntimeBackend,
)
from .registry import OperatorRegistry


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
    "infer_required_capabilities",
]
