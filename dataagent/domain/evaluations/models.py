from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from ..common.models import VersionedModel


class QCStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class QCReport(VersionedModel):
    work_order_id: str
    dataset_version_id: str
    pipeline_version_id: str
    task_spec_version_id: str
    run_id: str
    evaluator_version: str
    status: QCStatus
    full_hard_rule_check: bool
    semantic_quality_verified: bool = False
    metrics: dict[str, float] = Field(default_factory=dict)
    failed_asset_uris: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_report(self) -> "QCReport":
        required = {"hard_rule_violation_rate", "retention_rate", "execution_failure_rate"}
        if not required.issubset(self.metrics):
            raise ValueError("QCReport is missing required metrics")
        if any(value < 0 or value > 1 for value in self.metrics.values()):
            raise ValueError("QCReport rates must be between zero and one")
        return self
