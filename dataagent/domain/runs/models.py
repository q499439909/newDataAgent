from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from ..common.models import DomainModel, VersionedModel
from ..operators import AnnotationRef, AssetRef, EmbeddingRef


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    EVALUATING = "EVALUATING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class DatasetVersionKind(StrEnum):
    LOGICAL = "logical"


class AssetOrigin(StrEnum):
    RUN_OUTPUT = "run_output"
    PARENT_DATASET_VERSION = "parent_dataset_version"
    REPAIR_RUN = "repair_run"
    EXCLUDED = "excluded"


class AssetMaterialization(StrEnum):
    LOCAL_FILE = "local_file"
    REFERENCED_FILE = "referenced_file"
    NOT_MATERIALIZED = "not_materialized"


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
    qc_report_id: str | None = None
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
    artifacts: tuple[AssetRef, ...] = ()
    annotations: tuple[AnnotationRef, ...] = ()
    embeddings: tuple[EmbeddingRef, ...] = ()
    asset_origin: AssetOrigin
    origin_dataset_version_id: str | None = None
    origin_run_id: str | None = None
    materialization: AssetMaterialization
    audit_refs: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def populate_logical_defaults(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        payload.setdefault("asset_origin", AssetOrigin.RUN_OUTPUT)
        payload.setdefault(
            "materialization",
            (
                AssetMaterialization.LOCAL_FILE
                if payload.get("output_uri")
                else AssetMaterialization.NOT_MATERIALIZED
            ),
        )
        return payload

    @model_validator(mode="after")
    def validate_materialization(self) -> "DatasetAsset":
        if self.materialization == AssetMaterialization.NOT_MATERIALIZED:
            if self.output_uri is not None or self.output_sha256 is not None:
                raise ValueError("Non-materialized assets cannot have output references")
        elif not self.output_uri or not self.output_sha256:
            raise ValueError("Materialized assets require output URI and SHA256")
        if (
            self.asset_origin == AssetOrigin.PARENT_DATASET_VERSION
            and not self.origin_dataset_version_id
        ):
            raise ValueError("Parent dataset assets require origin_dataset_version_id")
        if self.asset_origin == AssetOrigin.EXCLUDED and self.decision != "excluded":
            raise ValueError("Excluded asset origin requires an excluded decision")
        return self


class DatasetAssetPointer(DomainModel):
    source_uri: str
    source_sha256: str
    reason_codes: tuple[str, ...] = ()
    audit_refs: tuple[str, ...] = ()
    repair_attempts: int = Field(default=0, ge=0)


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
    version_kind: DatasetVersionKind = DatasetVersionKind.LOGICAL
    parent_dataset_version_id: str | None = None
    repair_run_ids: tuple[str, ...] = ()
    still_failed: tuple[DatasetAssetPointer, ...] = ()
    abandoned_assets: tuple[DatasetAssetPointer, ...] = ()
    excluded_assets: tuple[DatasetAssetPointer, ...] = ()
    deliverable: bool = False

    @model_validator(mode="before")
    @classmethod
    def populate_failed_asset_pointers(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "still_failed" in value:
            return value
        payload = dict(value)
        failed: list[dict[str, Any]] = []
        for item in payload.get("assets", ()):
            if isinstance(item, DatasetAsset):
                decision = item.decision
                source_uri = item.source_uri
                source_sha256 = item.source_sha256
                reason_codes = item.reason_codes
                audit_refs = item.audit_refs
            else:
                decision = item.get("decision")
                source_uri = item.get("source_uri")
                source_sha256 = item.get("source_sha256")
                reason_codes = item.get("reason_codes", ())
                audit_refs = item.get("audit_refs", ())
            if decision == "failed":
                failed.append(
                    {
                        "source_uri": source_uri,
                        "source_sha256": source_sha256,
                        "reason_codes": reason_codes,
                        "audit_refs": audit_refs,
                    }
                )
        payload["still_failed"] = failed
        return payload

    @model_validator(mode="after")
    def validate_summary(self) -> "DatasetVersion":
        if self.source_count != len(self.assets):
            raise ValueError("Dataset source count must equal its asset manifest")
        if self.kept_count + self.rejected_count + self.failed_count != self.source_count:
            raise ValueError("Dataset outcome counts must equal source count")
        if not self.original_files_unchanged:
            raise ValueError("DatasetVersion cannot publish after source mutation")
        if self.deliverable:
            raise ValueError(
                "DatasetVersion is logical; materialized delivery uses a Dataset Export"
            )
        asset_keys = {(item.source_uri, item.source_sha256) for item in self.assets}
        groups = {
            "still_failed": self.still_failed,
            "abandoned_assets": self.abandoned_assets,
            "excluded_assets": self.excluded_assets,
        }
        seen: set[tuple[str, str]] = set()
        for name, pointers in groups.items():
            keys = {(item.source_uri, item.source_sha256) for item in pointers}
            if not keys <= asset_keys:
                raise ValueError(f"{name} must reference assets in the DatasetVersion")
            if seen & keys:
                raise ValueError("Asset disposition lists cannot overlap")
            seen.update(keys)
        if (
            self.parent_version_id
            and self.parent_dataset_version_id
            and self.parent_version_id != self.parent_dataset_version_id
        ):
            raise ValueError("Dataset parent lineage fields must agree")
        return self
