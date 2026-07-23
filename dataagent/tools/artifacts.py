from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from ..application.pipeline_artifacts import (
    PipelineArtifact,
    pipeline_sha256,
    serialize_pipeline_yaml,
)
from ..domain.operators import OperatorStatus
from ..domain.pipelines import PipelineVersion
from ..operators.validation import ParameterValidationError, validate_parameters
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
    try:
        artifact = PipelineArtifact.model_validate(yaml.safe_load(request.content))
    except Exception as exc:
        return ToolResult(
            ok=False,
            tool="validate_pipeline_artifact",
            status="failed",
            summary="PipelineArtifact schema validation failed.",
            data={
                "schema_ok": False,
                "checksum_ok": False,
                "operators_ok": False,
                "parameters_ok": False,
                "production_eligible": False,
                "operators": [],
                "blockers": [
                    {
                        "code": "SCHEMA_INVALID",
                        "message": str(exc),
                    }
                ],
            },
            error_type="pipeline_artifact_schema_invalid",
        )
    pipeline = artifact.pipeline
    checksum = pipeline_sha256(pipeline)
    artifact_id = f"pipeline_artifact_{checksum[:16]}"
    if checksum != artifact.canonical_sha256:
        return ToolResult(
            ok=False,
            tool="validate_pipeline_artifact",
            status="failed",
            summary="PipelineArtifact checksum validation failed.",
            data={
                "pipeline_artifact_id": artifact_id,
                "pipeline_version_id": pipeline.id,
                "schema_ok": True,
                "checksum_ok": False,
                "operators_ok": False,
                "parameters_ok": False,
                "production_eligible": False,
                "operators": [],
                "blockers": [
                    {
                        "code": "CHECKSUM_MISMATCH",
                        "message": (
                            f"Expected {artifact.canonical_sha256}, got {checksum}"
                        ),
                    }
                ],
            },
            evidence=(
                ToolEvidence(kind="pipeline_artifact", id=artifact_id),
                ToolEvidence(kind="pipeline_version", id=pipeline.id),
            ),
            error_type="pipeline_artifact_checksum_mismatch",
        )

    blockers: list[dict[str, Any]] = []
    operators: list[dict[str, Any]] = []
    operators_ok = True
    parameters_ok = True
    released = {
        OperatorStatus.PERSONAL_RELEASE,
        OperatorStatus.PUBLIC_RELEASE,
    }
    if context.operator_registry is None:
        operators_ok = False
        blockers.append(
            {
                "code": "OPERATOR_REGISTRY_UNAVAILABLE",
                "message": "OperatorRegistry is required for artifact validation.",
            }
        )
    else:
        for node in pipeline.nodes:
            try:
                operator = context.operator_registry.get(node.operator_version_id)
            except KeyError:
                operators_ok = False
                blockers.append(
                    {
                        "code": "OPERATOR_UNAVAILABLE",
                        "node_id": node.id,
                        "operator_version_id": node.operator_version_id,
                        "message": "Operator version is not present in the current Catalog.",
                    }
                )
                continue
            operators.append(
                {
                    "node_id": node.id,
                    "operator_version_id": operator.id,
                    "status": operator.status.value,
                    "provider_id": operator.provider.provider_id,
                }
            )
            if operator.status not in released:
                blockers.append(
                    {
                        "code": "OPERATOR_NOT_RELEASED",
                        "node_id": node.id,
                        "operator_version_id": operator.id,
                        "status": operator.status.value,
                        "message": "Operator is not released for production execution.",
                    }
                )
            try:
                validate_parameters(operator.parameter_schema, node.parameters)
            except ParameterValidationError as exc:
                parameters_ok = False
                blockers.append(
                    {
                        "code": "PARAMETER_SCHEMA_VIOLATION",
                        "node_id": node.id,
                        "operator_version_id": operator.id,
                        "message": str(exc),
                    }
                )

    production_eligible = operators_ok and parameters_ok and not blockers
    ok = production_eligible
    return ToolResult(
        ok=ok,
        tool="validate_pipeline_artifact",
        status="succeeded" if ok else "failed",
        summary=(
            "PipelineArtifact is valid and production eligible."
            if ok
            else f"PipelineArtifact validation found {len(blockers)} blocker(s)."
        ),
        data={
            "pipeline_artifact_id": artifact_id,
            "pipeline_version_id": pipeline.id,
            "schema_ok": True,
            "checksum_ok": True,
            "operators_ok": operators_ok,
            "parameters_ok": parameters_ok,
            "production_eligible": production_eligible,
            "operators": operators,
            "blockers": blockers,
        },
        evidence=(
            ToolEvidence(kind="pipeline_artifact", id=artifact_id),
            ToolEvidence(kind="pipeline_version", id=pipeline.id),
        ),
        next_actions=("retrieve_operators",) if blockers else (),
        error_type=None if ok else "pipeline_artifact_blocked",
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
