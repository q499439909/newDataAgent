from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

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
from ..protocol import OperatorContext, OperatorInput, OperatorResult


def _spec(
    *,
    operator_id: str,
    name: str,
    summary: str,
    category: OperatorCategory,
    secondary: str,
    tags: frozenset[str],
    parameter_schema: dict[str, Any],
    resource_requirements: dict[str, Any] | None = None,
) -> OperatorSpecVersion:
    implementation_ref = f"dataagent.operators.builtin.semantic:{name}"
    return OperatorSpecVersion(
        id=operator_id,
        family_id=operator_id.rsplit(":", 1)[0],
        version=1,
        created_by="system",
        change_reason="built-in semantic decision operator",
        display_name=name,
        summary=summary,
        description=(
            f"Deterministic CPU operator for {summary.lower()} It consumes governed "
            "upstream labels and never downloads or executes a model."
        ),
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
            entrypoint=implementation_ref,
        ),
        supported_runtime_profiles=(RuntimeProfile(backend=RuntimeBackend.CPU),),
        implementation_ref=implementation_ref,
        resource_requirements=resource_requirements or {},
        status=OperatorStatus.PUBLIC_RELEASE,
        owner_id="system",
        visibility="public",
    )


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value.lower()
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _strings(nested)
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            yield from _strings(nested)


def _semantic_text(input_data: OperatorInput) -> str:
    provider_output = input_data.labels.get("datajuicer_output", {})
    preferred: list[Any] = []
    if isinstance(provider_output, dict):
        for key in ("authenticity_tags", "image_tags", "tags", "caption"):
            if key in provider_output:
                preferred.append(provider_output[key])
    values = preferred or [provider_output]
    return " ".join(token for value in values for token in _strings(value))


class AuthenticityDecisionOperator:
    spec = _spec(
        operator_id="builtin.authenticity_decision:1",
        name="AuthenticityDecisionOperator",
        summary="Resolve VLM authenticity evidence into keep, review, or reject decisions.",
        category=OperatorCategory.FILTERING,
        secondary="semantic_rule",
        tags=frozenset({"image", "authenticity_assessment", "filter", "cpu"}),
        resource_requirements={"upstream_capability_tags": ["visual_understanding"]},
        parameter_schema={
            "type": "object",
            "properties": {
                "uncertain_policy": {
                    "type": "string",
                    "enum": ["keep", "review", "reject"],
                    "default": "review",
                }
            },
            "additionalProperties": False,
        },
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        evidence = _semantic_text(input_data)
        synthetic_tokens = (
            "synthetic",
            "ai-generated",
            "ai generated",
            "generated image",
            "fake photo",
            "computer-generated",
            "人工智能生成",
            "合成图",
        )
        authentic_tokens = (
            "authentic",
            "real photo",
            "natural photograph",
            "真实照片",
            "实拍",
        )
        if any(token in evidence for token in synthetic_tokens):
            resolved = "synthetic"
            action = "reject"
        elif any(token in evidence for token in authentic_tokens):
            resolved = "authentic"
            action = "keep"
        else:
            resolved = "uncertain"
            action = parameters["uncertain_policy"]
        reject = action == "reject"
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels={
                **input_data.labels,
                "authenticity": resolved,
                "authenticity_review_required": action == "review",
            },
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
            decision="reject" if reject else "continue",
            reason_codes=["SYNTHETIC_OR_UNCERTAIN_IMAGE"] if reject else [],
            confidence=1.0 if resolved != "uncertain" else 0.5,
        )


class ClassResolutionOperator:
    spec = _spec(
        operator_id="builtin.class_resolution:1",
        name="ClassResolutionOperator",
        summary="Normalize VLM tags into cat, dog, mixed, or unknown task classes.",
        category=OperatorCategory.UNDERSTANDING,
        secondary="classification",
        tags=frozenset({"image", "class_resolution", "classification", "cpu"}),
        resource_requirements={"upstream_capability_tags": ["image_classification"]},
        parameter_schema={
            "type": "object",
            "properties": {
                "mixed_policy": {
                    "type": "string",
                    "enum": ["keep", "review", "reject"],
                    "default": "review",
                },
                "unknown_policy": {
                    "type": "string",
                    "enum": ["keep", "review", "reject"],
                    "default": "review",
                },
            },
            "additionalProperties": False,
        },
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        evidence = _semantic_text(input_data)
        has_cat = any(token in evidence for token in ("cat", "kitten", "feline", "猫"))
        has_dog = any(token in evidence for token in ("dog", "puppy", "canine", "狗"))
        if has_cat and has_dog:
            resolved = "mixed"
        elif has_cat:
            resolved = "cat"
        elif has_dog:
            resolved = "dog"
        else:
            resolved = "unknown"
        policy = (
            parameters["mixed_policy"]
            if resolved == "mixed"
            else parameters["unknown_policy"]
            if resolved == "unknown"
            else "keep"
        )
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels={
                **input_data.labels,
                "resolved_class": resolved,
                "class_review_required": policy == "review",
            },
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
            decision="reject" if policy == "reject" else "continue",
            reason_codes=["UNRESOLVED_TASK_CLASS"] if policy == "reject" else [],
            confidence=1.0 if resolved in {"cat", "dog"} else 0.5,
        )


class DatasetPartitionOperator:
    spec = _spec(
        operator_id="builtin.dataset_partition:1",
        name="DatasetPartitionOperator",
        summary="Assign retained images to deterministic class output paths.",
        category=OperatorCategory.OUTPUT,
        secondary="dataset_freeze",
        tags=frozenset({"image", "dataset_partition", "output", "cpu"}),
        resource_requirements={"upstream_capability_tags": ["class_resolution"]},
        parameter_schema={
            "type": "object",
            "properties": {
                "directory_prefix": {"type": "string", "default": "classes"}
            },
            "additionalProperties": False,
        },
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        resolved = str(input_data.labels.get("resolved_class", "unknown"))
        if resolved not in {"cat", "dog", "mixed", "unknown"}:
            resolved = "unknown"
        prefix = Path(parameters["directory_prefix"])
        if prefix.is_absolute() or ".." in prefix.parts:
            raise ValueError("directory_prefix must be a safe relative path")
        source = Path(input_data.source_path)
        digest = str(input_data.metrics.get("sha256", ""))[:12]
        suffix = source.suffix.lower()
        filename = f"{source.stem}-{digest}{suffix}" if digest else source.name
        relative_path = (prefix / resolved / filename).as_posix()
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels={**input_data.labels, "output_relative_path": relative_path},
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
            decision="continue",
            confidence=1.0,
        )


def builtin_semantic_operators() -> tuple:
    return (
        AuthenticityDecisionOperator(),
        ClassResolutionOperator(),
        DatasetPartitionOperator(),
    )


__all__ = [
    "AuthenticityDecisionOperator",
    "ClassResolutionOperator",
    "DatasetPartitionOperator",
    "builtin_semantic_operators",
]
