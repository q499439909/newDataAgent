from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import Field

from ..domain.common import new_id, utc_now
from ..domain.common.models import DomainModel
from ..domain.runs import DatasetAsset, DatasetVersion, RunStatus
from .dataset_versions import validate_dataset_references


class DeliverableDatasetExport(DomainModel):
    id: str
    dataset_version_id: str
    root_uri: str
    manifest_uri: str
    excluded_assets_uri: str
    file_count: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(asset: DatasetAsset) -> Path:
    configured = asset.labels.get("output_relative_path")
    if configured:
        relative = Path(str(configured))
    else:
        output = Path(str(asset.output_uri))
        parts = output.parts
        file_indexes = [
            index for index, part in enumerate(parts) if part.casefold() == "files"
        ]
        relative = (
            Path(*parts[file_indexes[-1] + 1 :])
            if file_indexes and file_indexes[-1] + 1 < len(parts)
            else Path(f"{asset.output_sha256[:12]}_{output.name}")
        )
    if relative.is_absolute() or ".." in relative.parts or not relative.name:
        raise ValueError(f"Unsafe Dataset export path: {relative}")
    return relative


def export_deliverable_dataset(
    *,
    dataset: DatasetVersion,
    run_status: str,
    destination: Path,
) -> DeliverableDatasetExport:
    if run_status != RunStatus.SUCCEEDED:
        raise ValueError("Deliverable Dataset Export requires a SUCCEEDED DatasetVersion")
    if dataset.still_failed:
        raise ValueError("DatasetVersion still_failed assets must be resolved before export")
    if dataset.abandoned_assets:
        raise ValueError(
            "DatasetVersion abandoned assets must be explicitly excluded before export"
        )

    manifest_source = Path(dataset.manifest_uri)
    if not manifest_source.is_file():
        raise ValueError(f"DatasetVersion manifest is missing: {manifest_source}")
    try:
        manifest_payload = json.loads(manifest_source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"DatasetVersion manifest is unreadable: {manifest_source}") from exc
    if manifest_payload.get("id") != dataset.id:
        raise ValueError("DatasetVersion manifest identity does not match the requested version")

    issues = validate_dataset_references(dataset)
    if issues:
        issue = issues[0]
        raise ValueError(
            f"DatasetVersion reference validation failed: {issue.code}: "
            f"{issue.output_uri or issue.source_uri}"
        )

    target = destination.expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"Dataset export destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    if staging.exists():
        raise FileExistsError(f"Dataset export staging path already exists: {staging}")

    copied_paths: set[str] = set()
    try:
        files_root = staging / "files"
        files_root.mkdir(parents=True)
        file_count = 0
        for asset in dataset.assets:
            if asset.decision != "keep":
                continue
            relative = _safe_relative_path(asset)
            collision_key = relative.as_posix().casefold()
            if collision_key in copied_paths:
                raise ValueError(f"Dataset export path collision: {relative.as_posix()}")
            copied_paths.add(collision_key)
            destination_file = files_root / relative
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(str(asset.output_uri)), destination_file)
            if _sha256(destination_file) != asset.output_sha256:
                raise ValueError(
                    f"Dataset export copy hash mismatch: {relative.as_posix()}"
                )
            file_count += 1

        (staging / "manifest.json").write_text(
            json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (staging / "excluded_assets.json").write_text(
            json.dumps(
                {
                    "dataset_version_id": dataset.id,
                    "excluded_assets": [
                        item.model_dump(mode="json") for item in dataset.excluded_assets
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return DeliverableDatasetExport(
        id=new_id("export"),
        dataset_version_id=dataset.id,
        root_uri=str(target),
        manifest_uri=str(target / "manifest.json"),
        excluded_assets_uri=str(target / "excluded_assets.json"),
        file_count=file_count,
    )
