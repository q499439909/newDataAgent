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
    RepairedDatasetVersion,
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


def _lineage_reference(
    asset: DatasetAsset,
    *,
    origin: AssetOrigin,
    origin_dataset_version_id: str,
    origin_run_id: str | None,
    audit_ref: str,
) -> DatasetAsset:
    audit_refs = tuple(dict.fromkeys((*asset.audit_refs, audit_ref)))
    return asset.model_copy(
        update={
            "asset_origin": origin,
            "origin_dataset_version_id": origin_dataset_version_id,
            "origin_run_id": origin_run_id,
            "materialization": (
                AssetMaterialization.REFERENCED_FILE
                if asset.output_uri
                else AssetMaterialization.NOT_MATERIALIZED
            ),
            "audit_refs": audit_refs,
        }
    )


def merge_repaired_dataset_version(
    *,
    dataset_id: str,
    owner_id: str,
    parent: DatasetVersion,
    repair_output: DatasetVersion,
    repair_run_id: str,
    repair_attempt: int,
    manifest_uri: str,
) -> RepairedDatasetVersion:
    repair_by_asset: dict[tuple[str, str], DatasetAsset] = {}
    for asset in repair_output.assets:
        key = (asset.source_uri, asset.source_sha256)
        if key in repair_by_asset:
            raise ValueError(f"Repair output contains duplicate asset identity: {key[0]}")
        repair_by_asset[key] = asset

    merged_assets: list[DatasetAsset] = []
    for index, parent_asset in enumerate(parent.assets):
        key = (parent_asset.source_uri, parent_asset.source_sha256)
        repaired_asset = repair_by_asset.pop(key, None)
        if parent_asset.decision != "failed":
            audit_ref = f"dataset:{parent.id}:asset:{index}"
            if repaired_asset is not None:
                audit_ref += f":repair:{repair_run_id}:ignored_out_of_scope"
            merged_assets.append(
                _lineage_reference(
                    parent_asset,
                    origin=AssetOrigin.PARENT_DATASET_VERSION,
                    origin_dataset_version_id=parent.id,
                    origin_run_id=parent_asset.origin_run_id,
                    audit_ref=audit_ref,
                )
            )
            continue
        selected = repaired_asset or parent_asset
        if repaired_asset is None:
            merged_assets.append(
                _lineage_reference(
                    selected,
                    origin=AssetOrigin.PARENT_DATASET_VERSION,
                    origin_dataset_version_id=parent.id,
                    origin_run_id=selected.origin_run_id,
                    audit_ref=f"dataset:{parent.id}:asset:{index}:repair_output_missing",
                )
            )
            continue
        merged_assets.append(
            _lineage_reference(
                selected,
                origin=AssetOrigin.REPAIR_RUN,
                origin_dataset_version_id=repair_output.id,
                origin_run_id=repair_run_id,
                audit_ref=f"repair:{repair_run_id}:asset:{index}",
            )
        )

    for index, repaired_asset in enumerate(repair_by_asset.values()):
        merged_assets.append(
            _lineage_reference(
                repaired_asset,
                origin=AssetOrigin.REPAIR_RUN,
                origin_dataset_version_id=repair_output.id,
                origin_run_id=repair_run_id,
                audit_ref=f"repair:{repair_run_id}:missing_parent_asset:{index}",
            )
        )

    latest_failures = tuple(
        DatasetAssetPointer(
            source_uri=asset.source_uri,
            source_sha256=asset.source_sha256,
            reason_codes=asset.reason_codes,
            audit_refs=asset.audit_refs,
            repair_attempts=repair_attempt,
        )
        for asset in merged_assets
        if asset.decision == "failed"
    )
    if repair_attempt >= 3:
        still_failed: tuple[DatasetAssetPointer, ...] = ()
        abandoned_assets = tuple(
            {
                (item.source_uri, item.source_sha256): item
                for item in (*parent.abandoned_assets, *latest_failures)
            }.values()
        )
    else:
        still_failed = latest_failures
        abandoned_assets = parent.abandoned_assets
    repair_run_ids = tuple(
        dict.fromkeys((*parent.repair_run_ids, repair_run_id))
    )
    merged = build_logical_dataset_version(
        dataset_id=dataset_id,
        owner_id=owner_id,
        work_order_id=parent.work_order_id,
        pipeline_version_id=parent.pipeline_version_id,
        task_spec_version_id=parent.task_spec_version_id,
        run_id=repair_run_id,
        source_roots=parent.source_roots,
        manifest_uri=manifest_uri,
        assets=merged_assets,
        version=parent.version + 1,
        change_reason="merged parent DatasetVersion with Repair Run output",
        parent_dataset_version_id=parent.id,
        repair_run_ids=repair_run_ids,
        still_failed=still_failed,
        abandoned_assets=abandoned_assets,
        excluded_assets=parent.excluded_assets,
    )
    return RepairedDatasetVersion.model_validate(merged.model_dump(mode="python"))


