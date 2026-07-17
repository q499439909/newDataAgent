from __future__ import annotations

from typing import Any

from ...domain.operators import (
    ImplementationSpec,
    ImplementationType,
    ModelRequirement,
    ModelSource,
    OperatorCategory,
    OperatorSpecVersion,
    OperatorStatus,
    ProviderRef,
    RuntimeBackend,
    RuntimeProfile,
)
from ..models import MockModelBackend, ModelManager
from ..protocol import (
    AnnotationRef,
    EmbeddingRef,
    OperatorContext,
    OperatorInput,
    OperatorResult,
)


def _parameter_schema(properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }


def _model_spec(
    *,
    operator_id: str,
    display_name: str,
    summary: str,
    description: str,
    secondary: str,
    tags: frozenset[str],
    model_id: str,
    entrypoint: str,
    parameter_schema: dict[str, Any] | None = None,
) -> OperatorSpecVersion:
    return OperatorSpecVersion(
        id=operator_id,
        family_id=operator_id.rsplit(":", 1)[0],
        version=1,
        created_by="system",
        change_reason="mock-first model operator",
        display_name=display_name,
        summary=summary,
        description=description,
        primary_category=OperatorCategory.UNDERSTANDING,
        secondary_category=secondary,
        capability_tags=tags,
        input_schema="ImageAssetRef",
        output_schema="ModelEnrichedImageAsset",
        parameter_schema=parameter_schema or _parameter_schema(),
        provider=ProviderRef(
            provider_id="native",
            provider_version="0.1.0",
            provider_operator_ref=operator_id,
        ),
        implementation=ImplementationSpec(
            implementation_type=ImplementationType.MODEL,
            entrypoint=entrypoint,
        ),
        supported_runtime_profiles=(
            RuntimeProfile(
                backend=RuntimeBackend.MOCK,
                cpu=0.1,
                memory_mb=64,
            ),
        ),
        model_requirement=ModelRequirement(
            model_source=ModelSource.OPEN_SOURCE,
            model_id=model_id,
            revision="candidate-v1",
            sha256="",
            code_license="review_required",
            checkpoint_license="review_required",
            estimated_size_bytes=0,
        ),
        implementation_type="model",
        implementation_ref=entrypoint,
        status=OperatorStatus.DRAFT,
        owner_id="system",
        visibility="private",
        limitations=("Mock output only; not approved for production use.",),
    )


class _MockModelOperator:
    capability: str
    spec: OperatorSpecVersion

    def __init__(self, model_manager: ModelManager | None = None) -> None:
        self.model_manager = model_manager or ModelManager(
            (MockModelBackend(),), allow_download=False
        )

    def _infer(self, input_data: OperatorInput, parameters: dict[str, Any]) -> dict[str, Any]:
        assert self.spec.model_requirement is not None
        return self.model_manager.infer(
            backend=RuntimeBackend.MOCK,
            requirement=self.spec.model_requirement,
            capability=self.capability,
            input_data=input_data,
            parameters=parameters,
        )


class AestheticScoreOperator(_MockModelOperator):
    capability = "aesthetic_score"
    spec = _model_spec(
        operator_id="model.aesthetic_score:1",
        display_name="美学评分（Mock）",
        summary="生成图片美学评分和置信度。",
        description="开发阶段使用确定性 Mock 输出；真实后端候选为经过任务评测的开源美学模型。",
        secondary="aesthetic_understanding",
        tags=frozenset({"image", "aesthetic_score", "model", "mock"}),
        model_id="candidate/aesthetic-predictor",
        entrypoint="dataagent.operators.builtin.model:AestheticScoreOperator",
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        inference = self._infer(input_data, parameters)
        score = inference["unit_score"]
        return OperatorResult(
            output_path=input_data.current_path,
            metrics={**input_data.metrics, "aesthetic_score": score},
            labels={**input_data.labels, "model_output_mode": "mock"},
            annotations=[
                *input_data.annotations,
                AnnotationRef(annotation_type="score", values={"name": "aesthetic", "value": score}),
            ],
            artifacts=input_data.artifacts,
            embeddings=input_data.embeddings,
            confidence=0.0,
            model_version_id=f"{self.spec.model_requirement.model_id}@candidate-v1",
        )


class SegmentationOperator(_MockModelOperator):
    capability = "segmentation"
    spec = _model_spec(
        operator_id="model.segmentation:1",
        display_name="图像分割（Mock）",
        summary="生成目标区域和 mask 引用。",
        description="开发阶段返回结构正确的 Mock mask；真实后端可替换为 SAM 系列或领域分割模型。",
        secondary="segmentation",
        tags=frozenset({"image", "segmentation", "mask", "model", "mock"}),
        model_id="candidate/segment-anything",
        entrypoint="dataagent.operators.builtin.model:SegmentationOperator",
        parameter_schema=_parameter_schema(
            {
                "confidence_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.5,
                }
            }
        ),
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        inference = self._infer(input_data, parameters)
        digest = inference["digest"]
        confidence = inference["unit_score"]
        annotation = AnnotationRef(
            annotation_type="mask",
            payload_uri=f"mock://segmentation/{digest}",
            values={"box_xyxy": [0.2, 0.2, 0.8, 0.8], "class": "foreground"},
        )
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels={**input_data.labels, "model_output_mode": "mock"},
            artifacts=input_data.artifacts,
            annotations=[*input_data.annotations, annotation],
            embeddings=input_data.embeddings,
            confidence=0.0,
            model_version_id=f"{self.spec.model_requirement.model_id}@candidate-v1",
        )


