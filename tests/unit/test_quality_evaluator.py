from __future__ import annotations

from dataagent.domain.runs import DatasetAsset, DatasetVersion
from dataagent.domain.specs import DataSourceSpec, TaskSpecVersion
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
