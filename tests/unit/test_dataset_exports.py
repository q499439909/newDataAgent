from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dataagent.application.dataset_exports import export_deliverable_dataset
from dataagent.application.dataset_versions import (
    build_logical_dataset_version,
    write_dataset_manifest,
)
from dataagent.domain.runs import DatasetAsset, DatasetAssetPointer


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dataset(
    tmp_path: Path,
    *,
    failed: bool = False,
    excluded: bool = False,
):
    source = tmp_path / "source" / "cat.png"
    output = tmp_path / "logical" / "files" / "classes" / "cat" / "cat.png"
    source.parent.mkdir(parents=True)
    output.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    output.write_bytes(b"published")
    kept = DatasetAsset(
        source_uri=str(source),
        source_sha256=_sha256(source),
        output_uri=str(output),
        output_sha256=_sha256(output),
        decision="keep",
    )
    assets = [kept]
    failed_pointer: DatasetAssetPointer | None = None
    if failed:
        failed_source = tmp_path / "source" / "failed.png"
        failed_source.write_bytes(b"failed")
        failed_asset = DatasetAsset(
            source_uri=str(failed_source),
            source_sha256=_sha256(failed_source),
            decision="failed",
            reason_codes=("OPERATOR_ERROR:TimeoutError",),
            audit_refs=("run:run_1:asset:1",),
        )
        assets.append(failed_asset)
        failed_pointer = DatasetAssetPointer(
            source_uri=failed_asset.source_uri,
            source_sha256=failed_asset.source_sha256,
            reason_codes=failed_asset.reason_codes,
            audit_refs=failed_asset.audit_refs,
            repair_attempts=3,
        )
    dataset = build_logical_dataset_version(
        dataset_id="dataset_1",
        owner_id="owner_1",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="task_spec_1",
        run_id="run_1",
        source_roots=(str(tmp_path / "source"),),
        manifest_uri=str(tmp_path / "logical" / "manifest.json"),
        assets=assets,
        still_failed=() if excluded else None,
        excluded_assets=(failed_pointer,) if excluded and failed_pointer else (),
    )
    write_dataset_manifest(dataset)
    return dataset, output


@pytest.mark.parametrize("status", ["PARTIAL", "FAILED", "RUNNING"])
def test_export_requires_succeeded_run(tmp_path: Path, status: str) -> None:
    dataset, _ = _dataset(tmp_path)

    with pytest.raises(ValueError, match="SUCCEEDED"):
        export_deliverable_dataset(
            dataset=dataset,
            run_status=status,
            destination=tmp_path / "export",
        )

    assert not (tmp_path / "export").exists()


def test_export_rejects_unresolved_failures(tmp_path: Path) -> None:
    dataset, _ = _dataset(tmp_path, failed=True)

    with pytest.raises(ValueError, match="still_failed"):
        export_deliverable_dataset(
            dataset=dataset,
            run_status="SUCCEEDED",
            destination=tmp_path / "export",
        )


def test_succeeded_dataset_exports_files_manifest_and_exclusions(
    tmp_path: Path,
) -> None:
    dataset, _ = _dataset(tmp_path, failed=True, excluded=True)

    result = export_deliverable_dataset(
        dataset=dataset,
        run_status="SUCCEEDED",
        destination=tmp_path / "export",
    )

    export_root = tmp_path / "export"
    exported_file = export_root / "files" / "classes" / "cat" / "cat.png"
    assert result.dataset_version_id == dataset.id
    assert result.file_count == 1
    assert Path(result.root_uri) == export_root.resolve()
    assert _sha256(exported_file) == dataset.assets[0].output_sha256
    manifest = json.loads((export_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == dataset.id
    exclusions = json.loads(
        (export_root / "excluded_assets.json").read_text(encoding="utf-8")
    )
    assert exclusions["dataset_version_id"] == dataset.id
    assert exclusions["excluded_assets"][0]["reason_codes"] == [
        "OPERATOR_ERROR:TimeoutError"
    ]
    assert exclusions["excluded_assets"][0]["audit_refs"] == ["run:run_1:asset:1"]


def test_export_fails_atomically_for_missing_or_changed_reference(
    tmp_path: Path,
) -> None:
    dataset, output = _dataset(tmp_path)
    output.write_bytes(b"changed")

    with pytest.raises(ValueError, match="OUTPUT_HASH_MISMATCH"):
        export_deliverable_dataset(
            dataset=dataset,
            run_status="SUCCEEDED",
            destination=tmp_path / "export",
        )

    assert not (tmp_path / "export").exists()
    output.unlink()
    with pytest.raises(ValueError, match="OUTPUT_MISSING"):
        export_deliverable_dataset(
            dataset=dataset,
            run_status="SUCCEEDED",
            destination=tmp_path / "export",
        )


def test_export_does_not_overwrite_existing_destination(tmp_path: Path) -> None:
    dataset, _ = _dataset(tmp_path)
    destination = tmp_path / "export"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")

    with pytest.raises(FileExistsError):
        export_deliverable_dataset(
            dataset=dataset,
            run_status="SUCCEEDED",
            destination=destination,
        )

    assert marker.read_text(encoding="utf-8") == "unchanged"
