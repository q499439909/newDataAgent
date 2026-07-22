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


def _preferred_provider_values(input_data: OperatorInput) -> list[Any]:
    provider_output = input_data.labels.get("datajuicer_output", {})
    preferred: list[Any] = []
    if isinstance(provider_output, dict):
        for key in ("authenticity_tags", "image_tags", "tags", "caption"):
            if key in provider_output:
                preferred.append(provider_output[key])
    return preferred or [provider_output]


def _semantic_text(input_data: OperatorInput) -> str:
    return " ".join(
        token
        for value in _preferred_provider_values(input_data)
        for token in _strings(value)
    )


def _normalize_label(value: str) -> str:
    return "-".join(value.lower().replace("_", " ").replace("-", " ").split())


def _semantic_values(input_data: OperatorInput) -> set[str]:
    return {
        _normalize_label(token)
        for value in _preferred_provider_values(input_data)
        for token in _strings(value)
        if token.strip()
    }


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
        )
        authentic_tokens = ("authentic", "real photo", "natural photograph")
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
        summary="Normalize VLM tags into configured task classes.",
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
                "labels": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "minLength": 1},
                            "aliases": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                            },
                        },
                        "required": ["id", "aliases"],
                        "additionalProperties": False,
                    },
                    "default": [
                        {"id": "cat", "aliases": ["cat", "kitten", "feline"]},
                        {"id": "dog", "aliases": ["dog", "puppy", "canine"]},
                    ],
                },
                "mixed_label": {"type": "string", "default": "mixed"},
                "unknown_label": {"type": "string", "default": "unknown"},
            },
            "additionalProperties": False,
        },
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        evidence = _semantic_values(input_data)
        labels = parameters.get("labels") or [
            {"id": "cat", "aliases": ["cat", "kitten", "feline"]},
            {"id": "dog", "aliases": ["dog", "puppy", "canine"]},
        ]
        mixed_label = str(parameters.get("mixed_label", "mixed"))
        unknown_label = str(parameters.get("unknown_label", "unknown"))
        matches: list[str] = []
        for label in labels:
            aliases = {_normalize_label(label["id"])}
            aliases.update(_normalize_label(alias) for alias in label["aliases"])
            if evidence.intersection(aliases):
                matches.append(label["id"])
        if len(matches) > 1:
            resolved = mixed_label
        elif matches:
            resolved = matches[0]
        else:
            resolved = unknown_label
        policy = (
            parameters["mixed_policy"]
            if resolved == mixed_label
            else parameters["unknown_policy"]
            if resolved == unknown_label
            else "keep"
        )
        known_ids = {item["id"] for item in labels}
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
            confidence=1.0 if resolved in known_ids else 0.5,
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
                "directory_prefix": {"type": "string", "default": "classes"},
                "allowed_labels": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "default": ["cat", "dog"],
                },
                "mixed_label": {"type": "string", "default": "mixed"},
                "unknown_label": {"type": "string", "default": "unknown"},
            },
            "additionalProperties": False,
        },
    )

    def execute(
        self, context: OperatorContext, input_data: OperatorInput, parameters: dict[str, Any]
    ) -> OperatorResult:
        unknown_label = str(parameters.get("unknown_label", "unknown"))
        mixed_label = str(parameters.get("mixed_label", "mixed"))
        allowed_labels = parameters.get("allowed_labels") or ["cat", "dog"]
        resolved = str(input_data.labels.get("resolved_class", unknown_label))
        allowed = {
            *allowed_labels,
            mixed_label,
            unknown_label,
        }
        if resolved not in allowed:
            resolved = unknown_label
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
