from __future__ import annotations

import tempfile
import shutil
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from ..domain.common import new_id
from ..domain.common.models import DomainModel
from ..domain.operators import OperatorCategory
from ..domain.pipelines import PipelineNode, PipelineVersion
from ..domain.specs import ConstraintContract, TaskSpecVersion
from ..operators import OperatorLibrary
from ..operators.protocol import OperatorContext, OperatorInput


class PipelineTrialStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class ConstraintTrialStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    MISSING_EVIDENCE = "missing_evidence"
    NOT_EVALUATED = "not_evaluated"


class ConstraintTrialResult(DomainModel):
    constraint_id: str
    asset_path: str | None = None
    status: ConstraintTrialStatus
    operator_version_id: str | None = None
    evidence_type: str
    observed_value: Any = None
    failure_code: str | None = None


class PipelineTrialRequest(DomainModel):
    task_spec: TaskSpecVersion
    pipeline: PipelineVersion
    sample_paths: tuple[str, ...] = ()
    max_assets: int = Field(default=20, ge=1, le=500)

    @model_validator(mode="after")
    def validate_versions(self) -> "PipelineTrialRequest":
        if self.pipeline.task_spec_version_id != self.task_spec.id:
            raise ValueError("Pipeline trial requires the matching TaskSpecVersion")
        if not self.task_spec.confirmed:
            raise ValueError("Pipeline trial requires a confirmed TaskSpecVersion")
        return self


class PipelineTrialObservation(DomainModel):
    pipeline_version_id: str
    trial_run_id: str
    status: PipelineTrialStatus
    asset_count: int
    constraint_results: tuple[ConstraintTrialResult, ...] = ()
    execution_failures: tuple[str, ...] = ()
    dataset_level_failures: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    cost: dict[str, float] = Field(default_factory=dict)


def _ordered_nodes(pipeline: PipelineVersion) -> tuple[PipelineNode, ...]:
    by_id = {node.id: node for node in pipeline.nodes}
    indegree = {node.id: 0 for node in pipeline.nodes}
    outgoing: dict[str, list[str]] = {node.id: [] for node in pipeline.nodes}
    for edge in pipeline.edges:
        indegree[edge.target] += 1
        outgoing[edge.source].append(edge.target)
    ready = [node.id for node in pipeline.nodes if indegree[node.id] == 0]
    ordered: list[PipelineNode] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(by_id[node_id])
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return tuple(ordered)


def _candidate_evidence_keys(field: str) -> tuple[str, ...]:
    normalized = field.strip()
    leaf = normalized.rsplit(".", 1)[-1]
    return tuple(dict.fromkeys((normalized, normalized.replace(".", "_"), leaf)))


def _string_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(
            item
            for nested in value.values()
            for item in _string_values(nested)
        )
    if isinstance(value, (list, tuple, set)):
        return tuple(
            item for nested in value for item in _string_values(nested)
        )
    return ()


def _observed_value(
    *,
    constraint: ConstraintContract,
    snapshot: dict[str, Any],
) -> Any:
    if constraint.required_evidence_type == "visual_semantic_judgment":
        judgments = {
            value.strip().lower().replace("_", "-").replace(" ", "-")
            for value in _string_values(snapshot.get("labels") or {})
        }
        if {"semantic-match", "match"}.intersection(judgments):
            return constraint.value
        if {"semantic-mismatch", "mismatch"}.intersection(judgments):
            return "__semantic_mismatch__"
        if {"semantic-uncertain", "uncertain"}.intersection(judgments):
            return "__semantic_uncertain__"
    for source_name in ("metrics", "labels"):
        source = snapshot.get(source_name) or {}
        for key in _candidate_evidence_keys(constraint.field):
            if key in source:
                return source[key]
    return None


def _satisfies(constraint: ConstraintContract, observed: Any) -> bool:
    expected = constraint.value
    if constraint.operator == "eq":
        return observed == expected
    try:
        left = float(observed)
        right = float(expected)
    except (TypeError, ValueError):
        return False
    if constraint.operator == "lt":
        return left < right
    if constraint.operator == "lte":
        return left <= right
    if constraint.operator == "gt":
        return left > right
    if constraint.operator == "gte":
        return left >= right
    return False


