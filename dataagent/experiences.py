from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
        "objective:" + " ".join(spec.objective.lower().split()),
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


class PipelineExperienceMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experience_id: str
    pipeline_version_id: str
    strategy: str
    score: float = Field(ge=0, le=100)
    structural_score: float = Field(ge=0, le=1)
    semantic_score: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=1)
    reasons: tuple[str, ...]


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _semantic_terms(value: str) -> set[str]:
    normalized = " ".join(value.lower().split())
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    compact = "".join(character for character in normalized if not character.isspace())
    trigrams = {
        compact[index : index + 3]
        for index in range(max(0, len(compact) - 2))
    }
    return words | trigrams


class PipelineExperienceRetriever:
    def __init__(self, version_store: DomainVersionStore) -> None:
        self.version_store = version_store

    def search(
        self,
        spec: TaskSpecVersion,
        *,
        owner_id: str,
        available_operator_ids: set[str] | None = None,
        include_candidates: bool = False,
        limit: int = 5,
    ) -> tuple[PipelineExperienceMatch, ...]:
        current = task_signature(spec)
        latest: dict[str, PipelineExperience] = {}
        for payload in self.version_store.list_for_owner(
            kind="pipeline_experience", owner_id=owner_id
        ):
            experience = PipelineExperience.model_validate(payload)
            previous = latest.get(experience.run_id)
            if previous is None or experience.version > previous.version:
                latest[experience.run_id] = experience

        matches: list[PipelineExperienceMatch] = []
        for experience in latest.values():
            if experience.status == ExperienceStatus.REJECTED:
                continue
            if (
                experience.status != ExperienceStatus.RECOMMENDED
                and not include_candidates
            ):
                continue
            historical = experience.task_signature
            current_capabilities = set(current.capabilities)
            historical_capabilities = set(historical.capabilities)
            if not current_capabilities.issubset(historical_capabilities):
                continue
            if current.modality != historical.modality:
                continue
            if (
                current.classification_mode
                and current.classification_mode != historical.classification_mode
            ):
                continue
            historical_operators = set(
                experience.compatibility.get("operator_version_ids", ())
            )
            if (
                available_operator_ids is not None
                and not historical_operators.issubset(available_operator_ids)
            ):
                continue

            capability_score = _jaccard(
                current_capabilities, historical_capabilities
            )
            action_score = _jaccard(
                set(current.output_actions), set(historical.output_actions)
            )
            classification_score = 1.0
            if current.classification_mode:
                classification_score = (
                    1.0
                    if len(current.label_ids) == len(historical.label_ids)
                    else 0.5
                )
            structural = (
                capability_score * 0.55
                + action_score * 0.3
                + classification_score * 0.15
            )
            semantic = _jaccard(
                _semantic_terms(current.normalized_summary),
                _semantic_terms(historical.normalized_summary),
            )
            qc_quality = 1.0 - float(
                experience.outcome_metrics.get("execution_failure_rate", 0.0)
            )
            retention = float(
                experience.outcome_metrics.get("retention_rate", 0.5)
            )
            rating = (experience.rating or 3) / 5
            quality = max(0.0, min(1.0, qc_quality * 0.5 + retention * 0.2 + rating * 0.3))
            score = round(structural * 60 + semantic * 15 + quality * 25, 4)
            matches.append(
                PipelineExperienceMatch(
                    experience_id=experience.id,
                    pipeline_version_id=experience.pipeline_version_id,
                    strategy=experience.strategy,
                    score=score,
                    structural_score=round(structural, 6),
                    semantic_score=round(semantic, 6),
                    quality_score=round(quality, 6),
                    reasons=(
                        f"capability_overlap:{capability_score:.3f}",
                        f"action_overlap:{action_score:.3f}",
                        f"classification_shape:{classification_score:.3f}",
                        f"user_rating:{experience.rating or 0}",
                    ),
                )
            )
        return tuple(
            sorted(
                matches,
                key=lambda item: (-item.score, item.experience_id),
            )[:limit]
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


__all__ = [
    "PipelineExperienceMatch",
    "PipelineExperienceRetriever",
    "PipelineExperienceService",
    "task_signature",
]
