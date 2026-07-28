from __future__ import annotations

from dataagent.domain.runs import DatasetAsset, DatasetVersion
from dataagent.domain.specs import (
    AcceptanceSpec,
    ConstraintContract,
    DataSourceSpec,
    TaskCapabilitySpec,
    TaskSpecVersion,
)
from dataagent.evaluation import QualityEvaluator
from dataagent.infrastructure import DomainVersionStore, SqliteDatabase


def test_quality_evaluator_rejects_kept_asset_that_breaks_frozen_rule(tmp_path) -> None:
    store = DomainVersionStore(SqliteDatabase(tmp_path / "control.db"))
    spec = TaskSpecVersion(
        id="spec_1",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="keep wide images",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(tmp_path)),),
        hard_constraints={"min_width": 200},
        confirmed=True,
    )
    dataset = DatasetVersion(
        id="dataset_1",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=spec.id,
        run_id="run_1",
        source_roots=(str(tmp_path),),
        manifest_uri=str(tmp_path / "manifest.json"),
        assets=(
            DatasetAsset(
                source_uri=str(tmp_path / "small.png"),
                source_sha256="a" * 64,
                output_uri=str(tmp_path / "output.png"),
                output_sha256="b" * 64,
                decision="keep",
                metrics={"decode_ok": True, "width": 100, "height": 100, "format": "png"},
            ),
        ),
        source_count=1,
        kept_count=1,
        rejected_count=0,
        failed_count=0,
        original_files_unchanged=True,
    )

    report = QualityEvaluator(store).evaluate(
        dataset=dataset, spec=spec, owner_id="user_1"
    )

    assert report.status == "FAILED"
    assert report.metrics["hard_rule_violation_rate"] == 1
    assert report.reason_codes == ("HARD_RULE_VIOLATION_RATE_EXCEEDED",)
    assert report.semantic_quality_verified is False


def test_quality_evaluator_fails_when_required_vlm_evidence_is_missing(
    tmp_path,
) -> None:
    store = DomainVersionStore(SqliteDatabase(tmp_path / "control.db"))
    spec = TaskSpecVersion(
        id="spec_semantic",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="classify cats and dogs",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(tmp_path)),),
        capability_requirements=(
            TaskCapabilitySpec(
                id="classify",
                capability="image_classification",
                description="classify cats and dogs",
            ),
        ),
        confirmed=True,
    )
    dataset = DatasetVersion(
        id="dataset_semantic",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=spec.id,
        run_id="run_1",
        source_roots=(str(tmp_path),),
        manifest_uri=str(tmp_path / "manifest.json"),
        assets=(
            DatasetAsset(
                source_uri=str(tmp_path / "cat.png"),
                source_sha256="a" * 64,
                output_uri=str(tmp_path / "output.png"),
                output_sha256="b" * 64,
                decision="keep",
                labels={
                    "resolved_class": "unknown",
                    "datajuicer_output": {},
                },
            ),
        ),
        source_count=1,
        kept_count=1,
        rejected_count=0,
        failed_count=0,
        original_files_unchanged=True,
    )

    report = QualityEvaluator(store).evaluate(
        dataset=dataset, spec=spec, owner_id="user_1"
    )

    assert report.status == "FAILED"
    assert report.metrics["semantic_output_missing_rate"] == 1
    assert "REQUIRED_SEMANTIC_OUTPUT_MISSING" in report.reason_codes


def test_quality_evaluator_verifies_visual_selection_contract(tmp_path) -> None:
    store = DomainVersionStore(SqliteDatabase(tmp_path / "control.db"))
    spec = TaskSpecVersion(
        id="spec_visual_selection",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="keep people wearing black clothing",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(tmp_path)),),
        capability_requirements=(
            TaskCapabilitySpec(
                id="visual_selection",
                capability="visual_semantic_selection",
                description="select black clothing",
            ),
        ),
        confirmed=True,
    )
    matched = DatasetAsset(
        source_uri=str(tmp_path / "matched.png"),
        source_sha256="a" * 64,
        output_uri=str(tmp_path / "output.png"),
        output_sha256="b" * 64,
        decision="keep",
        labels={
            "visual_semantic_selection": "match",
            "datajuicer_output": {"visual_tags": ["semantic_match"]},
        },
    )
    dataset = DatasetVersion(
        id="dataset_visual_selection",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=spec.id,
        run_id="run_1",
        source_roots=(str(tmp_path),),
        manifest_uri=str(tmp_path / "manifest.json"),
        assets=(matched,),
        source_count=1,
        kept_count=1,
        rejected_count=0,
        failed_count=0,
        original_files_unchanged=True,
    )

    report = QualityEvaluator(store).evaluate(
        dataset=dataset,
        spec=spec,
        owner_id="user_1",
    )

    assert report.status == "PASSED"
    assert report.semantic_quality_verified is True
    assert report.metrics["semantic_selection_match_rate"] == 1
    assert report.metrics["semantic_selection_mismatch_rate"] == 0


