from __future__ import annotations

from ...domain.common import new_id
from ...domain.pipelines import (
    PipelineEdge,
    PipelineNode,
    PipelineStrategy,
    PipelineVersion,
)
from ...domain.specs import TaskSpecVersion
from ..shared import WorkOrderGraphState, append_trace


STRATEGY_THRESHOLDS: dict[PipelineStrategy, tuple[float, float]] = {
    PipelineStrategy.RETENTION_FIRST: (0.35, 0.45),
    PipelineStrategy.BALANCED: (0.55, 0.65),
    PipelineStrategy.QUALITY_FIRST: (0.75, 0.85),
}


def _build_variant(
    *,
    strategy: PipelineStrategy,
    threshold: float,
    variant_index: int,
    spec: TaskSpecVersion,
    owner_id: str,
) -> PipelineVersion:
    family_id = f"pipeline_{strategy.value}"
    nodes = (
        PipelineNode(
            id="ingest",
            operator_version_id="builtin.decode_check:1",
            name="解码与元数据",
            category="INGESTION",
            required=True,
        ),
        PipelineNode(
            id="filter",
            operator_version_id="builtin.quality_filter:1",
            name="质量与规则过滤",
            category="FILTERING",
            parameters={"confidence_threshold": threshold},
            required=True,
        ),
        PipelineNode(
            id="deduplicate",
            operator_version_id="builtin.perceptual_dedup:1",
            name="感知去重",
            category="DEDUPLICATION",
            parameters={"distance_threshold": max(1, round(threshold * 10))},
        ),
        PipelineNode(
            id="manifest",
            operator_version_id="builtin.manifest:1",
            name="生成 Manifest",
            category="OUTPUT",
            required=True,
        ),
    )
    edges = (
        PipelineEdge(source="ingest", target="filter"),
        PipelineEdge(source="filter", target="deduplicate"),
        PipelineEdge(source="deduplicate", target="manifest"),
    )
    return PipelineVersion(
        id=new_id("pipeline_version"),
        family_id=family_id,
        version=variant_index + 1,
        created_by=owner_id,
        change_reason=f"{strategy.value} internal variant {variant_index + 1}",
        strategy=strategy,
        task_spec_version_id=spec.id,
        nodes=nodes,
        edges=edges,
        created_from="processing_agent",
    )


def generate_pipeline_variants(state: WorkOrderGraphState) -> dict:
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    variants = []
    for strategy, thresholds in STRATEGY_THRESHOLDS.items():
        for index, threshold in enumerate(thresholds):
            variants.append(
                _build_variant(
                    strategy=strategy,
                    threshold=threshold,
                    variant_index=index,
                    spec=spec,
                    owner_id=state["owner_id"],
                ).model_dump(mode="json")
            )
    return {
        "pipeline_variants": variants,
        "current_agent": "processing",
        "trace": append_trace(state, "processing:variants_generated"),
    }


def select_representative_pipelines(state: WorkOrderGraphState) -> dict:
    variants = [PipelineVersion.model_validate(item) for item in state["pipeline_variants"]]
    representatives = []
    for strategy in PipelineStrategy:
        group = [item for item in variants if item.strategy == strategy]
        # Until real experiment metrics are attached, the first valid variant is conservative.
        representatives.append(group[0].model_dump(mode="json"))
    return {
        "representative_pipelines": representatives,
        "next_action": "approve_pipeline",
        "trace": append_trace(state, "processing:representatives_selected"),
    }
