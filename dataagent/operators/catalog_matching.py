from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from ..domain.operators import OperatorSpecVersion, OperatorStatus, RuntimeBackend
from ..domain.specs import TaskCapabilitySpec
from .registry import OperatorRegistry


class OperatorCatalogMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: str
    capability: str = ""
    operator_version_id: str
    provider_id: str = "datajuicer"
    provider_operator_ref: str
    display_name: str
    score: int = Field(ge=0)
    matched_terms: tuple[str, ...] = ()
    recall_sources: tuple[str, ...] = ()
    rule_score: int = Field(default=0, ge=0)
    keyword_score: int = Field(default=0, ge=0)
    semantic_score: int = Field(default=0, ge=0)
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
        "perceptual_deduplication",
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
        "image_classification",
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
        ("image_classification",),
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
        "visual_understanding",
        "vlm_ray_vllm_engine_pipeline",
        ("视觉大模型处理", "vlm pipeline", "vllm vision"),
    ),
)


_SEMANTIC_CONCEPTS: dict[str, frozenset[str]] = {
    "image_quality": frozenset({"quality", "image_quality", "blur", "aesthetic"}),
    "authenticity_assessment": frozenset(
        {"authenticity", "aigc", "synthetic", "fake", "vlm_judgement"}
    ),
    "image_classification": frozenset(
        {
            "classification",
            "image_classification",
            "image_tagging",
            "tagging",
            "visual_understanding",
        }
    ),
    "perceptual_deduplication": frozenset(
        {"deduplication", "deduplicator", "duplicate", "perceptual_duplicate"}
    ),
    "aesthetic_score": frozenset({"aesthetic", "aesthetic_score"}),
    "segmentation": frozenset({"segment", "segmentation", "mask"}),
    "watermark_detection": frozenset({"watermark", "watermark_detection"}),
    "face_identity": frozenset({"face_identity", "identity", "face_and_person"}),
}


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


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in re.findall(r"[a-z0-9]+", value.lower().replace("_", " "))
        if len(token) >= 3 and token not in {"the", "and", "for", "with", "image"}
    )


def _operator_terms(operator: OperatorSpecVersion) -> frozenset[str]:
    text = " ".join(
        (
            operator.id,
            operator.display_name,
            operator.summary,
            operator.description,
            operator.secondary_category,
            " ".join(operator.capability_tags),
        )
    )
    return frozenset({*_tokens(text), *operator.capability_tags})


def _capability_names(
    required_capabilities: tuple[str, ...],
    capability_requirements: Iterable[TaskCapabilitySpec | dict[str, Any]],
) -> tuple[str, ...]:
    values = list(required_capabilities)
    for item in capability_requirements:
        capability = (
            item.capability
            if isinstance(item, TaskCapabilitySpec)
            else str(item.get("capability", ""))
        )
        if capability and capability not in values:
            values.append(capability)
    return tuple(values)


