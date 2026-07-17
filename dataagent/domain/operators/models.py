from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from ..common.models import DomainModel, VersionedModel


class OperatorCategory(StrEnum):
    INGESTION = "INGESTION"
    FILTERING = "FILTERING"
    DEDUPLICATION = "DEDUPLICATION"
    UNDERSTANDING = "UNDERSTANDING"
    TRANSFORMATION = "TRANSFORMATION"
    ENHANCEMENT = "ENHANCEMENT"
    SAMPLING = "SAMPLING"
    EVALUATION = "EVALUATION"
    OUTPUT = "OUTPUT"


class OperatorStatus(StrEnum):
    DRAFT = "DRAFT"
    EVALUATED = "EVALUATED"
    PERSONAL_RELEASE = "PERSONAL_RELEASE"
    PUBLIC_RELEASE = "PUBLIC_RELEASE"
    DEPRECATED = "DEPRECATED"


class ImplementationType(StrEnum):
    CODE = "code"
    MODEL = "model"
    EXTERNAL_SERVICE = "external_service"
    CONTAINER = "container"


class RuntimeBackend(StrEnum):
    MOCK = "mock"
    CPU = "cpu"
    CUDA = "cuda"
    REMOTE = "remote"


class ExecutionScope(StrEnum):
    ASSET = "asset"
    DATASET = "dataset"


class ModelSource(StrEnum):
    OPEN_SOURCE = "open_source"
    INTERNAL = "internal"
    COMMERCIAL = "commercial"


class ProviderRef(DomainModel):
    provider_id: str = "native"
    provider_version: str = "0.1.0"
    provider_operator_ref: str = ""
    source_digest: str = ""


class ImplementationSpec(DomainModel):
    implementation_type: ImplementationType = ImplementationType.CODE
    entrypoint: str = ""
    dependency_lock_digest: str | None = None


class RuntimeProfile(DomainModel):
    backend: RuntimeBackend = RuntimeBackend.CPU
    cpu: float = Field(default=1.0, ge=0)
    memory_mb: int = Field(default=256, ge=0)
    gpu_count: int = Field(default=0, ge=0)
    gpu_memory_mb: int = Field(default=0, ge=0)
    timeout_seconds: int = Field(default=300, ge=1)
    concurrency: int = Field(default=1, ge=1)


class ModelRequirement(DomainModel):
    model_source: ModelSource
    model_id: str
    revision: str
    sha256: str
    code_license: str
    checkpoint_license: str
    estimated_size_bytes: int = Field(ge=0)
    requires_explicit_license_acceptance: bool = False

    @model_validator(mode="after")
    def validate_model_identity(self) -> "ModelRequirement":
        if not self.model_id.strip() or not self.revision.strip():
            raise ValueError("Model id and revision are required")
        invalid_sha = len(self.sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.sha256.lower()
        )
        if self.sha256 and invalid_sha:
            raise ValueError("Model SHA256 must be empty or a 64-character hexadecimal digest")
        return self


class AssetRef(DomainModel):
    uri: str
    media_type: str
    sha256: str | None = None


class AnnotationRef(DomainModel):
    annotation_type: Literal["box", "mask", "keypoints", "label", "score"]
    payload_uri: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class EmbeddingRef(DomainModel):
    vector_id: str
    model_version_id: str
    dimensions: int = Field(ge=1)


class ExampleType(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    BOUNDARY = "BOUNDARY"
    BEFORE_AFTER = "BEFORE_AFTER"


class OperatorSpecVersion(VersionedModel):
    family_id: str
    display_name: str
    summary: str
    description: str
    primary_category: OperatorCategory
    secondary_category: str
    capability_tags: frozenset[str] = frozenset()
    input_schema: str
    output_schema: str
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
    provider: ProviderRef = Field(default_factory=ProviderRef)
    implementation: ImplementationSpec = Field(default_factory=ImplementationSpec)
    supported_runtime_profiles: tuple[RuntimeProfile, ...] = Field(
        default_factory=lambda: (RuntimeProfile(),)
    )
    execution_scope: ExecutionScope = ExecutionScope.ASSET
    model_requirement: ModelRequirement | None = None
    # Kept for compatibility with the first milestone. New code uses `implementation`.
    implementation_type: str = "python"
    implementation_ref: str
    resource_requirements: dict[str, Any] = Field(default_factory=dict)
    failure_policy: str = "fail_node"
    side_effects: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    status: OperatorStatus = OperatorStatus.DRAFT
    owner_id: str
    visibility: str = "private"

    @model_validator(mode="after")
    def validate_description(self) -> "OperatorSpecVersion":
        if not self.summary.strip() or not self.description.strip():
            raise ValueError("Operator summary and description are required")
        if not self.secondary_category.strip():
            raise ValueError("Operator secondary category is required")
        if not self.supported_runtime_profiles:
            raise ValueError("Operator must support at least one runtime profile")
        if (
            self.implementation.implementation_type == ImplementationType.MODEL
            and self.model_requirement is None
        ):
            raise ValueError("Model operators require model metadata")
        return self


class OperatorExample(DomainModel):
    id: str
    example_type: ExampleType
    source_asset_id: str
    input_preview_uri: str
    output_preview_uri: str | None = None
    labels_before: dict[str, Any] = Field(default_factory=dict)
    labels_after: dict[str, Any] = Field(default_factory=dict)
    decision: str | None = None
    score: float | None = None
    explanation: str
    task_id: str
    pipeline_version_id: str
    run_id: str
    evaluation_id: str


class OperatorExampleSet(VersionedModel):
    operator_version_id: str
    examples: tuple[OperatorExample, ...]
    supported_task_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_examples(self) -> "OperatorExampleSet":
        if not self.examples:
            raise ValueError("An operator example set must contain at least one example")
        return self
