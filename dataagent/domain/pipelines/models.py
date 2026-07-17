from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from ..common.models import DomainModel, VersionedModel
from ..operators import AnnotationRef, AssetRef, EmbeddingRef, RuntimeBackend


class PipelineStrategy(StrEnum):
    RETENTION_FIRST = "retention_first"
    BALANCED = "balanced"
    QUALITY_FIRST = "quality_first"


class PipelineNode(DomainModel):
    id: str
    operator_version_id: str
    name: str
    category: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    runtime_backend: RuntimeBackend = RuntimeBackend.CPU
    required: bool = False


class PipelineEdge(DomainModel):
    source: str
    target: str
    condition: str | None = None


class PipelineVersion(VersionedModel):
    family_id: str
    strategy: PipelineStrategy
    task_spec_version_id: str
    nodes: tuple[PipelineNode, ...]
    edges: tuple[PipelineEdge, ...] = ()
    created_from: str
    run_id: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    approved: bool = False

    @model_validator(mode="after")
    def validate_graph(self) -> "PipelineVersion":
        if not self.nodes:
            raise ValueError("Pipeline must contain at least one node")
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Pipeline node ids must be unique")
        known = set(node_ids)
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError("Pipeline edge references an unknown node")
            if edge.source == edge.target:
                raise ValueError("Pipeline nodes cannot connect to themselves")
        self._assert_acyclic(known)
        return self

    def _assert_acyclic(self, known: set[str]) -> None:
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in known}
        indegree = {node_id: 0 for node_id in known}
        for edge in self.edges:
            if edge.target not in adjacency[edge.source]:
                adjacency[edge.source].add(edge.target)
                indegree[edge.target] += 1
        ready = [node_id for node_id, degree in indegree.items() if degree == 0]
        visited = 0
        while ready:
            current = ready.pop()
            visited += 1
            for target in adjacency[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if visited != len(known):
            raise ValueError("Pipeline graph must be acyclic")

    def branch(
        self,
        *,
        new_id: str,
        actor: str,
        reason: str,
        nodes: tuple[PipelineNode, ...] | None = None,
        edges: tuple[PipelineEdge, ...] | None = None,
    ) -> "PipelineVersion":
        return self.model_copy(
            update={
                "id": new_id,
                "version": self.version + 1,
                "parent_version_id": self.id,
                "created_by": actor,
                "change_reason": reason,
                "nodes": nodes if nodes is not None else self.nodes,
                "edges": edges if edges is not None else self.edges,
                "approved": False,
                "run_id": None,
                "metrics": {},
            }
        )


class NodePreviewItem(DomainModel):
    node_id: str
    operator_version_id: str
    source_asset_id: str
    input_preview_uri: str
    output_preview_uri: str | None = None
    overlay_preview_uri: str | None = None
    metrics_before: dict[str, Any] = Field(default_factory=dict)
    metrics_after: dict[str, Any] = Field(default_factory=dict)
    labels_before: dict[str, Any] = Field(default_factory=dict)
    labels_after: dict[str, Any] = Field(default_factory=dict)
    artifacts: tuple[AssetRef, ...] = ()
    annotations: tuple[AnnotationRef, ...] = ()
    embeddings: tuple[EmbeddingRef, ...] = ()
    decision: str
    reason_codes: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)
    run_id: str


class NodePreviewSet(VersionedModel):
    pipeline_version_id: str
    items: tuple[NodePreviewItem, ...]

    @model_validator(mode="after")
    def require_items(self) -> "NodePreviewSet":
        if not self.items:
            raise ValueError("A node preview set must contain at least one item")
        return self
