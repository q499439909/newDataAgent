from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from ..common.models import DomainModel, VersionedModel


class ExperienceStatus(StrEnum):
    CANDIDATE = "candidate"
    RECOMMENDED = "recommended"
    REJECTED = "rejected"


class TaskSignature(DomainModel):
    modality: str = "image"
    capabilities: tuple[str, ...]
    output_actions: tuple[str, ...]
    classification_mode: str | None = None
    label_ids: tuple[str, ...] = ()
    normalized_summary: str


class RunFeedback(VersionedModel):
    run_id: str
    work_order_id: str
    rating: int | None = Field(default=None, ge=1, le=5)
    accepted: bool
    reusable: bool = False
    comment: str = ""


class PipelineExperience(VersionedModel):
    task_spec_version_id: str
    pipeline_version_id: str
    run_id: str
    dataset_version_id: str | None = None
    qc_report_id: str | None = None
    feedback_id: str
    strategy: str
    task_signature: TaskSignature
    run_status: str
    qc_status: str | None = None
    outcome_metrics: dict[str, float] = Field(default_factory=dict)
    rating: int | None = Field(default=None, ge=1, le=5)
    accepted: bool
    reusable: bool
    status: ExperienceStatus
    compatibility: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ExperienceStatus",
    "PipelineExperience",
    "RunFeedback",
    "TaskSignature",
]