def test_quality_evaluator_rejects_missing_visual_selection_contract(
    tmp_path,
) -> None:
    store = DomainVersionStore(SqliteDatabase(tmp_path / "control.db"))
    spec = TaskSpecVersion(
        id="spec_visual_selection_missing",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="keep people wearing black clothing",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(tmp_path)),),
        capability_requirements=(
            TaskCapabilitySpec(
                id="visual_selection",
                capability="visual_semantic_selection",
                description="select black clothing",
            ),
        ),
        confirmed=True,
    )
    dataset = DatasetVersion(
        id="dataset_visual_selection_missing",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=spec.id,
        run_id="run_1",
        source_roots=(str(tmp_path),),
        manifest_uri=str(tmp_path / "manifest.json"),
        assets=(
            DatasetAsset(
                source_uri=str(tmp_path / "missing.png"),
                source_sha256="a" * 64,
                output_uri=str(tmp_path / "output.png"),
                output_sha256="b" * 64,
                decision="keep",
                labels={"datajuicer_output": {}},
            ),
        ),
        source_count=1,
        kept_count=1,
        rejected_count=0,
        failed_count=0,
        original_files_unchanged=True,
    )

    report = QualityEvaluator(store).evaluate(
        dataset=dataset,
        spec=spec,
        owner_id="user_1",
    )

    assert report.status == "FAILED"
    assert report.semantic_quality_verified is False
    assert "REQUIRED_SEMANTIC_OUTPUT_MISSING" in report.reason_codes


def test_quality_evaluator_fails_when_kept_asset_lacks_constraint_evidence(
    tmp_path,
) -> None:
    store = DomainVersionStore(SqliteDatabase(tmp_path / "control.db"))
    spec = TaskSpecVersion(
        id="spec_face_count",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="keep images with fewer than two faces",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(tmp_path)),),
        constraints=(
            ConstraintContract(
                id="C07",
                source_text="人脸数量少于2个",
                scope="asset",
                field="face_count",
                operator="lt",
                value=2,
                unit="count",
                required_evidence_type="detected_face_count",
            ),
        ),
        confirmed=True,
    )
    dataset = DatasetVersion(
        id="dataset_missing_face_evidence",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=spec.id,
        run_id="run_1",
        source_roots=(str(tmp_path),),
        manifest_uri=str(tmp_path / "manifest.json"),
        assets=(
            DatasetAsset(
                source_uri=str(tmp_path / "unknown.png"),
                source_sha256="a" * 64,
                output_uri=str(tmp_path / "output.png"),
                output_sha256="b" * 64,
                decision="keep",
                metrics={"decode_ok": True, "width": 100, "height": 100},
            ),
        ),
        source_count=1,
        kept_count=1,
        rejected_count=0,
        failed_count=0,
        original_files_unchanged=True,
    )

    report = QualityEvaluator(store).evaluate(
        dataset=dataset, spec=spec, owner_id="user_1"
    )

    assert report.status == "FAILED"
    assert "REQUIRED_CONSTRAINT_EVIDENCE_MISSING" in report.reason_codes
    assert report.failed_asset_uris == (str(tmp_path / "unknown.png"),)


