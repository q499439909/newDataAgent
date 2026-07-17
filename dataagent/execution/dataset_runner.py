from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from ..domain.pipelines import PipelineVersion
from ..domain.operators import AnnotationRef, AssetRef, EmbeddingRef
from ..domain.runs import DatasetAsset, DatasetVersion
from ..domain.specs import TaskSpecVersion
from ..evaluation import QualityEvaluator
from ..imaging import scan_images
from ..infrastructure import DomainVersionStore, RunStore
from ..operators import OperatorRuntime
from ..operators.protocol import OperatorContext, OperatorInput


_OPERATOR_OUTPUTS_KEY = "_dataagent_operator_outputs"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    ) -> None:
        self.home = home.resolve()
        self.run_store = run_store
        self.version_store = version_store
        self.operator_runtime = operator_runtime
        self.quality_evaluator = quality_evaluator

    def execute(self, run_id: str) -> dict[str, Any]:
        run = self.run_store.get(run_id)
        try:
            dataset = self._execute(run)
        except Exception as exc:
            self.run_store.mark_failed(run_id, str(exc))
            raise
        if dataset is not None:
            self.run_store.mark_evaluating(run_id)
            self.quality_evaluator.evaluate(
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
            self.run_store.mark_succeeded(run_id, dataset.id)
        return self.run_store.get(run_id)

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
        self.run_store.set_total(run["id"], len(planned))
        self.operator_runtime.validate_pipeline(pipeline)

        existing = self.run_store.items(run["id"])
        if len(existing) > len(planned):
            raise RuntimeError("Run checkpoint has more assets than the current source plan")
        context = OperatorContext(
            run_id=run["id"],
            work_order_id=run["work_order_id"],
            owner_id=owner_id,
            shared={
                "seen_dhash": set(),
                "artifact_root": self.home / "runs" / run["id"] / "artifacts",
            },
        )
        for item in existing:
            dhash = item["metrics"].get("dhash")
            if dhash:
                context.shared["seen_dhash"].add(dhash)

        staging_files = self.home / "runs" / run["id"] / "files"
        for sequence, planned_source in enumerate(planned):
            source = Path(planned_source["source_uri"])
            relative_path = Path(planned_source["output_relative_path"])
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
                return None
            if status == "CANCELLING":
                self.run_store.mark_cancelled(run["id"])
                return None

            current = OperatorInput(source_path=str(source), current_path=str(source))
            decision = "keep"
            reason_codes: list[str] = []
            try:
                for node in _ordered_nodes(pipeline):
                    result = self.operator_runtime.execute(
                        operator_version_id=node.operator_version_id,
                        context=context,
                        input_data=current,
                        parameters=node.parameters,
                        runtime_backend=node.runtime_backend,
                    )
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
                        break
            except Exception as exc:
                decision = "failed"
                reason_codes.append(f"OPERATOR_ERROR:{type(exc).__name__}")
                current = current.model_copy(
                    update={"labels": {**current.labels, "execution_error": str(exc)}}
                )

            output_relative_path: str | None = None
            output_hash: str | None = None
            if decision == "keep":
                source_output = Path(current.current_path).resolve()
                destination = staging_files / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_output, destination)
                output_relative_path = relative_path.as_posix()
                output_hash = _sha256(destination)
            if _sha256(source) != source_hash:
                raise RuntimeError(f"Source image changed during execution: {source}")
            self.run_store.add_item(
                run["id"],
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

        completed = self.run_store.items(run["id"])
        if not any(item["decision"] == "keep" for item in completed):
            raise RuntimeError("Run produced an empty dataset; publication was blocked")
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
        for item in items:
            labels = dict(item["labels"])
            operator_outputs = labels.pop(_OPERATOR_OUTPUTS_KEY, {})
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
                artifacts=tuple(
                    published_artifact(value)
                    for value in operator_outputs.get("artifacts", ())
                ),
                annotations=tuple(
                    AnnotationRef.model_validate(value)
                    for value in operator_outputs.get("annotations", ())
                ),
                embeddings=tuple(
                    EmbeddingRef.model_validate(value)
                    for value in operator_outputs.get("embeddings", ())
                ),
            ))
        assets_tuple = tuple(assets)
        dataset = DatasetVersion(
            id=dataset_id,
            version=1,
            created_by=run["owner_id"],
            change_reason="approved pipeline dataset run",
            work_order_id=run["work_order_id"],
            pipeline_version_id=pipeline.id,
            task_spec_version_id=spec.id,
            run_id=run["id"],
            source_roots=tuple(str(root) for root in roots),
            manifest_uri=str(manifest_path.resolve()),
            assets=assets_tuple,
            source_count=len(assets_tuple),
            kept_count=sum(item.decision == "keep" for item in assets_tuple),
            rejected_count=sum(item.decision == "reject" for item in assets_tuple),
            failed_count=sum(item.decision == "failed" for item in assets_tuple),
            original_files_unchanged=True,
        )
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(manifest_path)
        self.version_store.save_if_absent(
            kind="dataset", owner_id=run["owner_id"], payload=dataset.model_dump(mode="json")
        )
        return dataset
