from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from ..domain.common import new_id
from ..domain.pipelines import NodePreviewItem, NodePreviewSet, PipelineVersion
from ..operators.protocol import OperatorContext, OperatorInput
from ..operators.runtime import OperatorRuntime


class NodePreviewBuilder:
    def __init__(self, runtime: OperatorRuntime, preview_root: Path):
        self.runtime = runtime
        self.preview_root = preview_root.resolve()
        self.preview_root.mkdir(parents=True, exist_ok=True)

    def build(
        self,
        *,
        pipeline: PipelineVersion,
        source_path: Path,
        owner_id: str,
        work_order_id: str,
        run_id: str,
    ) -> NodePreviewSet:
        self.runtime.validate_pipeline(pipeline)
        source_path = source_path.resolve()
        asset_id = hashlib.sha256(source_path.read_bytes()).hexdigest()
        context = OperatorContext(
            run_id=run_id,
            work_order_id=work_order_id,
            owner_id=owner_id,
            purpose="preview",
            shared={
                "artifact_root": self.preview_root
                / pipeline.id
                / asset_id
                / "artifacts"
            },
        )
        current = OperatorInput(source_path=str(source_path), current_path=str(source_path))
        items: list[NodePreviewItem] = []
        for node in self._ordered_nodes(pipeline):
            input_preview = self._thumbnail(
                Path(current.current_path), pipeline.id, asset_id, f"{node.id}-input"
            )
            result = self.runtime.execute(
                operator_version_id=node.operator_version_id,
                context=context,
                input_data=current,
                parameters=node.parameters,
                runtime_backend=node.runtime_backend,
            )
            output_path = Path(result.output_path) if result.output_path else Path(current.current_path)
            output_preview = self._thumbnail(
                output_path, pipeline.id, asset_id, f"{node.id}-output"
            )
            items.append(
                NodePreviewItem(
                    node_id=node.id,
                    operator_version_id=node.operator_version_id,
                    source_asset_id=asset_id,
                    input_preview_uri=str(input_preview),
                    output_preview_uri=str(output_preview),
                    metrics_before=current.metrics,
                    metrics_after=result.metrics,
                    labels_before=current.labels,
                    labels_after=result.labels,
                    artifacts=tuple(result.artifacts),
                    annotations=tuple(result.annotations),
                    embeddings=tuple(result.embeddings),
                    decision=result.decision,
                    reason_codes=tuple(result.reason_codes),
                    confidence=result.confidence,
                    run_id=run_id,
                )
            )
            current = OperatorInput(
                source_path=current.source_path,
                current_path=str(output_path),
                metrics=result.metrics,
                labels=result.labels,
                artifacts=result.artifacts,
                annotations=result.annotations,
                embeddings=result.embeddings,
            )
            if result.decision == "reject":
                break
        return NodePreviewSet(
            id=new_id("node_preview_set"),
            version=1,
            created_by=owner_id,
            change_reason="pipeline trial preview",
            pipeline_version_id=pipeline.id,
            items=tuple(items),
        )

    @staticmethod
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

    def _thumbnail(
        self, source: Path, pipeline_id: str, asset_id: str, label: str
    ) -> Path:
        target = self.preview_root / pipeline_id / asset_id / f"{label}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            preview = ImageOps.exif_transpose(image).convert("RGB")
            preview.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            preview.save(target, "JPEG", quality=88, optimize=True)
        return target.resolve()
