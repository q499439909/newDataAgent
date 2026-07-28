from __future__ import annotations

import hashlib
import shutil
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from ..domain.pipelines import PipelineVersion
from ..domain.operators import AnnotationRef, AssetRef, EmbeddingRef, ExecutionScope, OperatorCategory
from ..domain.runs import DatasetAsset, DatasetVersion
from ..domain.specs import TaskSpecVersion
from ..evaluation import QualityEvaluator
from ..imaging import scan_images
from ..infrastructure import DomainVersionStore, RunStore
from ..operators import OperatorRuntime
from ..operators.protocol import OperatorContext, OperatorInput
from ..application.dataset_versions import (
    build_logical_dataset_version,
    merge_repaired_dataset_version,
    validate_dataset_references,
    write_dataset_manifest,
)


_OPERATOR_OUTPUTS_KEY = "_dataagent_operator_outputs"


def _redact_parameters(value: Any, key: str = "") -> Any:
    normalized_key = key.lower()
    if any(token in normalized_key for token in ("api_key", "token", "password", "secret")):
        return "***"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_parameters(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_parameters(item) for item in value]
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_provider_execution_evidence(artifact: AssetRef) -> bool:
    return (
        artifact.media_type == "application/x-ndjson"
        and Path(artifact.uri).name in {"output.jsonl", "output_stats.jsonl"}
    )


def _ordered_nodes(pipeline: PipelineVersion):
    by_id = {node.id: node for node in pipeline.nodes}
    indegree = {node.id: 0 for node in pipeline.nodes}
    outgoing: dict[str, list[str]] = {node.id: [] for node in pipeline.nodes}
    for edge in pipeline.edges:
        indegree[edge.target] += 1
        outgoing[edge.source].append(edge.target)
    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    ordered = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(by_id[node_id])
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return ordered


