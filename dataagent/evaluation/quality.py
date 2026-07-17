from __future__ import annotations

from ..domain.evaluations import QCReport, QCStatus
from ..domain.runs import DatasetAsset, DatasetVersion
from ..domain.specs import TaskSpecVersion
from ..infrastructure import DomainVersionStore


class QualityEvaluator:
    VERSION = "builtin.quality_evaluator:1"

    def __init__(self, version_store: DomainVersionStore) -> None:
        self.version_store = version_store

    def evaluate(
        self,
        *,
        dataset: DatasetVersion,
        spec: TaskSpecVersion,
        owner_id: str,
    ) -> QCReport:
        existing = [
            QCReport.model_validate(item)
            for item in self.version_store.list_for_owner(kind="qc_report", owner_id=owner_id)
            if item.get("dataset_version_id") == dataset.id
        ]
        if existing:
            return existing[-1]
        hard_violations = [
            asset
            for asset in dataset.assets
            if asset.decision == "keep" and self._hard_rule_failures(asset, spec)
        ]
        failed_assets = [asset for asset in dataset.assets if asset.decision == "failed"]
        kept = [asset for asset in dataset.assets if asset.decision == "keep"]
        source_count = max(1, dataset.source_count)
        checked_count = max(1, len(kept))
        violation_rate = len(hard_violations) / checked_count
        failure_rate = dataset.failed_count / source_count
        retention_rate = dataset.kept_count / source_count
        threshold = spec.acceptance.hard_rule_violation_rate
        reasons: list[str] = []
        recommendations: list[str] = []
        if not kept:
            reasons.append("EMPTY_DATASET")
            recommendations.append("Relax the pipeline or inspect source quality before retrying")
        if violation_rate > threshold:
            reasons.append("HARD_RULE_VIOLATION_RATE_EXCEEDED")
            recommendations.append("Review failed assets and revise the responsible pipeline node")
        if dataset.failed_count:
            reasons.append("EXECUTION_FAILURES_PRESENT")
            recommendations.append("Retry failed assets after diagnosing operator errors")
        passed = bool(kept) and violation_rate <= threshold and dataset.failed_count == 0
        report = QCReport(
            id=f"qc_report_{dataset.id.removeprefix('dataset_')}",
            version=1,
            created_by="quality_evaluator",
            change_reason="automatic full hard-rule evaluation",
            work_order_id=dataset.work_order_id,
            dataset_version_id=dataset.id,
            pipeline_version_id=dataset.pipeline_version_id,
            task_spec_version_id=dataset.task_spec_version_id,
            run_id=dataset.run_id,
            evaluator_version=self.VERSION,
            status=QCStatus.PASSED if passed else QCStatus.FAILED,
            full_hard_rule_check=True,
            semantic_quality_verified=False,
            metrics={
                "hard_rule_violation_rate": round(violation_rate, 6),
                "retention_rate": round(retention_rate, 6),
                "execution_failure_rate": round(failure_rate, 6),
            },
            failed_asset_uris=tuple(
                asset.source_uri for asset in (*hard_violations, *failed_assets)
            ),
            reason_codes=tuple(reasons),
            recommendations=tuple(recommendations),
        )
        self.version_store.save_if_absent(
            kind="qc_report", owner_id=owner_id, payload=report.model_dump(mode="json")
        )
        return report

    @staticmethod
    def _hard_rule_failures(
        asset: DatasetAsset, spec: TaskSpecVersion
    ) -> tuple[str, ...]:
        metrics = asset.metrics
        constraints = spec.hard_constraints
        failures: list[str] = []
        if not metrics.get("decode_ok", True):
            failures.append("DECODE_FAILED")
        width = int(metrics.get("width", 0))
        height = int(metrics.get("height", 0))
        if minimum := constraints.get("min_width"):
            if width < int(minimum):
                failures.append("MIN_WIDTH")
        if minimum := constraints.get("min_height"):
            if height < int(minimum):
                failures.append("MIN_HEIGHT")
        if minimum := constraints.get("min_short_edge"):
            if min(width, height) < int(minimum):
                failures.append("MIN_SHORT_EDGE")
        if maximum := constraints.get("max_width"):
            if width > int(maximum):
                failures.append("MAX_WIDTH")
        if maximum := constraints.get("max_height"):
            if height > int(maximum):
                failures.append("MAX_HEIGHT")
        allowed = {str(item).lower().lstrip(".") for item in constraints.get("allowed_formats", ())}
        if allowed and str(metrics.get("format", "")).lower() not in allowed:
            failures.append("FORMAT_NOT_ALLOWED")
        ratio = width / height if height else 0
        if minimum := constraints.get("aspect_ratio_min"):
            if ratio < float(minimum):
                failures.append("ASPECT_RATIO_MIN")
        if maximum := constraints.get("aspect_ratio_max"):
            if ratio > float(maximum):
                failures.append("ASPECT_RATIO_MAX")
        return tuple(failures)