def _dataset_constraint_result(
    *,
    constraint: ConstraintContract,
    coverage: Any,
    snapshots: dict[str, dict[str, dict[str, Any]]],
) -> ConstraintTrialResult:
    operator_version_id = (
        coverage.operator_version_id if coverage is not None else None
    )
    if coverage is None:
        return ConstraintTrialResult(
            constraint_id=constraint.id,
            status=ConstraintTrialStatus.MISSING_EVIDENCE,
            operator_version_id=None,
            evidence_type=constraint.required_evidence_type,
            failure_code="MISSING_EVIDENCE",
        )
    node_snapshots = [
        item.get(coverage.node_id) for item in snapshots.values()
    ]
    has_duplicate_groups = node_snapshots and all(
        item is not None
        and item.get("labels", {}).get("duplicate_group_id")
        for item in node_snapshots
    )
    if has_duplicate_groups:
        groups: dict[str, int] = {}
        for item in node_snapshots:
            group_id = str(item["labels"]["duplicate_group_id"])
            if item["decision"] not in {"reject", "failed"}:
                groups[group_id] = groups.get(group_id, 0) + 1
            else:
                groups.setdefault(group_id, 0)
        if constraint.field.rsplit(".", 1)[-1] == "duplicate_count":
            observed = sum(max(0, count - 1) for count in groups.values())
        elif constraint.required_evidence_type == "duplicate_group_membership":
            observed = bool(groups) and all(count == 1 for count in groups.values())
        else:
            observed = None
        if observed is None:
            return ConstraintTrialResult(
                constraint_id=constraint.id,
                status=ConstraintTrialStatus.MISSING_EVIDENCE,
                operator_version_id=operator_version_id,
                evidence_type=constraint.required_evidence_type,
                failure_code="MISSING_EVIDENCE",
            )
        passed = _satisfies(constraint, observed)
        return ConstraintTrialResult(
            constraint_id=constraint.id,
            status=(
                ConstraintTrialStatus.PASSED
                if passed
                else ConstraintTrialStatus.FAILED
            ),
            operator_version_id=operator_version_id,
            evidence_type=constraint.required_evidence_type,
            observed_value=observed,
            failure_code=None if passed else "DATASET_CONSTRAINT_FAILED",
        )
    return ConstraintTrialResult(
        constraint_id=constraint.id,
        status=ConstraintTrialStatus.MISSING_EVIDENCE,
        operator_version_id=operator_version_id,
        evidence_type=constraint.required_evidence_type,
        failure_code="MISSING_EVIDENCE",
    )


