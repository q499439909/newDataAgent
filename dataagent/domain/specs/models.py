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


class TaskCapabilitySpec(DomainModel):
    id: str
    capability: str
    description: str
    depends_on: tuple[str, ...] = ()
    required: bool = True


class ClassificationLabelSpec(DomainModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()


class ClassificationSpec(DomainModel):
    mode: Literal["closed_set"] = "closed_set"
    labels: tuple[ClassificationLabelSpec, ...] = Field(min_length=2)
    mixed_label: str = Field(default="mixed", pattern=r"^[a-z0-9][a-z0-9_-]*$")
    unknown_label: str = Field(default="unknown", pattern=r"^[a-z0-9][a-z0-9_-]*$")

    @model_validator(mode="after")
    def validate_labels(self) -> "ClassificationSpec":
        label_ids = [item.id for item in self.labels]
        if len(label_ids) != len(set(label_ids)):
            raise ValueError("Classification label ids must be unique")
        if self.mixed_label == self.unknown_label:
            raise ValueError("Mixed and unknown labels must be different")
        if {self.mixed_label, self.unknown_label}.intersection(label_ids):
            raise ValueError("Task labels cannot reuse mixed or unknown labels")
        return self


class TaskSpecVersion(VersionedModel):
    work_order_id: str
    objective: str
    data_sources: tuple[DataSourceSpec, ...]
    output_actions: tuple[str, ...] = ("filter", "manifest")
    required_capabilities: tuple[str, ...] = ()
    capability_requirements: tuple[TaskCapabilitySpec, ...] = ()
    classification: ClassificationSpec | None = None
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
        capability_ids = [item.id for item in self.capability_requirements]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("Task capability ids must be unique")
        known = set(capability_ids)
        for item in self.capability_requirements:
            unknown = set(item.depends_on).difference(known)
            if unknown:
                raise ValueError(
                    f"Task capability {item.id} has unknown dependencies: {sorted(unknown)}"
                )
            if item.id in item.depends_on:
                raise ValueError("Task capabilities cannot depend on themselves")
        self._assert_capability_dag()
        return self

    def _assert_capability_dag(self) -> None:
        adjacency = {item.id: set() for item in self.capability_requirements}
        indegree = {item.id: 0 for item in self.capability_requirements}
        for item in self.capability_requirements:
            for dependency in item.depends_on:
                adjacency[dependency].add(item.id)
                indegree[item.id] += 1
        ready = [item_id for item_id, degree in indegree.items() if degree == 0]
        visited = 0
        while ready:
            current = ready.pop()
            visited += 1
            for target in adjacency[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if visited != len(adjacency):
            raise ValueError("Task capability graph must be acyclic")

    def confirm(self, actor: str) -> "TaskSpecVersion":
        if self.ambiguities:
            raise ValueError("TaskSpec cannot be confirmed with unresolved ambiguities")
        return self.model_copy(update={"confirmed": True, "created_by": actor})
