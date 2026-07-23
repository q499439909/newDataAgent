from __future__ import annotations

from ..domain.evaluations import QCReport, QCStatus
from ..domain.runs import DatasetAsset, DatasetVersion
from ..domain.specs import TaskSpecVersion
from ..infrastructure import DomainVersionStore


_SEMANTIC_CAPABILITIES = frozenset(
    {
        "authenticity_assessment",
        "image_classification",
        "visual_semantic_selection",
    }
)


def _has_semantic_value(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_semantic_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_semantic_value(item) for item in value)
    return False


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
        required_capabilities = {
            *spec.required_capabilities,
            *(
                item.capability
                for item in spec.capability_requirements
                if item.required
            ),
        }
        semantic_missing = [
            asset
            for asset in kept
            if self._missing_required_semantics(asset, required_capabilities)
        ]
        source_count = max(1, dataset.source_count)
        checked_count = max(1, len(kept))
        violation_rate = len(hard_violations) / checked_count
        failure_rate = dataset.failed_count / source_count
        semantic_missing_rate = len(semantic_missing) / checked_count
        retention_rate = dataset.kept_count / source_count
        semantic_capabilities = required_capabilities.intersection(
            _SEMANTIC_CAPABILITIES
        )
        semantic_quality_verified = bool(semantic_capabilities) and not (
            semantic_missing or failed_assets
        )
        selection_counts = {
            value: sum(
                asset.labels.get("visual_semantic_selection") == value
                for asset in dataset.assets
            )
            for value in ("match", "mismatch", "uncertain")
        }
        selection_total = sum(selection_counts.values())
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
        if semantic_missing:
            reasons.append("REQUIRED_SEMANTIC_OUTPUT_MISSING")
            recommendations.append(
                "Inspect the model-operator response contract and retry before publication"
            )
        passed = (
            bool(kept)
            and violation_rate <= threshold
            and dataset.failed_count == 0
            and not semantic_missing
        )
        metrics = {
            "hard_rule_violation_rate": round(violation_rate, 6),
            "retention_rate": round(retention_rate, 6),
            "execution_failure_rate": round(failure_rate, 6),
            "semantic_output_missing_rate": round(semantic_missing_rate, 6),
        }
        if selection_total:
            metrics.update(
                {
                    f"semantic_selection_{value}_rate": round(
                        count / selection_total,
                        6,
                    )
                    for value, count in selection_counts.items()
                }
            )
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
            semantic_quality_verified=semantic_quality_verified,
            metrics=metrics,
            failed_asset_uris=tuple(
                dict.fromkeys(
                    asset.source_uri
                    for asset in (*hard_violations, *failed_assets, *semantic_missing)
                )
            ),
            reason_codes=tuple(reasons),
            recommendations=tuple(recommendations),
        )
        self.version_store.save_if_absent(
            kind="qc_report", owner_id=owner_id, payload=report.model_dump(mode="json")
        )
        return report

    @staticmethod
    def _missing_required_semantics(
        asset: DatasetAsset, required_capabilities: set[str]
    ) -> bool:
        labels = asset.labels
        provider_output = labels.get("datajuicer_output", {})
        if "image_classification" in required_capabilities:
            classification_evidence = (
                provider_output.get("image_tags")
                if isinstance(provider_output, dict)
                else None
            )
            if not _has_semantic_value(classification_evidence):
                return True
        if "authenticity_assessment" in required_capabilities:
            authenticity_evidence = (
                provider_output.get("authenticity_tags")
                if isinstance(provider_output, dict)
                else None
            )
            if not _has_semantic_value(authenticity_evidence):
                return True
        if "visual_semantic_selection" in required_capabilities:
            selection = labels.get("visual_semantic_selection")
            provider_evidence = (
                provider_output.get("visual_tags")
                if isinstance(provider_output, dict)
                else None
            )
            if selection not in {"match", "mismatch", "uncertain"}:
                return True
            if not _has_semantic_value(provider_evidence):
                return True
        return False

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
