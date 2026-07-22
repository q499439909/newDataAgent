from __future__ import annotations

from typing import Any

from .domain.common import new_id
from .domain.evaluations import QCReport, QCStatus
from .domain.experiences import (
    ExperienceStatus,
    PipelineExperience,
    RunFeedback,
    TaskSignature,
)
from .domain.pipelines import PipelineVersion
from .domain.specs import TaskSpecVersion
from .infrastructure import DomainVersionStore, RunStore


_TERMINAL_RUN_STATUSES = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}


def task_signature(spec: TaskSpecVersion) -> TaskSignature:
    capabilities = tuple(item.capability for item in spec.capability_requirements)
    classification_mode = spec.classification.mode if spec.classification else None
    label_ids = (
        tuple(item.id for item in spec.classification.labels)
        if spec.classification
        else ()
    )
    summary_parts = [
        "modality:image",
        "capabilities:" + ",".join(sorted(capabilities)),
        "actions:" + ",".join(sorted(spec.output_actions)),
    ]
    if classification_mode:
        summary_parts.append(f"classification:{classification_mode}")
        summary_parts.append(f"label_count:{len(label_ids)}")
    return TaskSignature(
        capabilities=capabilities,
        output_actions=spec.output_actions,
        classification_mode=classification_mode,
        label_ids=label_ids,
        normalized_summary="; ".join(summary_parts),
    )


class PipelineExperienceService:
    def __init__(
        self,
        version_store: DomainVersionStore,
        run_store: RunStore,
    ) -> None:
        self.version_store = version_store
        self.run_store = run_store

    def record_feedback(
        self,
        *,
        owner_id: str,
        run_id: str,
        accepted: bool,
        reusable: bool = False,
        rating: int | None = None,
        comment: str = "",
    ) -> tuple[RunFeedback, PipelineExperience]:
        run = self.run_store.get(run_id, owner_id)
        if run["status"] not in _TERMINAL_RUN_STATUSES:
            raise ValueError("Run feedback requires a terminal Run")
        spec = TaskSpecVersion.model_validate(
            self.version_store.get(
                kind="task_spec",
                entity_id=run["task_spec_version_id"],
                owner_id=owner_id,
            )
        )
        pipeline = PipelineVersion.model_validate(
            self.version_store.get(
                kind="pipeline",
                entity_id=run["pipeline_version_id"],
                owner_id=owner_id,
            )
        )
        reports = [
            QCReport.model_validate(item)
            for item in self.version_store.list_for_owner(
                kind="qc_report", owner_id=owner_id
            )
            if item.get("run_id") == run_id
        ]
        report = reports[-1] if reports else None
        prior_feedback = [
            RunFeedback.model_validate(item)
            for item in self.version_store.list_for_owner(
                kind="run_feedback", owner_id=owner_id
            )
            if item.get("run_id") == run_id
        ]
        feedback = RunFeedback(
            id=new_id("run_feedback"),
            version=len(prior_feedback) + 1,
            parent_version_id=prior_feedback[-1].id if prior_feedback else None,
            created_by=owner_id,
            change_reason="explicit user feedback for dataset run",
            run_id=run_id,
            work_order_id=run["work_order_id"],
            rating=rating,
            accepted=accepted,
            reusable=reusable,
            comment=comment.strip(),
        )
        status = self._experience_status(run, report, feedback)
        prior_experiences = [
            PipelineExperience.model_validate(item)
            for item in self.version_store.list_for_owner(
                kind="pipeline_experience", owner_id=owner_id
            )
            if item.get("run_id") == run_id
        ]
        experience = PipelineExperience(
            id=new_id("pipeline_experience"),
            version=len(prior_experiences) + 1,
            parent_version_id=prior_experiences[-1].id if prior_experiences else None,
            created_by=owner_id,
            change_reason="indexed terminal run with explicit user feedback",
            task_spec_version_id=spec.id,
            pipeline_version_id=pipeline.id,
            run_id=run_id,
            dataset_version_id=run.get("dataset_version_id"),
            qc_report_id=report.id if report else None,
            feedback_id=feedback.id,
            strategy=pipeline.strategy.value,
            task_signature=task_signature(spec),
            run_status=run["status"],
            qc_status=report.status.value if report else None,
            outcome_metrics=self._outcome_metrics(run, report),
            rating=rating,
            accepted=accepted,
            reusable=reusable,
            status=status,
            compatibility={
                "operator_version_ids": [
                    node.operator_version_id for node in pipeline.nodes
                ]
            },
        )
        self.version_store.save_if_absent(
            kind="run_feedback",
            owner_id=owner_id,
            payload=feedback.model_dump(mode="json"),
        )
        self.version_store.save_if_absent(
            kind="pipeline_experience",
            owner_id=owner_id,
            payload=experience.model_dump(mode="json"),
        )
        return feedback, experience

    @staticmethod
    def _experience_status(
        run: dict[str, Any],
        report: QCReport | None,
        feedback: RunFeedback,
    ) -> ExperienceStatus:
        if not feedback.accepted:
            return ExperienceStatus.REJECTED
        qc_passed = report is None or report.status == QCStatus.PASSED
        if (
            feedback.reusable
            and run["status"] in {"SUCCEEDED", "PARTIAL"}
            and qc_passed
        ):
            return ExperienceStatus.RECOMMENDED
        return ExperienceStatus.CANDIDATE

    @staticmethod
    def _outcome_metrics(
        run: dict[str, Any], report: QCReport | None
    ) -> dict[str, float]:
        if report is not None:
            return dict(report.metrics)
        total = max(int(run.get("total", 0)), 1)
        return {
            "retention_rate": float(run.get("kept", 0)) / total,
            "execution_failure_rate": float(run.get("failed", 0)) / total,
        }


__all__ = ["PipelineExperienceService", "task_signature"]
