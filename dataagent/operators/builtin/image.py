from __future__ import annotations

from pathlib import Path
from typing import Any

from ...domain.operators import OperatorCategory, OperatorSpecVersion, OperatorStatus
from ...imaging import analyze_image
from ..protocol import OperatorContext, OperatorInput, OperatorResult


def _spec(
    *,
    operator_id: str,
    name: str,
    summary: str,
    description: str,
    category: OperatorCategory,
    secondary: str,
    implementation_ref: str,
    tags: frozenset[str],
) -> OperatorSpecVersion:
    return OperatorSpecVersion(
        id=operator_id,
        family_id=operator_id.rsplit(":", 1)[0],
        version=1,
        created_by="system",
        change_reason="built-in operator",
        display_name=name,
        summary=summary,
        description=description,
        primary_category=category,
        secondary_category=secondary,
        capability_tags=tags,
        input_schema="ImageAssetRef",
        output_schema="EnrichedImageAsset",
        implementation_ref=implementation_ref,
        status=OperatorStatus.PUBLIC_RELEASE,
        owner_id="system",
        visibility="public",
    )


class DecodeCheckOperator:
    spec = _spec(
        operator_id="builtin.decode_check:1",
        name="图片解码与元数据检查",
        summary="检查图片能否解码并提取基础质量指标。",
        description="读取图片但不修改原图，输出哈希、尺寸、格式、亮度和清晰度代理指标。",
        category=OperatorCategory.INGESTION,
        secondary="decoding",
        implementation_ref="dataagent.operators.builtin.image:DecodeCheckOperator",
        tags=frozenset({"image", "decode", "metadata", "quality"}),
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        metrics = analyze_image(Path(input_data.current_path)).model_dump(mode="json")
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=metrics,
            decision="continue" if metrics["decode_ok"] else "reject",
            reason_codes=[] if metrics["decode_ok"] else ["DECODE_FAILED"],
            confidence=1.0,
        )


class QualityFilterOperator:
    spec = _spec(
        operator_id="builtin.quality_filter:1",
        name="图片质量过滤",
        summary="根据基础质量分数保留或过滤图片。",
        description="综合亮度和清晰度代理分数执行可解释过滤，不修改图片像素。",
        category=OperatorCategory.FILTERING,
        secondary="image_quality",
        implementation_ref="dataagent.operators.builtin.image:QualityFilterOperator",
        tags=frozenset({"image", "quality", "filter"}),
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        threshold = float(parameters.get("confidence_threshold", 0.55))
        brightness = float(input_data.metrics.get("brightness", 0))
        blur = float(input_data.metrics.get("blur_score", 0))
        brightness_score = max(0.0, 1.0 - abs(brightness - 127.5) / 127.5)
        blur_score = min(1.0, blur / 50.0)
        quality_score = round((brightness_score + blur_score) / 2.0, 4)
        keep = quality_score >= threshold
        return OperatorResult(
            output_path=input_data.current_path,
            metrics={**input_data.metrics, "quality_score": quality_score},
            labels={**input_data.labels, "quality_pass": keep},
            decision="continue" if keep else "reject",
            reason_codes=[] if keep else ["QUALITY_BELOW_THRESHOLD"],
            confidence=quality_score,
        )


class PerceptualDedupOperator:
    spec = _spec(
        operator_id="builtin.perceptual_dedup:1",
        name="感知哈希去重",
        summary="根据感知哈希识别本次运行中的重复图片。",
        description="读取上游产生的感知哈希；首次出现的图片保留，重复哈希图片被过滤。",
        category=OperatorCategory.DEDUPLICATION,
        secondary="perceptual_duplicate",
        implementation_ref="dataagent.operators.builtin.image:PerceptualDedupOperator",
        tags=frozenset({"image", "deduplication", "perceptual_hash"}),
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        seen = context.shared.setdefault("seen_dhash", set())
        dhash = input_data.metrics.get("dhash")
        duplicate = bool(dhash and dhash in seen)
        if dhash:
            seen.add(dhash)
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels={**input_data.labels, "duplicate": duplicate},
            decision="reject" if duplicate else "continue",
            reason_codes=["PERCEPTUAL_DUPLICATE"] if duplicate else [],
            confidence=1.0,
        )


class ManifestOperator:
    spec = _spec(
        operator_id="builtin.manifest:1",
        name="Manifest 记录",
        summary="记录图片最终判定及节点血缘。",
        description="不修改图片，把上游指标、标签、判定和版本引用交给输出阶段。",
        category=OperatorCategory.OUTPUT,
        secondary="manifest",
        implementation_ref="dataagent.operators.builtin.image:ManifestOperator",
        tags=frozenset({"manifest", "lineage", "output"}),
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels=input_data.labels,
            decision="keep",
            confidence=1.0,
        )


def builtin_image_operators() -> tuple:
    return (
        DecodeCheckOperator(),
        QualityFilterOperator(),
        PerceptualDedupOperator(),
        ManifestOperator(),
    )
