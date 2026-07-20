from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..domain.operators import OperatorStatus, RuntimeBackend
from .registry import OperatorRegistry


class OperatorCatalogMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: str
    operator_version_id: str
    provider_operator_ref: str
    display_name: str
    score: int = Field(ge=0)
    matched_terms: tuple[str, ...] = ()
    runtime_backend: RuntimeBackend
    status: OperatorStatus
    executable: bool
    blocked_reason: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class _CatalogIntent:
    name: str
    provider_ref: str
    keywords: tuple[str, ...]
    capabilities: tuple[str, ...] = ()


_INTENTS = (
    _CatalogIntent(
        "deduplication",
        "image_deduplicator",
        ("去重", "重复图片", "重复图", "deduplicate", "dedup", "duplicate images"),
    ),
    _CatalogIntent(
        "image_shape",
        "image_shape_filter",
        ("分辨率", "图片尺寸", "图像尺寸", "宽度", "高度", "像素", "resolution", "dimensions"),
    ),
    _CatalogIntent(
        "aspect_ratio",
        "image_aspect_ratio_filter",
        ("宽高比", "长宽比", "纵横比", "aspect ratio"),
    ),
    _CatalogIntent(
        "face_count",
        "image_face_count_filter",
        ("人脸数量", "人脸个数", "几张脸", "face count", "number of faces"),
    ),
    _CatalogIntent(
        "face_ratio",
        "image_face_ratio_filter",
        ("人脸占比", "人脸面积", "脸部占比", "face ratio", "face area"),
    ),
    _CatalogIntent(
        "file_size",
        "image_size_filter",
        ("文件大小", "文件尺寸", "file size", "image bytes"),
    ),
    _CatalogIntent(
        "subplot",
        "image_subplot_filter",
        ("拼图", "宫格图", "多宫格", "子图", "subplot", "grid image"),
    ),
    _CatalogIntent(
        "face_blur",
        "image_face_blur_mapper",
        ("人脸模糊", "人脸打码", "脸部打码", "blur faces", "face blur"),
    ),
    _CatalogIntent(
        "remove_background",
        "image_remove_background_mapper",
        ("去背景", "移除背景", "抠图", "remove background", "background removal"),
    ),
    _CatalogIntent(
        "image_blur",
        "image_blur_mapper",
        ("添加模糊", "模糊处理", "blur image", "apply blur"),
    ),
    _CatalogIntent(
        "aesthetic_score",
        "image_aesthetics_filter",
        ("美学", "审美", "aesthetic"),
        ("aesthetic_score",),
    ),
    _CatalogIntent(
        "watermark_detection",
        "image_watermark_filter",
        ("水印", "watermark"),
        ("watermark_detection",),
    ),
    _CatalogIntent(
        "segmentation",
        "image_segment_mapper",
        ("图像分割", "图片分割", "目标分割", "segmentation", "segment image"),
        ("segmentation",),
    ),
    _CatalogIntent(
        "object_detection",
        "image_detection_yolo_mapper",
        ("目标检测", "物体检测", "object detection", "detect objects"),
    ),
    _CatalogIntent(
        "image_tagging",
        "image_tagging_mapper",
        (
            "图片标签",
            "图像标签",
            "自动打标",
            "图片分类",
            "图像分类",
            "猫狗分类",
            "猫和狗的图片分开",
            "按类别分开",
            "image tagging",
            "generate tags",
            "classify images",
            "separate cats and dogs",
        ),
    ),
    _CatalogIntent(
        "nsfw_filtering",
        "image_nsfw_filter",
        ("色情", "不良内容", "nsfw", "unsafe image"),
    ),
    _CatalogIntent(
        "image_pair_similarity",
        "image_pair_similarity_filter",
        ("图片相似度", "图像对相似度", "image pair similarity", "similar images"),
    ),
    _CatalogIntent(
        "human_pose",
        "image_mmpose_mapper",
        ("人体姿态", "人体关键点", "姿态估计", "human pose", "keypoint detection"),
    ),
    _CatalogIntent(
        "body_3d",
        "image_sam_3d_body_mapper",
        ("人体3d", "人体 3d", "三维人体", "3d body", "human mesh"),
    ),
    _CatalogIntent(
        "vlm_processing",
        "vlm_ray_vllm_engine_pipeline",
        ("视觉大模型处理", "vlm pipeline", "vllm vision"),
    ),
)


