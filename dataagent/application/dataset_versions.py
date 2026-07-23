from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Iterable

from ..domain.common.models import DomainModel
from ..domain.runs import (
    AssetMaterialization,
    AssetOrigin,
    DatasetAsset,
    DatasetAssetPointer,
    DatasetVersion,
    DatasetVersionKind,
)


class DatasetReferenceIssue(DomainModel):
    code: str
    source_uri: str
    output_uri: str | None = None
    detail: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pointer(asset: DatasetAsset) -> DatasetAssetPointer:
    return DatasetAssetPointer(
        source_uri=asset.source_uri,
        source_sha256=asset.source_sha256,
        reason_codes=asset.reason_codes,
        audit_refs=asset.audit_refs,
    )


def build_logical_dataset_version(
    *,
    dataset_id: str,
    owner_id: str,
    work_order_id: str,
    pipeline_version_id: str,
    task_spec_version_id: str,
    run_id: str,
    source_roots: tuple[str, ...],
    manifest_uri: str,
    assets: Iterable[DatasetAsset],
    version: int = 1,
    change_reason: str = "approved pipeline dataset run",
    parent_dataset_version_id: str | None = None,
    repair_run_ids: tuple[str, ...] = (),
    still_failed: tuple[DatasetAssetPointer, ...] | None = None,
    abandoned_assets: tuple[DatasetAssetPointer, ...] = (),
    excluded_assets: tuple[DatasetAssetPointer, ...] = (),
) -> DatasetVersion:
    normalized_assets: list[DatasetAsset] = []
    for index, asset in enumerate(assets):
        updates: dict[str, object] = {}
        if asset.asset_origin == AssetOrigin.RUN_OUTPUT and not asset.origin_run_id:
            updates["origin_run_id"] = run_id
        if not asset.audit_refs:
            updates["audit_refs"] = (f"run:{run_id}:asset:{index}",)
        normalized_assets.append(asset.model_copy(update=updates) if updates else asset)
    asset_tuple = tuple(normalized_assets)
    failed = (
        tuple(_pointer(item) for item in asset_tuple if item.decision == "failed")
        if still_failed is None
        else still_failed
    )
    return DatasetVersion(
        id=dataset_id,
        version=version,
        parent_version_id=parent_dataset_version_id,
        created_by=owner_id,
        change_reason=change_reason,
        work_order_id=work_order_id,
        pipeline_version_id=pipeline_version_id,
        task_spec_version_id=task_spec_version_id,
        run_id=run_id,
        source_roots=source_roots,
        manifest_uri=str(Path(manifest_uri).resolve()),
        assets=asset_tuple,
        source_count=len(asset_tuple),
        kept_count=sum(item.decision == "keep" for item in asset_tuple),
        rejected_count=sum(
            item.decision in {"reject", "excluded"} for item in asset_tuple
        ),
        failed_count=sum(item.decision in {"failed", "abandoned"} for item in asset_tuple),
        original_files_unchanged=True,
        version_kind=DatasetVersionKind.LOGICAL,
        parent_dataset_version_id=parent_dataset_version_id,
        repair_run_ids=repair_run_ids,
        still_failed=failed,
        abandoned_assets=abandoned_assets,
        excluded_assets=excluded_assets,
        deliverable=False,
    )


def validate_dataset_references(
    dataset: DatasetVersion,
) -> tuple[DatasetReferenceIssue, ...]:
    issues: list[DatasetReferenceIssue] = []
    for asset in dataset.assets:
        if asset.materialization == AssetMaterialization.NOT_MATERIALIZED:
            if asset.decision == "keep":
                issues.append(
                    DatasetReferenceIssue(
                        code="KEPT_ASSET_NOT_MATERIALIZED",
                        source_uri=asset.source_uri,
                        detail="Kept asset has no materialized or referenced output",
                    )
                )
            continue
        if not asset.output_uri or not asset.output_sha256:
            issues.append(
                DatasetReferenceIssue(
                    code="OUTPUT_REFERENCE_INCOMPLETE",
                    source_uri=asset.source_uri,
                    output_uri=asset.output_uri,
                    detail="Materialized asset requires output URI and SHA256",
                )
            )
            continue
        output = Path(asset.output_uri)
        if not output.is_file():
            issues.append(
                DatasetReferenceIssue(
                    code="OUTPUT_MISSING",
                    source_uri=asset.source_uri,
                    output_uri=asset.output_uri,
                    detail="Referenced output file does not exist",
                )
            )
            continue
        actual_sha256 = _sha256(output)
        if actual_sha256 != asset.output_sha256:
            issues.append(
                DatasetReferenceIssue(
                    code="OUTPUT_HASH_MISMATCH",
                    source_uri=asset.source_uri,
                    output_uri=asset.output_uri,
                    detail=(
                        f"Expected {asset.output_sha256}, observed {actual_sha256}"
                    ),
                )
            )
    return tuple(issues)


def write_dataset_manifest(dataset: DatasetVersion) -> Path:
    manifest = Path(dataset.manifest_uri).resolve()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_name(f".{manifest.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, manifest)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest
