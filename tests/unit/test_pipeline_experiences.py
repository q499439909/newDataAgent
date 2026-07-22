from __future__ import annotations

import pytest

from dataagent.domain.evaluations import QCReport, QCStatus
from dataagent.domain.pipelines import PipelineNode, PipelineStrategy, PipelineVersion
from dataagent.domain.specs import (
    ClassificationLabelSpec,
    ClassificationSpec,
    DataSourceSpec,
    TaskCapabilitySpec,
    TaskSpecVersion,
)
from dataagent.experiences import PipelineExperienceRetriever, PipelineExperienceService
from dataagent.infrastructure import DomainVersionStore, RunStore, SqliteDatabase


def _stores(tmp_path):
    database = SqliteDatabase(tmp_path / "dataagent.db")
    return DomainVersionStore(database), RunStore(database)


def _seed_success(version_store: DomainVersionStore, run_store: RunStore) -> None:
    spec = TaskSpecVersion(
        id="spec_1",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_1",
        objective="Classify pigs and dogs",
        data_sources=(DataSourceSpec(type="local_directory", uri="D:/images"),),
        output_actions=("classify", "partition", "manifest"),
        capability_requirements=(
            TaskCapabilitySpec(
                id="image_classification",
                capability="image_classification",
                description="Classify images",
            ),
        ),
        classification=ClassificationSpec(
            labels=(
                ClassificationLabelSpec(id="pig", display_name="Pig"),
                ClassificationLabelSpec(id="dog", display_name="Dog"),
            )
        ),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id="pipeline_1",
        family_id="pipeline_balanced",
        version=1,
        created_by="user_1",
        change_reason="test",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=spec.id,
        nodes=(
            PipelineNode(
                id="classification",
                operator_version_id="provider.classifier:1",
                name="Classifier",
                category="understanding",
            ),
        ),
        created_from="processing_agent",
        approved=True,
    )
    report = QCReport(
        id="qc_1",
        version=1,
        created_by="system",
        change_reason="test",
        work_order_id="work_1",
        dataset_version_id="dataset_1",
        pipeline_version_id=pipeline.id,
        task_spec_version_id=spec.id,
        run_id="run_1",
        evaluator_version="test:1",
        status=QCStatus.PASSED,
        full_hard_rule_check=True,
        metrics={
            "hard_rule_violation_rate": 0.0,
            "retention_rate": 0.9,
            "execution_failure_rate": 0.0,
        },
    )
    for kind, item in (("task_spec", spec), ("pipeline", pipeline), ("qc_report", report)):
        version_store.save_if_absent(
            kind=kind,
            owner_id="user_1",
            payload=item.model_dump(mode="json"),
        )
    run_store.create(
        run_id="run_1",
        work_order_id="work_1",
        owner_id="user_1",
        pipeline_version_id=pipeline.id,
        task_spec_version_id=spec.id,
        idempotency_key="request_1",
    )
    run_store.mark_succeeded("run_1", "dataset_1")


def test_satisfied_successful_run_becomes_recommended_experience(tmp_path) -> None:
    version_store, run_store = _stores(tmp_path)
    _seed_success(version_store, run_store)

    feedback, experience = PipelineExperienceService(
        version_store, run_store
    ).record_feedback(
        owner_id="user_1",
        run_id="run_1",
        accepted=True,
        reusable=True,
        rating=5,
        comment="Useful result",
    )

    assert feedback.rating == 5
    assert experience.status.value == "recommended"
    assert experience.task_signature.label_ids == ("pig", "dog")
    assert experience.outcome_metrics["retention_rate"] == 0.9
    assert len(
        version_store.list_for_owner(
            kind="pipeline_experience", owner_id="user_1"
        )
    ) == 1


def test_feedback_is_versioned_and_owner_scoped(tmp_path) -> None:
    version_store, run_store = _stores(tmp_path)
    _seed_success(version_store, run_store)
    service = PipelineExperienceService(version_store, run_store)

    first, _ = service.record_feedback(
        owner_id="user_1", run_id="run_1", accepted=False
    )
    second, experience = service.record_feedback(
        owner_id="user_1", run_id="run_1", accepted=True, reusable=False
    )

    assert second.version == 2
    assert second.parent_version_id == first.id
    assert experience.status.value == "candidate"
    with pytest.raises(PermissionError):
        service.record_feedback(
            owner_id="another_user", run_id="run_1", accepted=True
        )


def test_retrieval_matches_task_shape_and_requires_current_operators(tmp_path) -> None:
    version_store, run_store = _stores(tmp_path)
    _seed_success(version_store, run_store)
    PipelineExperienceService(version_store, run_store).record_feedback(
        owner_id="user_1",
        run_id="run_1",
        accepted=True,
        reusable=True,
        rating=5,
    )
    query = TaskSpecVersion(
        id="spec_query",
        version=1,
        created_by="user_1",
        change_reason="query",
        work_order_id="work_query",
        objective="Classify cats and dogs",
        data_sources=(DataSourceSpec(type="local_directory", uri="D:/new-images"),),
        output_actions=("classify", "partition", "manifest"),
        capability_requirements=(
            TaskCapabilitySpec(
                id="image_classification",
                capability="image_classification",
                description="Classify images",
            ),
        ),
        classification=ClassificationSpec(
            labels=(
                ClassificationLabelSpec(id="cat", display_name="Cat"),
                ClassificationLabelSpec(id="dog", display_name="Dog"),
            )
        ),
    )
    retriever = PipelineExperienceRetriever(version_store)

    matches = retriever.search(
        query,
        owner_id="user_1",
        available_operator_ids={"provider.classifier:1"},
    )
    blocked = retriever.search(
        query,
        owner_id="user_1",
        available_operator_ids=set(),
    )

    assert len(matches) == 1
    assert matches[0].structural_score == 1.0
    assert matches[0].score > 80
    assert blocked == ()
