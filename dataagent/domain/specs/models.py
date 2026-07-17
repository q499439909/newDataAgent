from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ..common.models import DomainModel, VersionedModel


class DataSourceSpec(DomainModel):
    type: Literal["local_directory", "milvus", "object_storage", "database"]
    uri: str
    collection: str | None = None
    mapping: dict[str, str] = Field(default_factory=dict)


class AcceptanceSpec(DomainModel):
    hard_rule_violation_rate: float = Field(default=0.0, ge=0, le=1)
    boundary_review_size: int = Field(default=20, ge=1, le=500)
    semantic_metrics: dict[str, float] = Field(default_factory=dict)
    model_metrics: dict[str, float] = Field(default_factory=dict)


class TaskSpecVersion(VersionedModel):
    work_order_id: str
    objective: str
    data_sources: tuple[DataSourceSpec, ...]
    output_actions: tuple[str, ...] = ("filter", "manifest")
    required_capabilities: tuple[str, ...] = ()
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    semantic_requirements: tuple[str, ...] = ()
    exclusion_requirements: tuple[str, ...] = ()
    preferences: dict[str, Any] = Field(default_factory=dict)
    quotas: dict[str, int] = Field(default_factory=dict)
    acceptance: AcceptanceSpec = Field(default_factory=AcceptanceSpec)
    ambiguities: tuple[str, ...] = ()
    confirmed: bool = False

    @model_validator(mode="after")
    def validate_task(self) -> "TaskSpecVersion":
        if not self.objective.strip():
            raise ValueError("Task objective must not be empty")
        if not self.data_sources:
            raise ValueError("At least one data source is required")
        if any(value < 0 for value in self.quotas.values()):
            raise ValueError("Quota values must be non-negative")
        return self

    def confirm(self, actor: str) -> "TaskSpecVersion":
        if self.ambiguities:
            raise ValueError("TaskSpec cannot be confirmed with unresolved ambiguities")
        return self.model_copy(update={"confirmed": True, "created_by": actor})