class PipelineTrialRunner:
    """Execute a bounded sample and return Constraint-scoped observations."""

    def __init__(
        self,
        operator_library: OperatorLibrary,
        *,
        trial_root: Path | None = None,
        max_assets: int = 3,
    ) -> None:
        self.operator_library = operator_library
        self.max_assets = max(1, min(int(max_assets), 500))
        self.trial_root = (
            trial_root
            if trial_root is not None
            else Path(tempfile.mkdtemp(prefix="dataagent-trial-"))
        )

    @staticmethod
    def _sample_paths(request: PipelineTrialRequest) -> tuple[Path, ...]:
        if request.sample_paths:
            candidates = (
                Path(value).expanduser().resolve()
                for value in request.sample_paths
            )
        else:
            roots = (
                Path(source.uri).expanduser().resolve()
                for source in request.task_spec.data_sources
                if source.type == "local_directory"
            )
            candidates = (
                path.resolve()
                for root in roots
                if root.is_dir()
                for path in sorted(root.rglob("*"))
                if path.is_file()
            )
        selected: list[Path] = []
        seen: set[Path] = set()
        for path in candidates:
            if path in seen:
                continue
            selected.append(path)
            seen.add(path)
            if len(selected) >= request.max_assets:
                break
        return tuple(selected)

    def run(self, request: PipelineTrialRequest) -> PipelineTrialObservation:
        trial_run_id = new_id("pipeline_trial")
        paths = self._sample_paths(
            request.model_copy(
                update={
                    "max_assets": min(request.max_assets, self.max_assets),
                }
            )
        )
        missing_paths = tuple(str(path) for path in paths if not path.is_file())
        if missing_paths:
            return PipelineTrialObservation(
                pipeline_version_id=request.pipeline.id,
                trial_run_id=trial_run_id,
                status=PipelineTrialStatus.BLOCKED,
                asset_count=0,
                execution_failures=tuple(
                    f"ASSET_NOT_FOUND:{path}" for path in missing_paths
                ),
            )
        if not paths:
            return PipelineTrialObservation(
                pipeline_version_id=request.pipeline.id,
                trial_run_id=trial_run_id,
                status=PipelineTrialStatus.BLOCKED,
                asset_count=0,
                execution_failures=("NO_TRIAL_ASSETS",),
            )
        try:
            self.operator_library.runtime.validate_pipeline(request.pipeline)
        except (KeyError, ValueError) as exc:
            return PipelineTrialObservation(
                pipeline_version_id=request.pipeline.id,
                trial_run_id=trial_run_id,
                status=PipelineTrialStatus.BLOCKED,
                asset_count=len(paths),
                execution_failures=(f"PIPELINE_INVALID:{exc}",),
            )

        ordered_nodes = _ordered_nodes(request.pipeline)
        run_root = self.trial_root / trial_run_id
        input_root = run_root / "inputs"
        artifact_root = run_root / "artifacts"
        input_root.mkdir(parents=True, exist_ok=True)
        working_paths: dict[Path, Path] = {}
        for index, path in enumerate(paths):
            working_path = input_root / f"{index:04d}-{path.name}"
            shutil.copy2(path, working_path)
            working_paths[path] = working_path
        shared: dict[str, Any] = {
            "seen_dhash": set(),
            "artifact_root": artifact_root,
        }
        context = OperatorContext(
            run_id=trial_run_id,
            work_order_id=request.task_spec.work_order_id,
            owner_id=request.task_spec.created_by,
            purpose="development",
            shared=shared,
        )
        raw_inputs = tuple(
            OperatorInput(
                source_path=str(path),
                current_path=str(working_paths[path]),
            )
            for path in paths
        )
        execution_failures: list[str] = []
        batchable_prefix = True
        for node in ordered_nodes:
            operator = self.operator_library.runtime.get(node.operator_version_id)
            is_dataset = operator.spec.execution_scope.value == "dataset"
            is_batch_filter = (
                batchable_prefix
                and bool(getattr(operator, "supports_dataset_batch", False))
            )
            if not is_dataset and not is_batch_filter:
                if operator.spec.primary_category not in {
                    OperatorCategory.INGESTION,
                    OperatorCategory.FILTERING,
                    OperatorCategory.DEDUPLICATION,
                }:
                    batchable_prefix = False
                continue
            try:
                context.shared["active_node_id"] = node.id
                self.operator_library.runtime.prepare_dataset_node(
                    operator_version_id=node.operator_version_id,
                    node_id=node.id,
                    context=context,
                    inputs=raw_inputs,
                    parameters=node.parameters,
                    runtime_backend=node.runtime_backend,
                )
            except Exception as exc:
                execution_failures.append(
                    f"OPERATOR_EXECUTION_FAILED:{node.id}:{type(exc).__name__}:{exc}"
                )

        snapshots: dict[str, dict[str, dict[str, Any]]] = {}
        upstream_rejected: set[str] = set()
        for path in paths:
            current = OperatorInput(
                source_path=str(path),
                current_path=str(working_paths[path]),
            )
            node_snapshots: dict[str, dict[str, Any]] = {}
            for node in ordered_nodes:
                context.shared["active_node_id"] = node.id
                try:
                    result = self.operator_library.runtime.execute(
                        operator_version_id=node.operator_version_id,
                        context=context,
                        input_data=current,
                        parameters=node.parameters,
                        runtime_backend=node.runtime_backend,
                    )
                except Exception as exc:
                    execution_failures.append(
                        f"OPERATOR_EXECUTION_FAILED:{node.id}:{path}:"
                        f"{type(exc).__name__}:{exc}"
                    )
                    break
                current = OperatorInput(
                    source_path=current.source_path,
                    current_path=result.output_path or current.current_path,
                    metrics=result.metrics,
                    labels=result.labels,
                    artifacts=result.artifacts,
                    annotations=result.annotations,
                    embeddings=result.embeddings,
                )
                node_snapshots[node.id] = {
                    "metrics": dict(result.metrics),
                    "labels": dict(result.labels),
                    "decision": result.decision,
                    "reason_codes": tuple(result.reason_codes),
                }
                if result.decision in {"reject", "failed"}:
                    if result.decision == "reject":
                        upstream_rejected.add(str(path))
                    break
            snapshots[str(path)] = node_snapshots

        coverage = {
            item.constraint_id: item for item in request.pipeline.constraint_coverage
        }
        results: list[ConstraintTrialResult] = []
        for constraint in request.task_spec.constraints:
            if constraint.scope == "dataset":
                results.append(
                    _dataset_constraint_result(
                        constraint=constraint,
                        coverage=coverage.get(constraint.id),
                        snapshots=snapshots,
                    )
                )
                continue
            assigned = coverage.get(constraint.id)
            for path in paths:
                snapshot = (
                    snapshots[str(path)].get(assigned.node_id)
                    if assigned is not None
                    else None
                )
                if snapshot is None:
                    if str(path) in upstream_rejected:
                        results.append(
                            ConstraintTrialResult(
                                constraint_id=constraint.id,
                                asset_path=str(path),
                                status=ConstraintTrialStatus.NOT_EVALUATED,
                                operator_version_id=(
                                    assigned.operator_version_id
                                    if assigned is not None
                                    else None
                                ),
                                evidence_type=constraint.required_evidence_type,
                                failure_code="UPSTREAM_REJECTED",
                            )
                        )
                        continue
                    results.append(
                        ConstraintTrialResult(
                            constraint_id=constraint.id,
                            asset_path=str(path),
                            status=ConstraintTrialStatus.MISSING_EVIDENCE,
                            operator_version_id=(
                                assigned.operator_version_id
                                if assigned is not None
                                else None
                            ),
                            evidence_type=constraint.required_evidence_type,
                            failure_code="MISSING_EVIDENCE",
                        )
                    )
                    continue
                observed = _observed_value(
                    constraint=constraint,
                    snapshot=snapshot,
                )
                if observed is None:
                    results.append(
                        ConstraintTrialResult(
                            constraint_id=constraint.id,
                            asset_path=str(path),
                            status=ConstraintTrialStatus.MISSING_EVIDENCE,
                            operator_version_id=assigned.operator_version_id,
                            evidence_type=constraint.required_evidence_type,
                            failure_code="MISSING_EVIDENCE",
                        )
                    )
                    continue
                condition_satisfied = _satisfies(constraint, observed)
                rejected = snapshot.get("decision") in {"reject", "failed"}
                passed = (
                    not condition_satisfied if rejected else condition_satisfied
                )
                results.append(
                    ConstraintTrialResult(
                        constraint_id=constraint.id,
                        asset_path=str(path),
                        status=(
                            ConstraintTrialStatus.PASSED
                            if passed
                            else ConstraintTrialStatus.FAILED
                        ),
                        operator_version_id=assigned.operator_version_id,
                        evidence_type=constraint.required_evidence_type,
                        observed_value=observed,
                        failure_code=None if passed else "CONSTRAINT_NOT_SATISFIED",
                    )
                )

        failed = bool(execution_failures) or any(
            item.status
            in {
                ConstraintTrialStatus.FAILED,
                ConstraintTrialStatus.MISSING_EVIDENCE,
            }
            for item in results
            if next(
                constraint
                for constraint in request.task_spec.constraints
                if constraint.id == item.constraint_id
            ).hardness
            == "hard"
        )
        return PipelineTrialObservation(
            pipeline_version_id=request.pipeline.id,
            trial_run_id=trial_run_id,
            status=(
                PipelineTrialStatus.FAILED if failed else PipelineTrialStatus.PASSED
            ),
            asset_count=len(paths),
            constraint_results=tuple(results),
            execution_failures=tuple(execution_failures),
            artifact_refs=(
                (str(artifact_root),) if artifact_root.exists() else ()
            ),
        )


__all__ = [
    "ConstraintTrialResult",
    "ConstraintTrialStatus",
    "PipelineTrialObservation",
    "PipelineTrialRequest",
    "PipelineTrialRunner",
    "PipelineTrialStatus",
]
