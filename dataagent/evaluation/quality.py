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
        missing_constraint_evidence = [
            asset
            for asset in kept
            if any(
                code.startswith("MISSING_CONSTRAINT_EVIDENCE:")
                for code in self._hard_rule_failures(asset, spec)
            )
        ]
        dataset_constraint_failures = self._dataset_constraint_failures(
            dataset, spec
        )
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
        unresolved_semantic_review = [
            asset
            for asset in kept
            if asset.labels.get("visual_semantic_review_required") is True
            or asset.labels.get("visual_semantic_selection") == "uncertain"
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
            semantic_missing or unresolved_semantic_review or failed_assets
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
        if missing_constraint_evidence:
            reasons.append("REQUIRED_CONSTRAINT_EVIDENCE_MISSING")
            recommendations.append(
                "Produce the missing Constraint evidence before publication"
            )
        if dataset_constraint_failures:
            reasons.append("DATASET_CONSTRAINT_VIOLATION")
            recommendations.append(
                "Repair dataset-level duplicate or source-integrity constraints"
            )
        if dataset.failed_count:
            reasons.append("EXECUTION_FAILURES_PRESENT")
            recommendations.append("Retry failed assets after diagnosing operator errors")
        if semantic_missing:
            reasons.append("REQUIRED_SEMANTIC_OUTPUT_MISSING")
            recommendations.append(
                "Inspect the model-operator response contract and retry before publication"
            )
        if unresolved_semantic_review:
            reasons.append("UNRESOLVED_SEMANTIC_REVIEW")
            recommendations.append(
                "Resolve every semantic ReviewSet item before publication"
            )
        passed = (
            bool(kept)
            and violation_rate <= threshold
            and dataset.failed_count == 0
            and not missing_constraint_evidence
            and not dataset_constraint_failures
            and not semantic_missing
            and not unresolved_semantic_review
        )
        metrics = {
            "hard_rule_violation_rate": round(violation_rate, 6),
            "retention_rate": round(retention_rate, 6),
            "execution_failure_rate": round(failure_rate, 6),
            "semantic_output_missing_rate": round(semantic_missing_rate, 6),
            "dataset_constraint_failure_count": len(
                dataset_constraint_failures
            ),
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
                    for asset in (
                        *hard_violations,
                        *failed_assets,
                        *semantic_missing,
                        *unresolved_semantic_review,
                    )
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
        failures.extend(QualityEvaluator._constraint_failures(asset, spec))
        return tuple(failures)

    @staticmethod
    def _constraint_failures(
        asset: DatasetAsset, spec: TaskSpecVersion
    ) -> tuple[str, ...]:
        failures: list[str] = []
        metric_fields = {
            "width_px": "width",
            "height_px": "height",
            "aspect_ratio": "aspect_ratio",
            "file_size_bytes": "file_size_bytes",
            "face_count": "face_count",
        }
        for constraint in spec.constraints:
            if constraint.scope != "asset":
                continue
            if constraint.field == "primary_subject_garment_color":
                present = (
                    asset.labels.get("visual_semantic_selection")
                    in {"match", "mismatch", "uncertain"}
                )
                value = (
                    "black"
                    if asset.labels.get("visual_semantic_selection") == "match"
                    else "not_black"
                )
            else:
                metric_name = metric_fields.get(constraint.field)
                present = bool(metric_name) and metric_name in asset.metrics
                value = asset.metrics.get(metric_name) if metric_name else None
            if not present:
                failures.append(
                    f"MISSING_CONSTRAINT_EVIDENCE:{constraint.id}"
                )
                continue
            if not QualityEvaluator._matches_constraint(
                value, constraint.operator, constraint.value
            ):
                failures.append(f"CONSTRAINT_FAILED:{constraint.id}")
        return tuple(failures)

    @staticmethod
    def _dataset_constraint_failures(
        dataset: DatasetVersion, spec: TaskSpecVersion
    ) -> tuple[str, ...]:
        kept = [asset for asset in dataset.assets if asset.decision == "keep"]
        failures: list[str] = []
        for constraint in spec.constraints:
            if constraint.scope != "dataset":
                continue
            present = True
            if constraint.field == "exact_duplicate_count":
                value = len(kept) - len(
                    {asset.source_sha256 for asset in kept}
                )
            elif constraint.field == "perceptual_duplicate_policy_applied":
                present = all(
                    "duplicate_group_id" in asset.labels
                    and asset.labels.get("duplicate") is False
                    for asset in kept
                )
                value = present
            elif constraint.field == "source_assets_immutable":
                value = dataset.original_files_unchanged
            else:
                present = False
                value = None
            if not present:
                failures.append(
                    f"MISSING_CONSTRAINT_EVIDENCE:{constraint.id}"
                )
                continue
            if not QualityEvaluator._matches_constraint(
                value, constraint.operator, constraint.value
            ):
                failures.append(f"CONSTRAINT_FAILED:{constraint.id}")
        return tuple(failures)

    @staticmethod
    def _matches_constraint(
        value: object, operator: str, expected: object
    ) -> bool:
        if operator == "eq":
            return value == expected
        if operator == "lt":
            return value < expected
        if operator == "lte":
            return value <= expected
        if operator == "gt":
            return value > expected
        if operator == "gte":
            return value >= expected
        return False