class WatermarkDetectionOperator(_MockModelOperator):
    capability = "watermark_detection"
    spec = _model_spec(
        operator_id="model.watermark_detection:1",
        display_name="水印识别（Mock）",
        summary="识别水印概率、类型和区域。",
        description="开发阶段返回 Mock 水印概率；真实实现组合 OCR、检测/分割和协议 detector。",
        secondary="watermark_detection",
        tags=frozenset({"image", "watermark_detection", "model", "mock"}),
        model_id="candidate/watermark-detector",
        entrypoint="dataagent.operators.builtin.model:WatermarkDetectionOperator",
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        inference = self._infer(input_data, parameters)
        probability = inference["unit_score"]
        return OperatorResult(
            output_path=input_data.current_path,
            metrics={**input_data.metrics, "watermark_probability": probability},
            labels={
                **input_data.labels,
                "watermark_type": "mock_visible" if probability >= 0.5 else "none",
                "model_output_mode": "mock",
            },
            artifacts=input_data.artifacts,
            annotations=[
                *input_data.annotations,
                AnnotationRef(
                    annotation_type="score",
                    values={"name": "watermark_probability", "value": probability},
                ),
            ],
            embeddings=input_data.embeddings,
            confidence=0.0,
            model_version_id=f"{self.spec.model_requirement.model_id}@candidate-v1",
        )


class FaceIdentityOperator(_MockModelOperator):
    capability = "face_identity"
    spec = _model_spec(
        operator_id="model.face_identity:1",
        display_name="人像 ID 识别（Mock）",
        summary="生成授权身份库匹配结果或 unknown。",
        description="开发阶段只返回 unknown 和 Mock embedding 引用，禁止产生真实身份结论。",
        secondary="face_and_person",
        tags=frozenset({"image", "face_identity", "face_embedding", "biometric", "mock"}),
        model_id="candidate/face-embedding",
        entrypoint="dataagent.operators.builtin.model:FaceIdentityOperator",
        parameter_schema=_parameter_schema(
            {
                "identity_gallery_version_id": {
                    "type": ["string", "null"],
                    "default": None,
                },
                "match_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.6,
                },
            }
        ),
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        inference = self._infer(input_data, parameters)
        embedding = EmbeddingRef(
            vector_id=f"mock_embedding_{inference['digest'][:16]}",
            model_version_id=f"{self.spec.model_requirement.model_id}@candidate-v1",
            dimensions=512,
        )
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels={
                **input_data.labels,
                "identity_id": "unknown",
                "model_output_mode": "mock",
            },
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=[*input_data.embeddings, embedding],
            confidence=0.0,
            model_version_id=embedding.model_version_id,
        )


def builtin_model_operators(model_manager: ModelManager | None = None) -> tuple:
    return (
        AestheticScoreOperator(model_manager),
        SegmentationOperator(model_manager),
        WatermarkDetectionOperator(model_manager),
        FaceIdentityOperator(model_manager),
    )


__all__ = [
    "AestheticScoreOperator",
    "FaceIdentityOperator",
    "SegmentationOperator",
    "WatermarkDetectionOperator",
    "builtin_model_operators",
]