def _suggest_parameters(provider_ref: str, requirement: str) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    if provider_ref == "image_shape_filter":
        patterns = {
            "min_width": (
                r"宽(?:度)?(?:至少|不小于|大于等于|>=)\s*(\d+)",
                r"min(?:imum)?\s+width\s*(?:is|of|>=)?\s*(\d+)",
            ),
            "min_height": (
                r"高(?:度)?(?:至少|不小于|大于等于|>=)\s*(\d+)",
                r"min(?:imum)?\s+height\s*(?:is|of|>=)?\s*(\d+)",
            ),
        }
        for name, candidates in patterns.items():
            for pattern in candidates:
                match = re.search(pattern, requirement, flags=re.IGNORECASE)
                if match:
                    parameters[name] = int(match.group(1))
                    break
    return parameters


class DataJuicerCatalogMatcher:
    """Matches explicit task intents against the versioned Data-Juicer catalog."""

    def __init__(self, registry: OperatorRegistry) -> None:
        self.registry = registry

    def match(
        self,
        requirement: str,
        *,
        required_capabilities: tuple[str, ...] = (),
        allow_draft_candidates: bool = True,
    ) -> tuple[OperatorCatalogMatch, ...]:
        normalized = " ".join(requirement.lower().split())
        by_ref = {
            item.provider.provider_operator_ref: item
            for item in self.registry.search(include_drafts=True)
            if item.provider.provider_id == "datajuicer"
        }
        matches: list[OperatorCatalogMatch] = []
        for intent in _INTENTS:
            terms = tuple(term for term in intent.keywords if term in normalized)
            if intent.provider_ref in normalized:
                terms = (*terms, intent.provider_ref)
            capability_hit = tuple(
                capability
                for capability in intent.capabilities
                if capability in required_capabilities
            )
            if not terms and not capability_hit:
                continue
            operator = by_ref.get(intent.provider_ref)
            if operator is None:
                continue
            backends = {
                profile.backend for profile in operator.supported_runtime_profiles
            }
            backend = (
                RuntimeBackend.CPU
                if RuntimeBackend.CPU in backends
                else RuntimeBackend.CUDA
                if RuntimeBackend.CUDA in backends
                else next(iter(backends))
            )
            blocked_reason = None
            if "image" not in operator.capability_tags:
                blocked_reason = "The current Agent only executes image operators"
            elif backend != RuntimeBackend.CPU:
                blocked_reason = f"Runtime backend {backend.value} is not available"
            elif operator.status == OperatorStatus.DRAFT and not allow_draft_candidates:
                blocked_reason = "Draft candidate execution is disabled"
            matches.append(
                OperatorCatalogMatch(
                    intent=intent.name,
                    operator_version_id=operator.id,
                    provider_operator_ref=intent.provider_ref,
                    display_name=operator.display_name,
                    score=100 + 10 * len(terms) + 20 * len(capability_hit),
                    matched_terms=(*terms, *capability_hit),
                    runtime_backend=backend,
                    status=operator.status,
                    executable=blocked_reason is None,
                    blocked_reason=blocked_reason,
                    parameters=_suggest_parameters(intent.provider_ref, requirement),
                )
            )
        return tuple(
            sorted(matches, key=lambda item: (-item.score, item.operator_version_id))
        )


__all__ = ["DataJuicerCatalogMatcher", "OperatorCatalogMatch"]
