from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskState(StrEnum):
    DRAFT = "DRAFT"
    PLANNING = "PLANNING"
    WAITING_SPEC_CONFIRMATION = "WAITING_SPEC_CONFIRMATION"
    TRIAL_RUNNING = "TRIAL_RUNNING"
    WAITING_REVIEW = "WAITING_REVIEW"
    FULL_RUNNING = "FULL_RUNNING"
    EVALUATING = "EVALUATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HardConstraints(BaseModel):
    min_width: int | None = Field(default=None, ge=1)
    min_height: int | None = Field(default=None, ge=1)
    min_short_edge: int | None = Field(default=None, ge=1)
    max_width: int | None = Field(default=None, ge=1)
    max_height: int | None = Field(default=None, ge=1)
    allowed_formats: list[str] = Field(default_factory=list)
    aspect_ratio_min: float | None = Field(default=None, gt=0)
    aspect_ratio_max: float | None = Field(default=None, gt=0)

    @field_validator("allowed_formats")
    @classmethod
    def normalize_formats(cls, value: list[str]) -> list[str]:
        return sorted({item.lower().lstrip(".") for item in value if item})


class TransformSpec(BaseModel):
    resize_long_edge: int | None = Field(default=None, ge=1)
    output_format: str | None = None
    autocontrast: bool = False
    center_crop_ratio: float | None = Field(default=None, gt=0)


class TaskSpec(BaseModel):
    objective: str
    source_type: Literal["local", "milvus", "hybrid"] = "local"
    source_path: str | None = None
    output_actions: list[str] = Field(default_factory=lambda: ["filter", "manifest"])
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    semantic_requirements: list[str] = Field(default_factory=list)
    exclusion_requirements: list[str] = Field(default_factory=list)
    transforms: TransformSpec = Field(default_factory=TransformSpec)
    ambiguities: list[str] = Field(default_factory=list)
    acceptance_notes: list[str] = Field(default_factory=list)


class PipelineDefinition(BaseModel):
    strategy: Literal["retain", "balanced", "quality"]
    display_name: str
    description: str
    blur_min: float
    brightness_min: float
    brightness_max: float
    semantic_min: float
    deduplicate: bool = True
    operators: list[str]


class ImageMetrics(BaseModel):
    path: str
    sha256: str
    dhash: str
    width: int
    height: int
    aspect_ratio: float = 0.0
    file_size_bytes: int = 0
    format: str
    brightness: float
    blur_score: float
    decode_ok: bool = True
    error: str | None = None
    semantic_score: float | None = None
    semantic_pass: bool | None = None
    semantic_reason: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)


class ImageDecision(BaseModel):
    path: str
    keep: bool
    reasons: list[str] = Field(default_factory=list)
    metrics: ImageMetrics
    transformed_path: str | None = None


class CandidateReport(BaseModel):
    pipeline_id: str
    strategy: str
    display_name: str
    sample_size: int
    kept: int
    rejected: int
    retention_rate: float
    hard_constraint_pass_rate: float
    semantic_evaluated: int
    semantic_pass_rate: float | None = None
    api_input_tokens: int = 0
    api_output_tokens: int = 0
    elapsed_seconds: float
    decisions: list[ImageDecision]
    boundary_paths: list[str] = Field(default_factory=list)
    proxy_only: bool = True
    human_reviewed: int = 0
    human_acceptance_rate: float | None = None


class TrialBundle(BaseModel):
    task_id: str
    created_at: str = Field(default_factory=utc_now)
    sample_paths: list[str]
    candidates: list[CandidateReport]


class DatasetManifest(BaseModel):
    dataset_id: str
    task_id: str
    pipeline_id: str
    created_at: str = Field(default_factory=utc_now)
    source_root: str
    output_root: str
    source_count: int
    kept_count: int
    rejected_count: int
    original_files_unchanged: bool
    files: list[ImageDecision]
    task_spec: TaskSpec
    pipeline: PipelineDefinition


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ModelResult(BaseModel):
    text: str
    usage: ModelUsage = Field(default_factory=ModelUsage)
    model: str
    request_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)
