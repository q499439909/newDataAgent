from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from ..application.pipeline_artifacts import (
    parse_pipeline_yaml,
    pipeline_sha256,
    serialize_pipeline_yaml,
)
from ..domain.pipelines import PipelineVersion
from .observations import ToolEvidence, ToolResult
from .spec import ToolConfirmation, ToolContext, ToolEffect, ToolSpec


class CompilePipelineArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline: dict[str, Any] | None = None
    pipeline_version_id: str | None = None

    @model_validator(mode="after")
    def require_one_source(self) -> "CompilePipelineArtifactInput":
        if (self.pipeline is None) == (self.pipeline_version_id is None):
            raise ValueError("Provide exactly one pipeline source")
        return self


class ValidatePipelineArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


def _load_pipeline(
    context: ToolContext, request: CompilePipelineArtifactInput
) -> PipelineVersion:
    if request.pipeline is not None:
        return PipelineVersion.model_validate(request.pipeline)
    if context.version_store is None:
        raise RuntimeError("VersionStore is required for pipeline references")
    payload = context.version_store.get(
        kind="pipeline",
        entity_id=str(request.pipeline_version_id),
        owner_id=context.owner_id,
    )
    return PipelineVersion.model_validate(payload)


def _compile_pipeline_artifact(
    context: ToolContext, request: CompilePipelineArtifactInput
) -> ToolResult:
    pipeline = _load_pipeline(context, request)
    checksum = pipeline_sha256(pipeline)
    artifact_id = f"pipeline_artifact_{checksum[:16]}"
    return ToolResult(
        ok=True,
        tool="compile_pipeline_artifact",
        status="succeeded",
        summary="Compiled a deterministic PipelineArtifact draft.",
        data={
            "pipeline_artifact_id": artifact_id,
            "pipeline_version_id": pipeline.id,
            "canonical_sha256": checksum,
            "content": serialize_pipeline_yaml(pipeline),
            "approved": False,
        },
        evidence=(
            ToolEvidence(kind="pipeline_version", id=pipeline.id),
            ToolEvidence(kind="pipeline_artifact", id=artifact_id),
        ),
        next_actions=("validate_pipeline_artifact",),
    )


def _validate_pipeline_artifact(
    context: ToolContext, request: ValidatePipelineArtifactInput
) -> ToolResult:
    del context
    pipeline = parse_pipeline_yaml(request.content)
    checksum = pipeline_sha256(pipeline)
    artifact_id = f"pipeline_artifact_{checksum[:16]}"
    return ToolResult(
        ok=True,
        tool="validate_pipeline_artifact",
        status="succeeded",
        summary="PipelineArtifact schema and checksum are valid.",
        data={
            "pipeline_artifact_id": artifact_id,
            "pipeline_version_id": pipeline.id,
            "schema_ok": True,
            "checksum_ok": True,
        },
        evidence=(
            ToolEvidence(kind="pipeline_artifact", id=artifact_id),
            ToolEvidence(kind="pipeline_version", id=pipeline.id),
        ),
    )


def compile_pipeline_artifact_spec() -> ToolSpec:
    return ToolSpec(
        name="compile_pipeline_artifact",
        description="Compile a structured Pipeline draft into a deterministic artifact.",
        input_model=CompilePipelineArtifactInput,
        executor=_compile_pipeline_artifact,
        tags=("artifact", "pipeline", "draft_only"),
        effect=ToolEffect.DRAFT,
        confirmation=ToolConfirmation.DRAFT_ONLY,
    )


def validate_pipeline_artifact_spec() -> ToolSpec:
    return ToolSpec(
        name="validate_pipeline_artifact",
        description="Validate PipelineArtifact schema and checksum.",
        input_model=ValidatePipelineArtifactInput,
        executor=_validate_pipeline_artifact,
        tags=("artifact", "pipeline", "read_only"),
        effect=ToolEffect.READ,
        confirmation=ToolConfirmation.AUTO,
    )


__all__ = [
    "CompilePipelineArtifactInput",
    "ValidatePipelineArtifactInput",
    "compile_pipeline_artifact_spec",
    "validate_pipeline_artifact_spec",
]