class HybridOperatorCatalogMatcher:
    """Explainable rule, lexical, and capability-semantic catalog recall."""

    def __init__(
        self,
        registry: OperatorRegistry,
        *,
        provider_ids: frozenset[str] | None = None,
    ) -> None:
        self.registry = registry
        self.provider_ids = provider_ids

    def match(
        self,
        requirement: str,
        *,
        required_capabilities: tuple[str, ...] = (),
        capability_requirements: Iterable[TaskCapabilitySpec | dict[str, Any]] = (),
        allow_draft_candidates: bool = True,
    ) -> tuple[OperatorCatalogMatch, ...]:
        normalized = " ".join(requirement.lower().split())
        capabilities = _capability_names(
            required_capabilities,
            capability_requirements,
        )
        operators = [
            item
            for item in self.registry.search(include_drafts=True)
            if not self.provider_ids or item.provider.provider_id in self.provider_ids
        ]
        evidence: dict[tuple[str, str], dict[str, Any]] = {}

        def recall(
            capability: str,
            operator: OperatorSpecVersion,
            source: str,
            points: int,
            terms: Iterable[str],
        ) -> None:
            key = (capability, operator.id)
            item = evidence.setdefault(
                key,
                {
                    "operator": operator,
                    "sources": set(),
                    "terms": set(),
                    "scores": {"rule": 0, "keyword": 0, "semantic": 0},
                },
            )
            item["sources"].add(source)
            item["terms"].update(term for term in terms if term)
            item["scores"][source] = max(item["scores"][source], points)

        by_ref: dict[str, list[OperatorSpecVersion]] = {}
        for operator in operators:
            by_ref.setdefault(operator.provider.provider_operator_ref, []).append(operator)

        for intent in _INTENTS:
            terms = tuple(term for term in intent.keywords if term in normalized)
            if intent.provider_ref in normalized:
                terms = (*terms, intent.provider_ref)
            capability_hit = tuple(
                capability
                for capability in intent.capabilities
                if capability in capabilities
            )
            if terms or capability_hit:
                for operator in by_ref.get(intent.provider_ref, ()):
                    recall(
                        intent.name,
                        operator,
                        "rule",
                        100 + 10 * len(terms) + 20 * len(capability_hit),
                        (*terms, *capability_hit),
                    )

        query_tokens = _tokens(normalized)
        for operator in operators:
            operator_terms = _operator_terms(operator)
            overlap = query_tokens.intersection(operator_terms)
            explicit_ref = operator.provider.provider_operator_ref in normalized
            if overlap or explicit_ref:
                lexical_capability = next(
                    (
                        capability
                        for capability in capabilities
                        if _SEMANTIC_CONCEPTS.get(capability, frozenset()).intersection(
                            operator_terms
                        )
                    ),
                    operator.secondary_category,
                )
                recall(
                    lexical_capability,
                    operator,
                    "keyword",
                    10 * len(overlap) + (40 if explicit_ref else 0),
                    (*sorted(overlap), operator.provider.provider_operator_ref if explicit_ref else ""),
                )

        for capability in capabilities:
            concepts = _SEMANTIC_CONCEPTS.get(capability, frozenset())
            if not concepts:
                continue
            for operator in operators:
                matched = concepts.intersection(_operator_terms(operator))
                if matched:
                    recall(
                        capability,
                        operator,
                        "semantic",
                        25 * len(matched),
                        sorted(matched),
                    )

        matches = [
            self._to_match(
                capability,
                item,
                requirement=requirement,
                allow_draft_candidates=allow_draft_candidates,
            )
            for (capability, _), item in evidence.items()
        ]
        return tuple(
            sorted(matches, key=lambda item: (-item.score, item.operator_version_id))
        )

    @staticmethod
    def _to_match(
        capability: str,
        evidence: dict[str, Any],
        *,
        requirement: str,
        allow_draft_candidates: bool,
    ) -> OperatorCatalogMatch:
        operator: OperatorSpecVersion = evidence["operator"]
        backends = {profile.backend for profile in operator.supported_runtime_profiles}
        backend = (
            RuntimeBackend.CPU
            if RuntimeBackend.CPU in backends
            else RuntimeBackend.REMOTE
            if RuntimeBackend.REMOTE in backends
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
        scores = evidence["scores"]
        return OperatorCatalogMatch(
            intent=capability,
            capability=capability,
            operator_version_id=operator.id,
            provider_id=operator.provider.provider_id,
            provider_operator_ref=operator.provider.provider_operator_ref,
            display_name=operator.display_name,
            score=sum(scores.values()),
            matched_terms=tuple(sorted(evidence["terms"])),
            recall_sources=tuple(sorted(evidence["sources"])),
            rule_score=scores["rule"],
            keyword_score=scores["keyword"],
            semantic_score=scores["semantic"],
            runtime_backend=backend,
            status=operator.status,
            executable=blocked_reason is None,
            blocked_reason=blocked_reason,
            parameters=_suggest_parameters(
                operator.provider.provider_operator_ref,
                requirement,
            ),
        )


class DataJuicerCatalogMatcher(HybridOperatorCatalogMatcher):
    def __init__(self, registry: OperatorRegistry) -> None:
        super().__init__(registry, provider_ids=frozenset({"datajuicer"}))


__all__ = [
    "DataJuicerCatalogMatcher",
    "HybridOperatorCatalogMatcher",
    "OperatorCatalogMatch",
]
