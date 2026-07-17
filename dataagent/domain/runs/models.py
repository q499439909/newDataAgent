from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from ..common.models import DomainModel, VersionedModel


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RunSnapshot(DomainModel):
    id: str
    work_order_id: str
    owner_id: str
    pipeline_version_id: str
    task_spec_version_id: str
    status: RunStatus
    progress: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    kept: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    idempotency_key: str
    dataset_version_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_counts(self) -> "RunSnapshot":
        if self.total and self.progress > self.total:
            raise ValueError("Run progress cannot exceed total")
        if self.kept + self.rejected + self.failed > self.progress:
            raise ValueError("Run outcome counts cannot exceed progress")
        return self


class DatasetAsset(DomainModel):
    source_uri: str
    source_sha256: str
    output_uri: str | None = None
    output_sha256: str | None = None
    decision: str
    reason_codes: tuple[str, ...] = ()
    metrics: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, Any] = Field(default_factory=dict)


class DatasetVersion(VersionedModel):
    work_order_id: str
    pipeline_version_id: str
    task_spec_version_id: str
    run_id: str
    source_roots: tuple[str, ...]
    manifest_uri: str
    assets: tuple[DatasetAsset, ...]
    source_count: int = Field(ge=0)
    kept_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    original_files_unchanged: bool

    @model_validator(mode="after")
    def validate_summary(self) -> "DatasetVersion":
        if self.source_count != len(self.assets):
            raise ValueError("Dataset source count must equal its asset manifest")
        if self.kept_count + self.rejected_count + self.failed_count != self.source_count:
            raise ValueError("Dataset outcome counts must equal source count")
        if not self.original_files_unchanged:
            raise ValueError("DatasetVersion cannot publish after source mutation")
        return self