class DatasetRunExecutor:
    """Deterministically executes approved pipelines outside LangGraph."""

    def __init__(
        self,
        *,
        home: Path,
        run_store: RunStore,
        version_store: DomainVersionStore,
        operator_runtime: OperatorRuntime,
        quality_evaluator: QualityEvaluator,
        worker_concurrency: int = 1,
    ) -> None:
        self.home = home.resolve()
        self.run_store = run_store
        self.version_store = version_store
        self.operator_runtime = operator_runtime
        self.quality_evaluator = quality_evaluator
        self.worker_concurrency = max(1, int(worker_concurrency))

    def execute(self, run_id: str) -> dict[str, Any]:
        run = self.run_store.get(run_id)
        self.run_store.add_event(run_id, "run_started", {"status": run["status"]})
        try:
            dataset = self._execute(run)
        except Exception as exc:
            current_status = self.run_store.get(run_id)["status"]
            if current_status == "CANCELLING":
                self.run_store.mark_cancelled(run_id)
                self.run_store.add_event(run_id, "run_cancelled", {"reason": str(exc)})
                return self.run_store.get(run_id)
            if current_status == "PAUSING":
                self.run_store.mark_paused(run_id)
                self.run_store.add_event(run_id, "run_paused", {"reason": str(exc)})
                return self.run_store.get(run_id)
            self.run_store.mark_failed(run_id, str(exc))
            self.run_store.add_event(
                run_id,
                "run_failed",
                {"error_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        if dataset is not None:
            dataset = self._merge_repair_output(run, dataset)
            self.run_store.mark_evaluating(run_id)
            self.run_store.add_event(run_id, "evaluation_started")
            try:
                self._validate_materialized_dataset(dataset)
                report = self.quality_evaluator.evaluate(
                    dataset=dataset,
                    spec=TaskSpecVersion.model_validate(
                        self.version_store.get(
                            kind="task_spec",
                            entity_id=run["task_spec_version_id"],
                            owner_id=run["owner_id"],
                        )
                    ),
                    owner_id=run["owner_id"],
                )
            except Exception as exc:
                self.run_store.mark_quality_failed(run_id, dataset.id, str(exc))
                self.run_store.add_event(
                    run_id,
                    "run_failed",
                    {"stage": "evaluation", "error_type": type(exc).__name__, "error": str(exc)},
                )
                raise
            if str(report.status) != "PASSED":
                error = "Dataset QC failed: " + ", ".join(report.reason_codes)
                if self._is_partial_completion(dataset, report):
                    self.run_store.mark_partial(run_id, dataset.id, error)
                    self.run_store.add_event(
                        run_id,
                        "run_completed_with_errors",
                        {
                            "dataset_version_id": dataset.id,
                            "qc_report_id": report.id,
                            "reason_codes": list(report.reason_codes),
                            "failed_asset_count": dataset.failed_count,
                            "retryable": True,
                        },
                    )
                    return self.run_store.get(run_id)
                self.run_store.mark_quality_failed(run_id, dataset.id, error)
                self.run_store.add_event(
                    run_id,
                    "run_failed",
                    {
                        "stage": "evaluation",
                        "dataset_version_id": dataset.id,
                        "qc_report_id": report.id,
                        "reason_codes": list(report.reason_codes),
                    },
                )
                return self.run_store.get(run_id)
            self.run_store.mark_succeeded(run_id, dataset.id)
            self.run_store.add_event(
                run_id, "run_succeeded", {"dataset_version_id": dataset.id}
            )
        return self.run_store.get(run_id)

    def _merge_repair_output(
        self,
        run: dict[str, Any],
        repair_output: DatasetVersion,
    ) -> DatasetVersion:
        parent_dataset_version_id = run.get("parent_dataset_version_id")
        if run.get("operation_kind") != "repair" or not parent_dataset_version_id:
            return repair_output
        dataset_id = f"dataset_repaired_{run['id'].removeprefix('run_')}"
        try:
            existing = self.version_store.get(
                kind="dataset",
                entity_id=dataset_id,
                owner_id=run["owner_id"],
            )
        except KeyError:
            pass
        else:
            return DatasetVersion.model_validate(existing)
        parent = DatasetVersion.model_validate(
            self.version_store.get(
                kind="dataset",
                entity_id=parent_dataset_version_id,
                owner_id=run["owner_id"],
            )
        )
        manifest_path = self.home / "datasets" / dataset_id / "manifest.json"
        repaired = merge_repaired_dataset_version(
            dataset_id=dataset_id,
            owner_id=run["owner_id"],
            parent=parent,
            repair_output=repair_output,
            repair_run_id=run["id"],
            repair_attempt=int(run["repair_attempt"]),
            manifest_uri=str(manifest_path),
        )
        write_dataset_manifest(repaired)
        self.version_store.save_if_absent(
            kind="dataset",
            owner_id=run["owner_id"],
            payload=repaired.model_dump(mode="json"),
        )
        self.run_store.add_event(
            run["id"],
            "repaired_dataset_version_created",
            {
                "dataset_version_id": repaired.id,
                "parent_dataset_version_id": parent.id,
                "repair_output_dataset_version_id": repair_output.id,
                "still_failed_count": len(repaired.still_failed),
            },
        )
        return repaired

    @staticmethod
    def _is_partial_completion(dataset: DatasetVersion, report: Any) -> bool:
        return (
            dataset.failed_count > 0
            and set(report.reason_codes) == {"EXECUTION_FAILURES_PRESENT"}
        )

    @staticmethod
    def _validate_repair_scope(
        run: dict[str, Any],
        planned: list[dict[str, Any]],
    ) -> None:
        if run.get("operation_kind") != "repair":
            return
        expected_scope = {
            (item["source_uri"], item["source_sha256"])
            for item in run.get("repair_scope", ())
        }
        actual_scope = {
            (item["source_uri"], item["source_sha256"]) for item in planned
        }
        if actual_scope != expected_scope or len(planned) != len(expected_scope):
            raise RuntimeError("Repair Run plan escaped its frozen failed-asset scope")

    @staticmethod
    def _validate_publication_outcomes(
        run: dict[str, Any],
        completed: list[dict[str, Any]],
    ) -> None:
        if (
            run.get("operation_kind") != "repair"
            and not any(item["decision"] == "keep" for item in completed)
        ):
            raise RuntimeError("Run produced an empty dataset; publication was blocked")

    @staticmethod
    def _validate_materialized_dataset(dataset: DatasetVersion) -> None:
        manifest = Path(dataset.manifest_uri)
        if not manifest.is_file():
            raise RuntimeError(f"Published Dataset manifest is missing: {manifest}")
        issues = validate_dataset_references(dataset)
        if issues:
            issue = issues[0]
            raise RuntimeError(
                f"Published Dataset reference is invalid: {issue.code}: "
                f"{issue.output_uri or issue.source_uri}"
            )

    def _parallel_execution_policy(
        self,
        ordered_nodes: list[Any],
    ) -> tuple[int, dict[str, Any] | None]:
        if self.worker_concurrency < 2:
            return 1, {"reason": "worker_concurrency_is_one"}
        remote_limits: list[int] = []
        for node in ordered_nodes:
            operator = self.operator_runtime.get(node.operator_version_id)
            prepared_batch = bool(
                getattr(operator, "supports_dataset_batch", False)
            )
            if (
                operator.spec.execution_scope != ExecutionScope.ASSET
                and not prepared_batch
            ):
                return 1, {
                    "reason": "dataset_scoped_operator",
                    "operator_version_id": node.operator_version_id,
                }
            if (
                not prepared_batch
                and not bool(getattr(operator, "parallel_safe", False))
            ):
                return 1, {
                    "reason": "operator_not_parallel_safe",
                    "operator_version_id": node.operator_version_id,
                }
            if node.runtime_backend.value == "remote":
                remote_limits.extend(
                    profile.concurrency
                    for profile in operator.spec.supported_runtime_profiles
                    if profile.backend == node.runtime_backend
                )
        worker_limit = max(
            1,
            min(self.worker_concurrency, *(remote_limits or [self.worker_concurrency])),
        )
        return worker_limit, None

    def _execute_asset(
        self,
        *,
        run: dict[str, Any],
        sequence: int,
        planned_source: dict[str, Any],
        ordered_nodes: list[Any],
        staging_files: Path,
        context: OperatorContext,
    ) -> str:
        run_id = run["id"]
        source = Path(planned_source["source_uri"])
        relative_path = Path(planned_source["output_relative_path"])
        source_hash = _sha256(source)
        if source_hash != planned_source["source_sha256"]:
            raise RuntimeError("Source image changed after the run plan was frozen")
        current = OperatorInput(source_path=str(source), current_path=str(source))
        decision = "keep"
        reason_codes: list[str] = []
        stopped_at: int | None = None
        for node_index, node in enumerate(ordered_nodes):
            status = self.run_store.get(run_id)["status"]
            if status in {"PAUSING", "CANCELLING"}:
                return status
            started_at = time.perf_counter()
            node_error: str | None = None
            node_status = "completed"
            node_decision = "continue"
            node_reason_codes: list[str] = []
            operator_provider_id = self.operator_runtime.get(
                node.operator_version_id
            ).spec.provider.provider_id
            self.run_store.add_event(
                run_id,
                "asset_node_started",
                {
                    "asset_sequence": sequence,
                    "source_uri": str(source),
                    "operator_version_id": node.operator_version_id,
                    "runtime_backend": str(node.runtime_backend),
                    "parameters": _redact_parameters(node.parameters),
                },
                node_id=node.id,
                provider_id=operator_provider_id,
            )
            try:
                context.shared["active_node_id"] = node.id
                result = self.operator_runtime.execute(
                    operator_version_id=node.operator_version_id,
                    context=context,
                    input_data=current,
                    parameters=node.parameters,
                    runtime_backend=node.runtime_backend,
                )
                node_decision = result.decision
                node_reason_codes = list(result.reason_codes)
                current = OperatorInput(
                    source_path=current.source_path,
                    current_path=result.output_path or current.current_path,
                    metrics=result.metrics,
                    labels=result.labels,
                    artifacts=result.artifacts,
                    annotations=result.annotations,
                    embeddings=result.embeddings,
                )
                reason_codes.extend(result.reason_codes)
                if result.decision == "reject":
                    decision = "reject"
                    stopped_at = node_index
            except Exception as exc:
                node_status = "failed"
                node_decision = "failed"
                node_error = str(exc)
                node_reason_codes = [f"OPERATOR_ERROR:{type(exc).__name__}"]
                decision = "failed"
                reason_codes.extend(node_reason_codes)
                current = current.model_copy(
                    update={
                        "labels": {
                            **current.labels,
                            "execution_error": str(exc),
                        }
                    }
                )
                stopped_at = node_index
            duration_ms = round((time.perf_counter() - started_at) * 1000)
            self.run_store.add_node_result(
                run_id,
                {
                    "asset_sequence": sequence,
                    "source_uri": str(source),
                    "node_id": node.id,
                    "operator_version_id": node.operator_version_id,
                    "status": node_status,
                    "decision": node_decision,
                    "reason_codes": node_reason_codes,
                    "metrics": current.metrics,
                    "labels": current.labels,
                    "error": node_error,
                    "duration_ms": duration_ms,
                },
            )
            self.run_store.add_event(
                run_id,
                "asset_node_completed",
                {
                    "asset_sequence": sequence,
                    "source_uri": str(source),
                    "operator_version_id": node.operator_version_id,
                    "status": node_status,
                    "decision": node_decision,
                    "reason_codes": node_reason_codes,
                    "error": node_error,
                    "duration_ms": duration_ms,
                },
                node_id=node.id,
                provider_id=operator_provider_id,
            )
            if stopped_at is not None:
                break

        if stopped_at is not None:
            for node in ordered_nodes[stopped_at + 1 :]:
                self.run_store.add_node_result(
                    run_id,
                    {
                        "asset_sequence": sequence,
                        "source_uri": str(source),
                        "node_id": node.id,
                        "operator_version_id": node.operator_version_id,
                        "status": "skipped",
                        "decision": "not_run",
                        "reason_codes": ["UPSTREAM_REJECTED_OR_FAILED"],
                        "metrics": {},
                        "labels": {},
                        "duration_ms": 0,
                    },
                )

        output_relative_path: str | None = None
        output_hash: str | None = None
        if decision == "keep":
            source_output = Path(current.current_path).resolve()
            requested_relative_path = current.labels.get("output_relative_path")
            if requested_relative_path:
                requested = Path(str(requested_relative_path))
                if requested.is_absolute() or ".." in requested.parts:
                    raise ValueError(
                        "Operator produced an unsafe output_relative_path"
                    )
                relative_path = requested
            destination = staging_files / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_output, destination)
            output_relative_path = relative_path.as_posix()
            output_hash = _sha256(destination)
        if _sha256(source) != source_hash:
            raise RuntimeError(f"Source image changed during execution: {source}")
        self.run_store.add_item(
            run_id,
            {
                "sequence": sequence,
                "source_uri": str(source),
                "source_sha256": source_hash,
                "output_relative_path": output_relative_path,
                "output_sha256": output_hash,
                "decision": decision,
                "reason_codes": reason_codes,
                "metrics": current.metrics,
                "labels": self._checkpoint_labels(current),
            },
        )
        progress = self.run_store.get(run_id)["progress"]
        self.run_store.add_event(
            run_id,
            "asset_completed",
            {
                "sequence": sequence,
                "decision": decision,
                "source_uri": str(source),
                "reason_codes": reason_codes,
                "retryable": decision == "failed",
            },
            progress=progress,
        )
        return "completed"

    def _parallel_asset_context(
        self,
        run: dict[str, Any],
        artifact_root: Path,
        dataset_operator_results: dict[str, Any],
    ) -> OperatorContext:
        context: OperatorContext

        def cancel_check() -> bool:
            return self.run_store.get(run["id"])["status"] in {
                "CANCELLING",
                "PAUSING",
            }

        def event_sink(event_type: str, details: dict[str, Any]) -> None:
            payload = dict(details)
            self.run_store.add_event(
                run["id"],
                event_type,
                payload,
                node_id=str(context.shared.get("active_node_id") or "") or None,
                provider_id=payload.get("provider_id"),
            )

        context = OperatorContext(
            run_id=run["id"],
            work_order_id=run["work_order_id"],
            owner_id=run["owner_id"],
            shared={
                "seen_dhash": set(),
                "artifact_root": artifact_root,
                "cancel_check": cancel_check,
                "event_sink": event_sink,
                "dataset_operator_results": dataset_operator_results,
            },
        )
        return context

    def _execute_assets_parallel(
        self,
        *,
        run: dict[str, Any],
        planned: list[dict[str, Any]],
        existing: list[dict[str, Any]],
        ordered_nodes: list[Any],
        staging_files: Path,
        artifact_root: Path,
        worker_count: int,
        dataset_operator_results: dict[str, Any],
    ) -> str:
        existing_by_sequence = {item["sequence"]: item for item in existing}
        pending: list[tuple[int, dict[str, Any]]] = []
        for sequence, planned_source in enumerate(planned):
            checkpoint = existing_by_sequence.get(sequence)
            if checkpoint is not None:
                if (
                    checkpoint["source_uri"] != planned_source["source_uri"]
                    or checkpoint["source_sha256"]
                    != planned_source["source_sha256"]
                ):
                    raise RuntimeError(
                        "Source plan changed after the run was checkpointed"
                    )
                continue
            pending.append((sequence, planned_source))
        if not pending:
            return "completed"

        control_status = "completed"
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="dataagent-asset",
        ) as executor:
            pending_iterator = iter(pending)
            futures: dict[Future[str], int] = {}

            def submit_next() -> bool:
                try:
                    sequence, planned_source = next(pending_iterator)
                except StopIteration:
                    return False
                future = executor.submit(
                    self._execute_asset,
                    run=run,
                    sequence=sequence,
                    planned_source=planned_source,
                    ordered_nodes=ordered_nodes,
                    staging_files=staging_files,
                    context=self._parallel_asset_context(
                        run,
                        artifact_root,
                        dataset_operator_results,
                    ),
                )
                futures[future] = sequence
                return True

            for _ in range(worker_count):
                if not submit_next():
                    break
            while futures:
                completed_futures, _ = wait(
                    tuple(futures),
                    return_when=FIRST_COMPLETED,
                )
                for future in completed_futures:
                    futures.pop(future)
                    result = future.result()
                    if result in {"PAUSING", "CANCELLING"}:
                        control_status = result
                current_status = self.run_store.get(run["id"])["status"]
                if current_status in {"PAUSING", "CANCELLING"}:
                    control_status = current_status
                if control_status == "completed":
                    while len(futures) < worker_count and submit_next():
                        pass
        return control_status

    def _execute(self, run: dict[str, Any]) -> DatasetVersion | None:
        owner_id = run["owner_id"]
        pipeline = PipelineVersion.model_validate(
            self.version_store.get(
                kind="pipeline",
                entity_id=run["pipeline_version_id"],
                owner_id=owner_id,
            )
        )
        if not pipeline.approved:
            raise ValueError("Dataset runs require an approved PipelineVersion")
        spec = TaskSpecVersion.model_validate(
            self.version_store.get(
                kind="task_spec",
                entity_id=run["task_spec_version_id"],
                owner_id=owner_id,
            )
        )
        if not spec.confirmed:
            raise ValueError("Dataset runs require a confirmed TaskSpecVersion")
        roots = [
            Path(item.uri).expanduser().resolve()
            for item in spec.data_sources
            if item.type == "local_directory"
        ]
        if not roots:
            raise ValueError("Local worker currently requires a local_directory data source")
        planned = self.run_store.plan(run["id"])
        if run.get("operation_kind") == "repair" and not planned:
            raise RuntimeError("Repair Run has no frozen repair plan")
        if not planned:
            discovered: list[dict[str, Any]] = []
            for root_index, root in enumerate(roots, start=1):
                for source in scan_images(root):
                    prefix = Path(f"source_{root_index}") if len(roots) > 1 else Path()
                    discovered.append(
                        {
                            "sequence": len(discovered),
                            "source_uri": str(source),
                            "source_sha256": _sha256(source),
                            "output_relative_path": (prefix / source.relative_to(root)).as_posix(),
                        }
                    )
            planned = self.run_store.initialize_plan(run["id"], discovered)
        if not planned:
            raise ValueError("No supported images found in the approved data sources")
        self._validate_repair_scope(run, planned)
        self.run_store.set_total(run["id"], len(planned))
        self.operator_runtime.validate_pipeline(pipeline)
        ordered_nodes = _ordered_nodes(pipeline)
        encountered_asset_processing = False
        for node in ordered_nodes:
            operator_spec = self.operator_runtime.get(node.operator_version_id).spec
            if operator_spec.execution_scope == ExecutionScope.DATASET:
                if encountered_asset_processing:
                    raise ValueError(
                        "Dataset-scoped operators must precede asset filtering and transforms"
                    )
            elif operator_spec.primary_category != OperatorCategory.INGESTION:
                encountered_asset_processing = True
        self.run_store.add_event(
            run["id"],
            "run_planned",
            {"asset_count": len(planned), "node_count": len(ordered_nodes)},
        )

        existing = self.run_store.items(run["id"])
        if len(existing) > len(planned):
            raise RuntimeError("Run checkpoint has more assets than the current source plan")
        def cancel_check() -> bool:
            return self.run_store.get(run["id"])["status"] in {
                "CANCELLING",
                "PAUSING",
            }

        def event_sink(event_type: str, details: dict[str, Any]) -> None:
            payload = dict(details)
            self.run_store.add_event(
                run["id"],
                event_type,
                payload,
                node_id=str(context.shared.get("active_node_id") or "") or None,
                provider_id=payload.get("provider_id"),
            )

        context = OperatorContext(
            run_id=run["id"],
            work_order_id=run["work_order_id"],
            owner_id=owner_id,
            shared={
                "seen_dhash": set(),
                "artifact_root": self.home / "runs" / run["id"] / "artifacts",
                "cancel_check": cancel_check,
                "event_sink": event_sink,
            },
        )
        for item in existing:
            dhash = item["metrics"].get("dhash")
            if dhash:
                context.shared["seen_dhash"].add(dhash)

        staging_files = self.home / "runs" / run["id"] / "files"
        raw_inputs = tuple(
            OperatorInput(
                source_path=item["source_uri"], current_path=item["source_uri"]
            )
            for item in planned
        )
        batchable_prefix = True
        for node in ordered_nodes:
            context.shared["active_node_id"] = node.id
            operator = self.operator_runtime.get(node.operator_version_id)
            is_dataset = operator.spec.execution_scope == ExecutionScope.DATASET
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
            self.run_store.add_event(
                run["id"],
                "provider_batch_node_started" if is_batch_filter else "dataset_node_started",
                {"operator_version_id": node.operator_version_id, "asset_count": len(raw_inputs)},
                node_id=node.id,
                provider_id=operator.spec.provider.provider_id,
            )
            self.operator_runtime.prepare_dataset_node(
                operator_version_id=node.operator_version_id,
                node_id=node.id,
                context=context,
                inputs=raw_inputs,
                parameters=node.parameters,
                runtime_backend=node.runtime_backend,
            )
            self.run_store.add_event(
                run["id"],
                "provider_batch_node_completed" if is_batch_filter else "dataset_node_completed",
                {"operator_version_id": node.operator_version_id, "asset_count": len(raw_inputs)},
                node_id=node.id,
                provider_id=operator.spec.provider.provider_id,
            )
        parallel_limit, parallel_blocker = self._parallel_execution_policy(
            ordered_nodes
        )
        parallel_workers = min(
            len(planned),
            parallel_limit,
        )
        if parallel_workers > 1:
            self.run_store.add_event(
                run["id"],
                "asset_parallelism_selected",
                {
                    "requested_concurrency": self.worker_concurrency,
                    "effective_concurrency": parallel_workers,
                    "asset_count": len(planned),
                },
            )
            control_status = self._execute_assets_parallel(
                run=run,
                planned=planned,
                existing=existing,
                ordered_nodes=ordered_nodes,
                staging_files=staging_files,
                artifact_root=self.home / "runs" / run["id"] / "artifacts",
                worker_count=parallel_workers,
                dataset_operator_results=context.shared.get(
                    "dataset_operator_results", {}
                ),
            )
            if control_status == "PAUSING":
                self.run_store.mark_paused(run["id"])
                self.run_store.add_event(run["id"], "run_paused")
                return None
            if control_status == "CANCELLING":
                self.run_store.mark_cancelled(run["id"])
                self.run_store.add_event(run["id"], "run_cancelled")
                return None
            completed = self.run_store.items(run["id"])
            self._validate_publication_outcomes(run, completed)
            return self._publish(run, spec, pipeline, roots, completed)
        if (
            len(planned) > 1
            and self.worker_concurrency > 1
            and parallel_blocker is not None
        ):
            self.run_store.add_event(
                run["id"],
                "asset_parallelism_disabled",
                {
                    "requested_concurrency": self.worker_concurrency,
                    **parallel_blocker,
                },
            )
        for sequence, planned_source in enumerate(planned):
            source = Path(planned_source["source_uri"])
            source_hash = _sha256(source)
            if source_hash != planned_source["source_sha256"]:
                raise RuntimeError("Source image changed after the run plan was frozen")
            if sequence < len(existing):
                checkpoint = existing[sequence]
                if checkpoint["source_uri"] != str(source) or checkpoint["source_sha256"] != source_hash:
                    raise RuntimeError("Source plan changed after the run was checkpointed")
                continue

            status = self.run_store.get(run["id"])["status"]
            if status == "PAUSING":
                self.run_store.mark_paused(run["id"])
                self.run_store.add_event(run["id"], "run_paused")
                return None
            if status == "CANCELLING":
                self.run_store.mark_cancelled(run["id"])
                self.run_store.add_event(run["id"], "run_cancelled")
                return None

            control_status = self._execute_asset(
                run=run,
                sequence=sequence,
                planned_source=planned_source,
                ordered_nodes=ordered_nodes,
                staging_files=staging_files,
                context=context,
            )
            if control_status == "PAUSING":
                self.run_store.mark_paused(run["id"])
                self.run_store.add_event(run["id"], "run_paused")
                return None
            if control_status == "CANCELLING":
                self.run_store.mark_cancelled(run["id"])
                self.run_store.add_event(run["id"], "run_cancelled")
                return None

        completed = self.run_store.items(run["id"])
        self._validate_publication_outcomes(run, completed)
        return self._publish(run, spec, pipeline, roots, completed)

    @staticmethod
    def _checkpoint_labels(current: OperatorInput) -> dict[str, Any]:
        labels = dict(current.labels)
        if current.artifacts or current.annotations or current.embeddings:
            labels[_OPERATOR_OUTPUTS_KEY] = {
                "artifacts": [item.model_dump(mode="json") for item in current.artifacts],
                "annotations": [item.model_dump(mode="json") for item in current.annotations],
                "embeddings": [item.model_dump(mode="json") for item in current.embeddings],
            }
        return labels

    def _publish(
        self,
        run: dict[str, Any],
        spec: TaskSpecVersion,
        pipeline: PipelineVersion,
        roots: list[Path],
        items: list[dict[str, Any]],
    ) -> DatasetVersion:
        dataset_id = f"dataset_{run['id'].removeprefix('run_')}"
        try:
            existing = self.version_store.get(
                kind="dataset", entity_id=dataset_id, owner_id=run["owner_id"]
            )
        except KeyError:
            pass
        else:
            return DatasetVersion.model_validate(existing)
        staging_root = self.home / "runs" / run["id"]
        dataset_root = self.home / "datasets" / dataset_id
        if not dataset_root.exists():
            dataset_root.parent.mkdir(parents=True, exist_ok=True)
            staging_root.mkdir(parents=True, exist_ok=True)
            staging_root.replace(dataset_root)
        manifest_path = dataset_root / "manifest.json"

        def published_artifact(value: dict[str, Any]) -> AssetRef:
            artifact = AssetRef.model_validate(value)
            artifact_path = Path(artifact.uri)
            if artifact_path.is_absolute() and artifact_path.is_relative_to(staging_root):
                artifact = artifact.model_copy(
                    update={"uri": str(dataset_root / artifact_path.relative_to(staging_root))}
                )
            return artifact

        assets: list[DatasetAsset] = []
        execution_evidence: dict[tuple[str, str | None], AssetRef] = {}
        for sequence, item in enumerate(items):
            labels = dict(item["labels"])
            operator_outputs = labels.pop(_OPERATOR_OUTPUTS_KEY, {})
            delivery_artifacts: list[AssetRef] = []
            for value in operator_outputs.get("artifacts", ()):
                artifact = published_artifact(value)
                if _is_provider_execution_evidence(artifact):
                    execution_evidence[(artifact.uri, artifact.sha256)] = artifact
                else:
                    delivery_artifacts.append(artifact)
            assets.append(DatasetAsset(
                source_uri=item["source_uri"],
                source_sha256=item["source_sha256"],
                output_uri=(
                    str((dataset_root / "files" / item["output_relative_path"]).resolve())
                    if item["output_relative_path"]
                    else None
                ),
                output_sha256=item["output_sha256"],
                decision=item["decision"],
                reason_codes=tuple(item["reason_codes"]),
                metrics=item["metrics"],
                labels=labels,
                artifacts=tuple(delivery_artifacts),
                annotations=tuple(
                    AnnotationRef.model_validate(value)
                    for value in operator_outputs.get("annotations", ())
                ),
                embeddings=tuple(
                    EmbeddingRef.model_validate(value)
                    for value in operator_outputs.get("embeddings", ())
                ),
                origin_run_id=run["id"],
                audit_refs=(f"run:{run['id']}:asset:{sequence}",),
            ))
        dataset = build_logical_dataset_version(
            dataset_id=dataset_id,
            owner_id=run["owner_id"],
            work_order_id=run["work_order_id"],
            pipeline_version_id=pipeline.id,
            task_spec_version_id=spec.id,
            run_id=run["id"],
            source_roots=tuple(str(root) for root in roots),
            manifest_uri=str(manifest_path.resolve()),
            assets=assets,
        )
        write_dataset_manifest(dataset)
        if execution_evidence:
            self.run_store.add_event(
                run["id"],
                "provider_execution_evidence_published",
                {
                    "dataset_version_id": dataset.id,
                    "artifacts": [
                        artifact.model_dump(mode="json")
                        for artifact in execution_evidence.values()
                    ],
                },
            )
        self.version_store.save_if_absent(
            kind="dataset", owner_id=run["owner_id"], payload=dataset.model_dump(mode="json")
        )
        return dataset
