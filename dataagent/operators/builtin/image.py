from __future__ import annotations

from pathlib import Path
import hashlib
from typing import Any

from ...domain.operators import (
    ImplementationSpec,
    ImplementationType,
    OperatorCategory,
    OperatorSpecVersion,
    OperatorStatus,
    ProviderRef,
    RuntimeBackend,
    RuntimeProfile,
)
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
    parameter_schema: dict[str, Any] | None = None,
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
        parameter_schema=parameter_schema
        or {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        provider=ProviderRef(
            provider_id="native",
            provider_version="0.1.0",
            provider_operator_ref=operator_id,
        ),
        implementation=ImplementationSpec(
            implementation_type=ImplementationType.CODE,
            entrypoint=implementation_ref,
        ),
        supported_runtime_profiles=(RuntimeProfile(backend=RuntimeBackend.CPU),),
        implementation_ref=implementation_ref,
        status=OperatorStatus.PUBLIC_RELEASE,
        owner_id="system",
        visibility="public",
    )


class DecodeCheckOperator:
    parallel_safe = True
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
            labels=input_data.labels,
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
            decision="continue" if metrics["decode_ok"] else "reject",
            reason_codes=[] if metrics["decode_ok"] else ["DECODE_FAILED"],
            confidence=1.0,
        )


class QualityFilterOperator:
    parallel_safe = True
    spec = _spec(
        operator_id="builtin.quality_filter:1",
        name="图片质量过滤",
        summary="根据基础质量分数保留或过滤图片。",
        description="综合亮度和清晰度代理分数执行可解释过滤，不修改图片像素。",
        category=OperatorCategory.FILTERING,
        secondary="image_quality",
        implementation_ref="dataagent.operators.builtin.image:QualityFilterOperator",
        tags=frozenset({"image", "quality", "filter"}),
        parameter_schema={
            "type": "object",
            "properties": {
                "confidence_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.55,
                }
            },
            "additionalProperties": False,
        },
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
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
            decision="continue" if keep else "reject",
            reason_codes=[] if keep else ["QUALITY_BELOW_THRESHOLD"],
            confidence=quality_score,
        )


class PerceptualDedupOperator:
    parallel_safe = False  # mutates context.shared["seen_dhash"]; first-seen-wins must stay serial
    supports_dataset_batch = True
    spec = _spec(
        operator_id="builtin.perceptual_dedup:1",
        name="感知哈希去重",
        summary="根据感知哈希识别本次运行中的重复图片。",
        description="读取上游产生的感知哈希；首次出现的图片保留，重复哈希图片被过滤。",
        category=OperatorCategory.DEDUPLICATION,
        secondary="perceptual_duplicate",
        implementation_ref="dataagent.operators.builtin.image:PerceptualDedupOperator",
        tags=frozenset({"image", "deduplication", "perceptual_hash"}),
        parameter_schema={
            "type": "object",
            "properties": {
                "distance_threshold": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 64,
                    "default": 0,
                }
            },
            "additionalProperties": False,
        },
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        node_id = str(context.shared.get("active_node_id", ""))
        prepared = context.shared.get("dataset_operator_results", {}).get(
            node_id, {}
        )
        if input_data.source_path in prepared:
            result = prepared[input_data.source_path]
            return result.model_copy(
                update={
                    "metrics": {**input_data.metrics, **result.metrics},
                    "labels": {**input_data.labels, **result.labels},
                    "artifacts": [
                        *input_data.artifacts,
                        *(
                            item
                            for item in result.artifacts
                            if item not in input_data.artifacts
                        ),
                    ],
                }
            )
        seen = context.shared.setdefault("seen_dhash", set())
        dhash = input_data.metrics.get("dhash")
        threshold = parameters["distance_threshold"]
        duplicate = bool(
            dhash
            and any(
                (int(dhash, 16) ^ int(candidate, 16)).bit_count() <= threshold
                for candidate in seen
            )
        )
        if dhash:
            seen.add(dhash)
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels={**input_data.labels, "duplicate": duplicate},
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
            decision="reject" if duplicate else "continue",
            reason_codes=["PERCEPTUAL_DUPLICATE"] if duplicate else [],
            confidence=1.0,
        )

    def prepare_dataset(
        self,
        *,
        node_id: str,
        context: OperatorContext,
        inputs: tuple[OperatorInput, ...],
        parameters: dict[str, Any],
        runtime_backend: RuntimeBackend,
    ) -> None:
        threshold = int(parameters["distance_threshold"])
        canonicals: list[tuple[str, str, str]] = []
        prepared: dict[str, OperatorResult] = {}
        upstream_results = tuple(
            results
            for prepared_node_id, results in context.shared.get(
                "dataset_operator_results", {}
            ).items()
            if prepared_node_id != node_id and isinstance(results, dict)
        )
        for item in inputs:
            if any(
                (
                    upstream_result := results.get(item.source_path)
                ) is not None
                and upstream_result.decision in {"reject", "failed"}
                for results in upstream_results
            ):
                continue
            metrics = analyze_image(Path(item.current_path)).model_dump(mode="json")
            dhash = str(metrics.get("dhash") or "")
            match = next(
                (
                    candidate
                    for candidate in canonicals
                    if dhash
                    and (
                        int(dhash, 16) ^ int(candidate[0], 16)
                    ).bit_count()
                    <= threshold
                ),
                None,
            )
            if match is None:
                group_id = "duplicate_group_" + hashlib.sha256(
                    f"{item.source_path}|{dhash}".encode("utf-8")
                ).hexdigest()[:16]
                canonical_path = item.source_path
                if dhash:
                    canonicals.append((dhash, canonical_path, group_id))
                duplicate = False
            else:
                _, canonical_path, group_id = match
                duplicate = True
            prepared[item.source_path] = OperatorResult(
                output_path=item.current_path,
                metrics=metrics,
                labels={
                    "duplicate": duplicate,
                    "duplicate_group_id": group_id,
                    "canonical_asset_path": canonical_path,
                },
                decision="reject" if duplicate else "continue",
                reason_codes=["PERCEPTUAL_DUPLICATE"] if duplicate else [],
                confidence=1.0,
            )
        context.shared.setdefault("dataset_operator_results", {})[
            node_id
        ] = prepared


class ManifestOperator:
    parallel_safe = True
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
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
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