def test_missing_constraint_evidence_is_absolute_not_rate_based(tmp_path) -> None:
    store = DomainVersionStore(SqliteDatabase(tmp_path / "control.db"))
    spec = TaskSpecVersion(
        id="spec_face_count_absolute",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="keep images with fewer than two faces",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(tmp_path)),),
        constraints=(
            ConstraintContract(
                id="C07",
                source_text="人脸数量少于2个",
                scope="asset",
                field="face_count",
                operator="lt",
                value=2,
                unit="count",
                required_evidence_type="detected_face_count",
            ),
        ),
        acceptance=AcceptanceSpec(hard_rule_violation_rate=0.1),
        confirmed=True,
    )
    assets = tuple(
        DatasetAsset(
            source_uri=str(tmp_path / f"asset-{index}.png"),
            source_sha256=f"{index:064x}",
            output_uri=str(tmp_path / f"output-{index}.png"),
            output_sha256=f"{index + 100:064x}",
            decision="keep",
            metrics={
                "decode_ok": True,
                "width": 100,
                "height": 100,
                **({"face_count": 1} if index else {}),
            },
        )
        for index in range(21)
    )
    dataset = DatasetVersion(
        id="dataset_low_missing_evidence_rate",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=spec.id,
        run_id="run_1",
        source_roots=(str(tmp_path),),
        manifest_uri=str(tmp_path / "manifest.json"),
        assets=assets,
        source_count=len(assets),
        kept_count=len(assets),
        rejected_count=0,
        failed_count=0,
        original_files_unchanged=True,
    )

    report = QualityEvaluator(store).evaluate(
        dataset=dataset, spec=spec, owner_id="user_1"
    )

    assert (
        report.metrics["hard_rule_violation_rate"]
        < spec.acceptance.hard_rule_violation_rate
    )
    assert report.status == "FAILED"
    assert "REQUIRED_CONSTRAINT_EVIDENCE_MISSING" in report.reason_codes


def test_dataset_scope_exact_duplicate_constraint_blocks_qc(tmp_path) -> None:
    store = DomainVersionStore(SqliteDatabase(tmp_path / "control.db"))
    spec = TaskSpecVersion(
        id="spec_dataset_dedup",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="remove duplicate images",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(tmp_path)),),
        constraints=(
            ConstraintContract(
                id="C09",
                source_text="去除完全重复图片",
                scope="dataset",
                field="exact_duplicate_count",
                operator="eq",
                value=0,
                unit="count",
                required_evidence_type="exact_hash_duplicate_groups",
            ),
        ),
        acceptance=AcceptanceSpec(hard_rule_violation_rate=1),
        confirmed=True,
    )
    assets = tuple(
        DatasetAsset(
            source_uri=str(tmp_path / f"duplicate-{index}.png"),
            source_sha256="a" * 64,
            output_uri=str(tmp_path / f"output-{index}.png"),
            output_sha256="a" * 64,
            decision="keep",
            metrics={"decode_ok": True, "width": 100, "height": 100},
        )
        for index in range(2)
    )
    dataset = DatasetVersion(
        id="dataset_with_duplicate",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=spec.id,
        run_id="run_1",
        source_roots=(str(tmp_path),),
        manifest_uri=str(tmp_path / "manifest.json"),
        assets=assets,
        source_count=2,
        kept_count=2,
        rejected_count=0,
        failed_count=0,
        original_files_unchanged=True,
    )

    report = QualityEvaluator(store).evaluate(
        dataset=dataset, spec=spec, owner_id="user_1"
    )

    assert report.status == "FAILED"
    assert "DATASET_CONSTRAINT_VIOLATION" in report.reason_codes
    assert report.metrics["dataset_constraint_failure_count"] == 1


def test_quality_evaluator_blocks_unresolved_visual_review(tmp_path) -> None:
    store = DomainVersionStore(SqliteDatabase(tmp_path / "control.db"))
    spec = TaskSpecVersion(
        id="spec_visual_review",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="keep black clothing",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(tmp_path)),),
        capability_requirements=(
            TaskCapabilitySpec(
                id="visual_selection",
                capability="visual_semantic_selection",
                description="select black clothing",
            ),
        ),
        confirmed=True,
    )
    dataset = DatasetVersion(
        id="dataset_visual_review",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=spec.id,
        run_id="run_1",
        source_roots=(str(tmp_path),),
        manifest_uri=str(tmp_path / "manifest.json"),
        assets=(
            DatasetAsset(
                source_uri=str(tmp_path / "uncertain.png"),
                source_sha256="a" * 64,
                output_uri=str(tmp_path / "output.png"),
                output_sha256="b" * 64,
                decision="keep",
                labels={
                    "visual_semantic_selection": "uncertain",
                    "visual_semantic_review_required": True,
                    "datajuicer_output": {
                        "visual_tags": ["semantic_uncertain"]
                    },
                },
            ),
        ),
        source_count=1,
        kept_count=1,
        rejected_count=0,
        failed_count=0,
        original_files_unchanged=True,
    )

    report = QualityEvaluator(store).evaluate(
        dataset=dataset, spec=spec, owner_id="user_1"
    )

    assert report.status == "FAILED"
    assert "UNRESOLVED_SEMANTIC_REVIEW" in report.reason_codes
