from __future__ import annotations

from ...domain.common import new_id
from ...domain.pipelines import (
    PipelineEdge,
    PipelineNode,
    PipelineStrategy,
    PipelineVersion,
)
from ...domain.specs import TaskSpecVersion
from ...domain.operators import ExecutionScope, OperatorCategory, RuntimeBackend
from ...operators import OperatorLibrary, build_operator_library
from ...operators.catalog_matching import OperatorCatalogMatch
from ...operators.planning import (
    MODEL_CAPABILITY_REQUIREMENTS,
    OperatorRequirement,
    OperatorSelector,
)
from ..shared import WorkOrderGraphState, append_trace


STRATEGY_THRESHOLDS: dict[PipelineStrategy, tuple[float, float]] = {
    PipelineStrategy.RETENTION_FIRST: (0.35, 0.45),
    PipelineStrategy.BALANCED: (0.55, 0.65),
    PipelineStrategy.QUALITY_FIRST: (0.75, 0.85),
}


_BASE_REQUIREMENTS = (
    (
        "ingest",
        OperatorRequirement(
            capability="decode",
            category=OperatorCategory.INGESTION,
            secondary_category="decoding",
            runtime_backend=RuntimeBackend.CPU,
        ),
        {},
        True,
    ),
    (
        "filter",
        OperatorRequirement(
            capability="quality",
            category=OperatorCategory.FILTERING,
            secondary_category="image_quality",
            runtime_backend=RuntimeBackend.CPU,
        ),
        None,
        True,
    ),
    (
        "deduplicate",
        OperatorRequirement(
            capability="deduplication",
            category=OperatorCategory.DEDUPLICATION,
            secondary_category="perceptual_duplicate",
            runtime_backend=RuntimeBackend.CPU,
        ),
        None,
        False,
    ),
    (
        "manifest",
        OperatorRequirement(
            capability="manifest",
            category=OperatorCategory.OUTPUT,
            secondary_category="manifest",
            runtime_backend=RuntimeBackend.CPU,
        ),
        {},
        True,
    ),
)


def _compile_nodes(
    spec: TaskSpecVersion,
    threshold: float,
    *,
    operator_library: OperatorLibrary | None = None,
    operator_candidates: list[dict] | None = None,
) -> tuple[PipelineNode, ...]:
    library = operator_library or build_operator_library(include_datajuicer=False)
    selector = OperatorSelector(library.registry)
    base_nodes: dict[str, PipelineNode] = {}
    for node_id, requirement, parameters, required in _BASE_REQUIREMENTS:
        operator = selector.select(requirement)
        resolved_parameters = parameters
        if node_id == "filter":
            resolved_parameters = {"confidence_threshold": threshold}
        elif node_id == "deduplicate":
            resolved_parameters = {"distance_threshold": max(1, round(threshold * 10))}
        base_nodes[node_id] = PipelineNode(
            id=node_id,
            operator_version_id=operator.id,
            name=operator.display_name,
            category=operator.primary_category.value,
            parameters=resolved_parameters or {},
            runtime_backend=requirement.runtime_backend or RuntimeBackend.CPU,
            required=required,
        )

    matches = [
        OperatorCatalogMatch.model_validate(item)
        for item in (operator_candidates or [])
        if item.get("executable")
    ]
    candidate_nodes: list[tuple[OperatorCatalogMatch, PipelineNode]] = []
    for match in matches:
        operator = library.registry.get(match.operator_version_id)
        candidate_nodes.append(
            (
                match,
                PipelineNode(
                    id=f"datajuicer_{match.intent}",
                    operator_version_id=operator.id,
                    name=operator.display_name,
                    category=operator.primary_category.value,
                    parameters=match.parameters,
                    runtime_backend=match.runtime_backend,
                    required=True,
                ),
            )
        )

    selected: list[PipelineNode] = [base_nodes["ingest"]]
    selected.extend(
        node
        for match, node in candidate_nodes
        if library.registry.get(match.operator_version_id).execution_scope
        == ExecutionScope.DATASET
    )
    covered_capabilities = {match.intent for match, _ in candidate_nodes}
    for capability in spec.required_capabilities:
        if capability in covered_capabilities:
            continue
        requirement = MODEL_CAPABILITY_REQUIREMENTS.get(capability)
        if requirement is None:
            raise ValueError(f"No operator requirement is registered for: {capability}")
        operator = selector.select(requirement)
        selected.append(
            PipelineNode(
                id=f"understand_{capability}",
                operator_version_id=operator.id,
                name=operator.display_name,
                category=operator.primary_category.value,
                runtime_backend=RuntimeBackend.MOCK,
                required=True,
            ),
        )
    selected.extend(
        node
        for match, node in candidate_nodes
        if library.registry.get(match.operator_version_id).execution_scope
        == ExecutionScope.ASSET
        and library.registry.get(match.operator_version_id).primary_category
        == OperatorCategory.FILTERING
    )
    selected.append(base_nodes["filter"])
    selected.extend(
        node
        for match, node in candidate_nodes
        if library.registry.get(match.operator_version_id).execution_scope
        == ExecutionScope.ASSET
        and library.registry.get(match.operator_version_id).primary_category
        != OperatorCategory.FILTERING
    )
    if not any(
        library.registry.get(match.operator_version_id).primary_category
        == OperatorCategory.DEDUPLICATION
        for match, _ in candidate_nodes
    ):
        selected.append(base_nodes["deduplicate"])
    selected.append(base_nodes["manifest"])
    return tuple(selected)


def _build_variant(
    *,
    strategy: PipelineStrategy,
    threshold: float,
    variant_index: int,
    spec: TaskSpecVersion,
    owner_id: str,
    operator_library: OperatorLibrary | None = None,
    operator_candidates: list[dict] | None = None,
) -> PipelineVersion:
    family_id = f"pipeline_{strategy.value}"
    nodes = _compile_nodes(
        spec,
        threshold,
        operator_library=operator_library,
        operator_candidates=operator_candidates,
    )
    edges = tuple(
        PipelineEdge(source=source.id, target=target.id)
        for source, target in zip(nodes, nodes[1:])
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


def generate_pipeline_variants(
    state: WorkOrderGraphState,
    *,
    operator_library: OperatorLibrary | None = None,
) -> dict:
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
                    operator_library=operator_library,
                    operator_candidates=state.get("operator_candidates", []),
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
