from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dataagent.application.dataset_versions import (
    build_logical_dataset_version,
    validate_dataset_references,
    write_dataset_manifest,
)
from dataagent.domain.runs import (
    AssetMaterialization,
    AssetOrigin,
    DatasetAsset,
    DatasetVersionKind,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _asset(
    source: Path,
    *,
    decision: str,
    output: Path | None = None,
) -> DatasetAsset:
    return DatasetAsset(
        source_uri=str(source),
        source_sha256=_sha256(source),
        output_uri=str(output) if output else None,
        output_sha256=_sha256(output) if output else None,
        decision=decision,
        reason_codes=("OPERATOR_ERROR:TimeoutError",) if decision == "failed" else (),
    )


def test_build_initial_logical_dataset_preserves_complete_manifest(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    output_root = tmp_path / "dataset"
    source_root.mkdir()
    output_root.mkdir()
    kept_source = source_root / "kept.png"
    rejected_source = source_root / "rejected.png"
    failed_source = source_root / "failed.png"
    for index, path in enumerate((kept_source, rejected_source, failed_source)):
        path.write_bytes(f"asset-{index}".encode())
    kept_output = output_root / "kept.png"
    kept_output.write_bytes(kept_source.read_bytes())

    dataset = build_logical_dataset_version(
        dataset_id="dataset_1",
        owner_id="owner_1",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="task_spec_1",
        run_id="run_1",
        source_roots=(str(source_root),),
        manifest_uri=str(output_root / "manifest.json"),
        assets=(
            _asset(kept_source, decision="keep", output=kept_output),
            _asset(rejected_source, decision="reject"),
            _asset(failed_source, decision="failed"),
        ),
    )

    assert dataset.version_kind == DatasetVersionKind.LOGICAL
    assert dataset.deliverable is False
    assert dataset.source_count == 3
    assert dataset.kept_count == 1
    assert dataset.rejected_count == 1
    assert dataset.failed_count == 1
    assert [item.source_uri for item in dataset.still_failed] == [str(failed_source)]
    assert all(item.asset_origin == AssetOrigin.RUN_OUTPUT for item in dataset.assets)
    assert all(item.origin_run_id == "run_1" for item in dataset.assets)
    assert dataset.assets[0].materialization == AssetMaterialization.LOCAL_FILE
    assert dataset.assets[1].materialization == AssetMaterialization.NOT_MATERIALIZED
    assert dataset.assets[2].audit_refs == ("run:run_1:asset:2",)


def test_logical_dataset_can_reference_parent_asset_without_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    parent_output = tmp_path / "parent" / "source.png"
    source.write_bytes(b"source")
    parent_output.parent.mkdir()
    parent_output.write_bytes(b"published")
    asset = DatasetAsset(
        source_uri=str(source),
        source_sha256=_sha256(source),
        output_uri=str(parent_output),
        output_sha256=_sha256(parent_output),
        decision="keep",
        asset_origin=AssetOrigin.PARENT_DATASET_VERSION,
        origin_dataset_version_id="dataset_parent",
        origin_run_id="run_parent",
        materialization=AssetMaterialization.REFERENCED_FILE,
        audit_refs=("dataset:dataset_parent:asset:0",),
    )

    dataset = build_logical_dataset_version(
        dataset_id="dataset_repaired",
        owner_id="owner_1",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="task_spec_1",
        run_id="run_repair",
        source_roots=(str(tmp_path),),
        manifest_uri=str(tmp_path / "manifest.json"),
        assets=(asset,),
        parent_dataset_version_id="dataset_parent",
        repair_run_ids=("run_repair",),
    )

    assert dataset.assets[0].output_uri == str(parent_output)
    assert dataset.assets[0].materialization == AssetMaterialization.REFERENCED_FILE
    assert validate_dataset_references(dataset) == ()


def test_missing_or_changed_parent_reference_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    parent_output = tmp_path / "parent.png"
    source.write_bytes(b"source")
    parent_output.write_bytes(b"published")
    asset = DatasetAsset(
        source_uri=str(source),
        source_sha256=_sha256(source),
        output_uri=str(parent_output),
        output_sha256=_sha256(parent_output),
        decision="keep",
        asset_origin=AssetOrigin.PARENT_DATASET_VERSION,
        origin_dataset_version_id="dataset_parent",
        materialization=AssetMaterialization.REFERENCED_FILE,
    )
    dataset = build_logical_dataset_version(
        dataset_id="dataset_repaired",
        owner_id="owner_1",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="task_spec_1",
        run_id="run_repair",
        source_roots=(str(tmp_path),),
        manifest_uri=str(tmp_path / "manifest.json"),
        assets=(asset,),
        parent_dataset_version_id="dataset_parent",
    )

    parent_output.write_bytes(b"changed")
    issues = validate_dataset_references(dataset)
    assert [(item.code, item.source_uri) for item in issues] == [
        ("OUTPUT_HASH_MISMATCH", str(source))
    ]
    parent_output.unlink()
    issues = validate_dataset_references(dataset)
    assert [item.code for item in issues] == ["OUTPUT_MISSING"]


def test_manifest_write_is_atomic_and_round_trips(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "output.png"
    source.write_bytes(b"source")
    output.write_bytes(b"output")
    manifest = tmp_path / "nested" / "manifest.json"
    dataset = build_logical_dataset_version(
        dataset_id="dataset_1",
        owner_id="owner_1",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="task_spec_1",
        run_id="run_1",
        source_roots=(str(tmp_path),),
        manifest_uri=str(manifest),
        assets=(_asset(source, decision="keep", output=output),),
    )

    written = write_dataset_manifest(dataset)

    assert written == manifest.resolve()
    assert json.loads(manifest.read_text(encoding="utf-8"))["id"] == "dataset_1"
    assert not list(manifest.parent.glob("*.tmp"))
