from __future__ import annotations

from enum import StrEnum
from typing import Any

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
