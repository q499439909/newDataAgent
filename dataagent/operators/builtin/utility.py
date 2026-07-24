from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from ...domain.operators import (
    AnnotationRef,
    AssetRef,
    ImplementationSpec,
    ImplementationType,
    OperatorCategory,
    OperatorSpecVersion,
    OperatorStatus,
    ProviderRef,
    RuntimeBackend,
    RuntimeProfile,
)
from ..protocol import OperatorContext, OperatorInput, OperatorResult


def _spec(
    *,
    operator_id: str,
    display_name: str,
    summary: str,
    description: str,
    category: OperatorCategory,
    secondary: str,
    tags: frozenset[str],
    parameter_schema: dict[str, Any],
    entrypoint_name: str,
) -> OperatorSpecVersion:
    entrypoint = f"dataagent.operators.builtin.utility:{entrypoint_name}"
    return OperatorSpecVersion(
        id=operator_id,
        family_id=operator_id.rsplit(":", 1)[0],
        version=1,
        created_by="system",
        change_reason="built-in reference operator",
        display_name=display_name,
        summary=summary,
        description=description,
        primary_category=category,
        secondary_category=secondary,
        capability_tags=tags,
        input_schema="EnrichedImageAsset",
        output_schema="EnrichedImageAsset",
        parameter_schema=parameter_schema,
        provider=ProviderRef(
            provider_id="native",
            provider_version="0.1.0",
            provider_operator_ref=operator_id,
        ),
        implementation=ImplementationSpec(
            implementation_type=ImplementationType.CODE,
            entrypoint=entrypoint,
        ),
        supported_runtime_profiles=(RuntimeProfile(backend=RuntimeBackend.CPU),),
        implementation_ref=entrypoint,
        status=OperatorStatus.PUBLIC_RELEASE,
        owner_id="system",
        visibility="public",
    )


class AnnotationCoordinateOperator:
    parallel_safe = True
    spec = _spec(
        operator_id="builtin.annotation_coordinates:1",
        display_name="标注坐标转换",
        summary="在归一化坐标与像素坐标之间转换标注框。",
        description="转换 annotation 中的 box_xyxy，不修改原图或其他标注字段。",
        category=OperatorCategory.TRANSFORMATION,
        secondary="annotation_conversion",
        tags=frozenset({"image", "annotation", "coordinate_conversion"}),
        entrypoint_name="AnnotationCoordinateOperator",
        parameter_schema={
            "type": "object",
            "properties": {
                "target_space": {
                    "type": "string",
                    "enum": ["normalized", "pixel"],
                    "default": "pixel",
                }
            },
            "additionalProperties": False,
        },
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        width = float(input_data.metrics.get("width", 0))
        height = float(input_data.metrics.get("height", 0))
        if width <= 0 or height <= 0:
            raise ValueError("Annotation conversion requires positive image width and height")
        target_space = parameters["target_space"]
        converted: list[AnnotationRef] = []
        for annotation in input_data.annotations:
            values = dict(annotation.values)
            box = values.get("box_xyxy")
            current_space = values.get("coordinate_space", "normalized")
            if isinstance(box, list) and len(box) == 4 and current_space != target_space:
                scales = (width, height, width, height)
                if target_space == "pixel":
                    box = [round(float(value) * scale, 4) for value, scale in zip(box, scales)]
                else:
                    box = [round(float(value) / scale, 6) for value, scale in zip(box, scales)]
                values["box_xyxy"] = box
            values["coordinate_space"] = target_space
            converted.append(annotation.model_copy(update={"values": values}))
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels=input_data.labels,
            artifacts=input_data.artifacts,
            annotations=converted,
            embeddings=input_data.embeddings,
        )