def exclude_abandoned_assets_version(
    *,
    dataset_id: str,
    owner_id: str,
    parent: DatasetVersion,
    manifest_uri: str,
    audit_ref: str,
) -> RepairedDatasetVersion:
    if not parent.abandoned_assets:
        raise ValueError("DatasetVersion has no abandoned assets to exclude")
    abandoned_by_key = {
        (item.source_uri, item.source_sha256): item
        for item in parent.abandoned_assets
    }
    excluded_assets: list[DatasetAsset] = []
    newly_excluded: list[DatasetAssetPointer] = []
    for index, asset in enumerate(parent.assets):
        key = (asset.source_uri, asset.source_sha256)
        abandoned = abandoned_by_key.get(key)
        if abandoned is None:
            if asset.decision == "excluded":
                excluded_assets.append(asset)
            else:
                excluded_assets.append(
                    _lineage_reference(
                        asset,
                        origin=AssetOrigin.PARENT_DATASET_VERSION,
                        origin_dataset_version_id=parent.id,
                        origin_run_id=asset.origin_run_id,
                        audit_ref=f"dataset:{parent.id}:asset:{index}",
                    )
                )
            continue
        reason_codes = tuple(
            dict.fromkeys((*asset.reason_codes, "USER_CONFIRMED_EXCLUSION"))
        )
        audit_refs = tuple(dict.fromkeys((*asset.audit_refs, audit_ref)))
        excluded = asset.model_copy(
            update={
                "decision": "excluded",
                "reason_codes": reason_codes,
                "asset_origin": AssetOrigin.EXCLUDED,
                "origin_dataset_version_id": parent.id,
                "materialization": AssetMaterialization.NOT_MATERIALIZED,
                "output_uri": None,
                "output_sha256": None,
                "audit_refs": audit_refs,
            }
        )
        excluded_assets.append(excluded)
        newly_excluded.append(
            DatasetAssetPointer(
                source_uri=excluded.source_uri,
                source_sha256=excluded.source_sha256,
                reason_codes=reason_codes,
                audit_refs=audit_refs,
                repair_attempts=abandoned.repair_attempts,
            )
        )
    inherited_excluded = {
        (item.source_uri, item.source_sha256): item
        for item in parent.excluded_assets
    }
    inherited_excluded.update(
        {
            (item.source_uri, item.source_sha256): item
            for item in newly_excluded
        }
    )
    resolved = build_logical_dataset_version(
        dataset_id=dataset_id,
        owner_id=owner_id,
        work_order_id=parent.work_order_id,
        pipeline_version_id=parent.pipeline_version_id,
        task_spec_version_id=parent.task_spec_version_id,
        run_id=parent.run_id,
        source_roots=parent.source_roots,
        manifest_uri=manifest_uri,
        assets=excluded_assets,
        version=parent.version + 1,
        change_reason="user confirmed exclusion of abandoned assets",
        parent_dataset_version_id=parent.id,
        repair_run_ids=parent.repair_run_ids,
        still_failed=parent.still_failed,
        abandoned_assets=(),
        excluded_assets=tuple(inherited_excluded.values()),
    )
    return RepairedDatasetVersion.model_validate(resolved.model_dump(mode="python"))


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
