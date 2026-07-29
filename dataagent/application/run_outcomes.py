from __future__ import annotations

from typing import Any

from pydantic import Field

from ..domain.common.models import DomainModel
from ..domain.evaluations import QCReport
from ..domain.runs import RunSnapshot, RunStatus


_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCEEDED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)


class RunOutcomeObservation(DomainModel):
    """Immutable facts exposed to the Requirement Agent after a formal Run."""

    observation_id: str
    work_order_id: str
    run_id: str
    run_status: RunStatus
    pipeline_version_id: str
    task_spec_version_id: str
    dataset_version_id: str | None = None
    qc_report_id: str | None = None
    qc_status: str | None = None
    reason_codes: tuple[str, ...] = ()
    metrics: dict[str, float] = Field(default_factory=dict)
    failed_asset_uris: tuple[str, ...] = ()
    repair_candidate_uris: tuple[str, ...] = ()
    retryable: bool
    error: str | None = None
    evidence_refs: tuple[str, ...] = ()
    observed_at: str


class RunOutcomeObserver:
    """Project Run and QC stores into one model-readable observation."""

    def observe(
        self,
        *,
        run: RunSnapshot,
        qc_report: QCReport | None,
        repair_candidate_uris: tuple[str, ...] = (),
    ) -> RunOutcomeObservation:
        if run.status not in _TERMINAL_RUN_STATUSES:
            raise ValueError(
                f"Run outcome is not terminal: {run.status}"
            )
        if qc_report is not None and qc_report.run_id != run.id:
            raise ValueError("QCReport does not belong to the observed Run")
        reason_codes = qc_report.reason_codes if qc_report is not None else ()
        failed_asset_uris = (
            qc_report.failed_asset_uris if qc_report is not None else ()
        )
        evidence_refs = [f"run:{run.id}"]
        if run.dataset_version_id:
            evidence_refs.append(f"dataset:{run.dataset_version_id}")
        if qc_report is not None:
            evidence_refs.append(f"qc_report:{qc_report.id}")
        return RunOutcomeObservation(
            observation_id=f"run_outcome:{run.id}",
            work_order_id=run.work_order_id,
            run_id=run.id,
            run_status=run.status,
            pipeline_version_id=run.pipeline_version_id,
            task_spec_version_id=run.task_spec_version_id,
            dataset_version_id=run.dataset_version_id,
            qc_report_id=qc_report.id if qc_report is not None else None,
            qc_status=(
                str(qc_report.status) if qc_report is not None else None
            ),
            reason_codes=reason_codes,
            metrics=qc_report.metrics if qc_report is not None else {},
            failed_asset_uris=failed_asset_uris,
            repair_candidate_uris=repair_candidate_uris,
            retryable=bool(repair_candidate_uris) and run.repair_attempt < 3,
            error=run.error,
            evidence_refs=tuple(evidence_refs),
            observed_at=run.updated_at.isoformat(),
        )


def latest_qc_report_for_run(
    reports: list[dict[str, Any]],
    run_id: str,
) -> QCReport | None:
    matching = [
        QCReport.model_validate(item)
        for item in reports
        if item.get("run_id") == run_id
    ]
    return matching[-1] if matching else None


__all__ = [
    "RunOutcomeObservation",
    "RunOutcomeObserver",
    "latest_qc_report_for_run",
]