class AutoContrastOperator:
    parallel_safe = True
    spec = _spec(
        operator_id="builtin.auto_contrast:1",
        display_name="自动对比度增强",
        summary="生成不覆盖原图的自动对比度增强副本。",
        description="使用 Pillow 自动对比度算法写入运行产物目录，并返回新图片引用。",
        category=OperatorCategory.ENHANCEMENT,
        secondary="augmentation",
        tags=frozenset({"image", "enhancement", "auto_contrast"}),
        entrypoint_name="AutoContrastOperator",
        parameter_schema={
            "type": "object",
            "properties": {
                "cutoff": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 0,
                }
            },
            "additionalProperties": False,
        },
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        artifact_root_raw = context.shared.get("artifact_root")
        if not artifact_root_raw:
            raise RuntimeError("Enhancement operators require an artifact_root")
        artifact_root = Path(artifact_root_raw).resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        identity = f"{input_data.current_path}|{parameters['cutoff']}"
        target = artifact_root / f"auto-contrast-{hashlib.sha256(identity.encode()).hexdigest()}.png"
        if not target.exists():
            with Image.open(input_data.current_path) as image:
                output = ImageOps.autocontrast(
                    ImageOps.exif_transpose(image).convert("RGB"),
                    cutoff=parameters["cutoff"],
                )
                output.save(target, "PNG")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        artifact = AssetRef(uri=str(target), media_type="image/png", sha256=digest)
        return OperatorResult(
            output_path=str(target),
            metrics=input_data.metrics,
            labels={**input_data.labels, "enhancement": "auto_contrast"},
            artifacts=[*input_data.artifacts, artifact],
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
            confidence=1.0,
        )


class DeterministicSamplerOperator:
    parallel_safe = True
    spec = _spec(
        operator_id="builtin.deterministic_sampler:1",
        display_name="确定性随机采样",
        summary="按稳定哈希和采样率选择图片。",
        description="同一图片、种子和参数总是产生相同决定，便于复现和断点恢复。",
        category=OperatorCategory.SAMPLING,
        secondary="random_sampling",
        tags=frozenset({"image", "sampling", "deterministic"}),
        entrypoint_name="DeterministicSamplerOperator",
        parameter_schema={
            "type": "object",
            "properties": {
                "rate": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 1,
                },
                "seed": {"type": "integer", "default": 0},
            },
            "additionalProperties": False,
        },
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        identity = f"{input_data.source_path}|{parameters['seed']}"
        digest = hashlib.sha256(identity.encode()).hexdigest()
        score = int(digest[:16], 16) / 0xFFFFFFFFFFFFFFFF
        keep = score < parameters["rate"]
        return OperatorResult(
            output_path=input_data.current_path,
            metrics={**input_data.metrics, "sampling_score": round(score, 8)},
            labels={**input_data.labels, "sampled": keep},
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
            decision="continue" if keep else "reject",
            reason_codes=[] if keep else ["SAMPLED_OUT"],
            confidence=1.0,
        )


class HardConstraintEvaluatorOperator:
    parallel_safe = True
    spec = _spec(
        operator_id="builtin.hard_constraint_evaluator:1",
        display_name="图片硬约束评估",
        summary="评估解码状态和最小尺寸，但不执行过滤决定。",
        description="作为独立 Evaluator 记录验收事实，避免把评估与业务过滤混为一体。",
        category=OperatorCategory.EVALUATION,
        secondary="hard_constraint_check",
        tags=frozenset({"image", "evaluation", "hard_constraint"}),
        entrypoint_name="HardConstraintEvaluatorOperator",
        parameter_schema={
            "type": "object",
            "properties": {
                "min_width": {"type": "integer", "minimum": 1, "default": 1},
                "min_height": {"type": "integer", "minimum": 1, "default": 1},
            },
            "additionalProperties": False,
        },
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        passed = bool(
            input_data.metrics.get("decode_ok", False)
            and int(input_data.metrics.get("width", 0)) >= parameters["min_width"]
            and int(input_data.metrics.get("height", 0)) >= parameters["min_height"]
        )
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels={**input_data.labels, "hard_constraint_pass": passed},
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
            decision="continue",
            reason_codes=[] if passed else ["HARD_CONSTRAINT_FAILED"],
            confidence=1.0,
        )


def builtin_utility_operators() -> tuple:
    return (
        AnnotationCoordinateOperator(),
        AutoContrastOperator(),
        DeterministicSamplerOperator(),
        HardConstraintEvaluatorOperator(),
    )


__all__ = [
    "AnnotationCoordinateOperator",
    "AutoContrastOperator",
    "DeterministicSamplerOperator",
    "HardConstraintEvaluatorOperator",
    "builtin_utility_operators",
]
