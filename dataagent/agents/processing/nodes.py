from __future__ import annotations

from typing import Any

from ...domain.common import new_id
from ...domain.operators import RuntimeBackend
from ...domain.pipelines import (
    PipelineEdge,
    PipelineNode,
    PipelineStrategy,
    PipelineVersion,
)
from ...domain.plans import CapabilityCoverage, CapabilityCoverageStatus
from ...domain.specs import TaskSpecVersion
from ...operators import OperatorLibrary, build_operator_library
from ...operators.catalog_matching import OperatorCatalogMatch
from ..shared import WorkOrderGraphState, append_trace


STRATEGY_POLICIES: dict[PipelineStrategy, dict[str, Any]] = {
    PipelineStrategy.RETENTION_FIRST: {
        "quality_threshold": 0.35,
        "uncertain_policy": "keep",
        "mixed_policy": "keep",
        "unknown_policy": "keep",
        "dedup_distance": 4,
    },
    PipelineStrategy.BALANCED: {
        "quality_threshold": 0.55,
        "uncertain_policy": "review",
        "mixed_policy": "review",
        "unknown_policy": "review",
        "dedup_distance": 2,
    },
    PipelineStrategy.QUALITY_FIRST: {
        "quality_threshold": 0.75,
        "uncertain_policy": "reject",
        "mixed_policy": "reject",
        "unknown_policy": "reject",
        "dedup_distance": 1,
    },
}


_LEGACY_DEFAULT_CAPABILITIES = (
    ("image_decode", "builtin.decode_check:1"),
    ("image_quality", "builtin.quality_filter:1"),
    ("perceptual_deduplication", "builtin.perceptual_dedup:1"),
    ("manifest", "builtin.manifest:1"),
)

_NODE_IDS = {
    "image_decode": "ingest",
    "image_quality": "quality_filter",
    "authenticity_assessment": "authenticity_decision",
    "image_classification": "image_classification",
    "class_resolution": "class_resolution",
    "dataset_partition": "dataset_partition",
    "perceptual_deduplication": "deduplicate",
    "manifest": "manifest",
}

_AUTHENTICITY_PROMPT = (
    "Judge whether the image is an authentic natural photograph or a synthetic "
    "AI-generated image. Return concise tags including exactly one of: "
    "authentic, synthetic, uncertain."
)
_CLASSIFICATION_PROMPT = (
    "Classify the visible animals for dataset partitioning. Return concise tags "
    "including exactly one of: cat, dog, mixed, unknown."
)


def _candidate_map(state: WorkOrderGraphState) -> dict[str, OperatorCatalogMatch]:
    return {
        item.operator_version_id: item
        for item in (
            OperatorCatalogMatch.model_validate(payload)
            for payload in state.get("operator_candidates", ())
        )
        if item.executable
    }


def _coverage_selection(
    state: WorkOrderGraphState,
) -> list[tuple[str, str]]:
    coverage = [
        CapabilityCoverage.model_validate(item)
        for item in state.get("capability_coverage", ())
    ]
    if not coverage:
        spec = TaskSpecVersion.model_validate(state["task_spec"])
        if spec.capability_requirements or spec.required_capabilities:
            raise ValueError(
                "Capability coverage is required before compiling requested capabilities"
            )
        return list(_LEGACY_DEFAULT_CAPABILITIES)
    gaps = [
        item.capability
        for item in coverage
        if item.required and item.status != CapabilityCoverageStatus.COVERED
    ]
    if gaps:
        raise ValueError(
            "Cannot compile pipelines with uncovered capabilities: "
            + ", ".join(gaps)
        )
    return [
        (item.capability, str(item.selected_operator_version_id))
        for item in coverage
        if item.selected_operator_version_id
    ]


def _runtime_for(
    operator_id: str,
    *,
    library: OperatorLibrary,
    candidates: dict[str, OperatorCatalogMatch],
) -> RuntimeBackend:
    if operator_id in candidates:
        return candidates[operator_id].runtime_backend
    supported = {
        profile.backend
        for profile in library.registry.get(operator_id).supported_runtime_profiles
    }
    return RuntimeBackend.CPU if RuntimeBackend.CPU in supported else next(iter(supported))


def _vlm_parameters(
    operator_id: str,
    *,
    purpose: str,
    library: OperatorLibrary,
    candidates: dict[str, OperatorCatalogMatch],
) -> dict[str, Any]:
    parameters = dict(candidates.get(operator_id).parameters if operator_id in candidates else {})
    properties = library.registry.get(operator_id).parameter_schema.get("properties", {})
    if "tag_field_name" in properties:
        parameters["tag_field_name"] = (
            "authenticity_tags" if purpose == "authenticity" else "image_tags"
        )
    if "system_prompt" in properties:
        parameters["system_prompt"] = (
            _AUTHENTICITY_PROMPT
            if purpose == "authenticity"
            else _CLASSIFICATION_PROMPT
        )
    return parameters


def _node(
    *,
    node_id: str,
    operator_id: str,
    parameters: dict[str, Any],
    library: OperatorLibrary,
    candidates: dict[str, OperatorCatalogMatch],
) -> PipelineNode:
    operator = library.registry.get(operator_id)
    return PipelineNode(
        id=node_id,
        operator_version_id=operator_id,
        name=operator.display_name,
        category=operator.primary_category.value,
        parameters=parameters,
        runtime_backend=_runtime_for(
            operator_id, library=library, candidates=candidates
        ),
        required=True,
    )


def _remote_visual_operator(
    *,
    library: OperatorLibrary,
    candidates: dict[str, OperatorCatalogMatch],
) -> str:
    eligible = [
        operator_id
        for operator_id, candidate in candidates.items()
        if candidate.runtime_backend == RuntimeBackend.REMOTE
        and "visual_understanding"
        in library.registry.get(operator_id).capability_tags
    ]
    if not eligible:
        raise ValueError(
            "Authenticity assessment requires an executable remote visual operator"
        )
    return sorted(eligible)[0]


def _parameters_for_capability(
    capability: str,
    policy: dict[str, Any],
) -> dict[str, Any]:
    if capability == "image_quality":
        return {"confidence_threshold": policy["quality_threshold"]}
    if capability == "authenticity_assessment":
        return {"uncertain_policy": policy["uncertain_policy"]}
    if capability == "class_resolution":
        return {
            "mixed_policy": policy["mixed_policy"],
            "unknown_policy": policy["unknown_policy"],
        }
    if capability == "perceptual_deduplication":
        return {"distance_threshold": policy["dedup_distance"]}
    if capability == "dataset_partition":
        return {"directory_prefix": "classes"}
    return {}


def _compile_nodes(
    state: WorkOrderGraphState,
    policy: dict[str, Any],
    *,
    operator_library: OperatorLibrary | None = None,
) -> tuple[PipelineNode, ...]:
    library = operator_library or build_operator_library(include_datajuicer=False)
    candidates = _candidate_map(state)
    selection = _coverage_selection(state)
    nodes: list[PipelineNode] = []
    used_node_ids: dict[str, int] = {}

    for capability, operator_id in selection:
        operator = library.registry.get(operator_id)
        upstream_tags = tuple(
            operator.resource_requirements.get("upstream_capability_tags", ())
        )
        if capability == "authenticity_assessment" and "visual_understanding" in upstream_tags:
            vlm_id = _remote_visual_operator(library=library, candidates=candidates)
            nodes.append(
                _node(
                    node_id="authenticity_tagging",
                    operator_id=vlm_id,
                    parameters=_vlm_parameters(
                        vlm_id,
                        purpose="authenticity",
                        library=library,
                        candidates=candidates,
                    ),
                    library=library,
                    candidates=candidates,
                )
            )

        base_node_id = _NODE_IDS.get(capability, f"capability_{capability}")
        used_node_ids[base_node_id] = used_node_ids.get(base_node_id, 0) + 1
        node_id = (
            base_node_id
            if used_node_ids[base_node_id] == 1
            else f"{base_node_id}_{used_node_ids[base_node_id]}"
        )
        parameters = _parameters_for_capability(capability, policy)
        if operator_id in candidates:
            parameters = dict(candidates[operator_id].parameters)
        if capability == "image_classification" and "visual_understanding" in operator.capability_tags:
            parameters = _vlm_parameters(
                operator_id,
                purpose="classification",
                library=library,
                candidates=candidates,
            )
        nodes.append(
            _node(
                node_id=node_id,
                operator_id=operator_id,
                parameters=parameters,
                library=library,
                candidates=candidates,
            )
        )
    return tuple(nodes)


def _build_pipeline(
    *,
    strategy: PipelineStrategy,
    state: WorkOrderGraphState,
    spec: TaskSpecVersion,
    owner_id: str,
    operator_library: OperatorLibrary | None,
) -> PipelineVersion:
    nodes = _compile_nodes(
        state,
        STRATEGY_POLICIES[strategy],
        operator_library=operator_library,
    )
    edges = tuple(
        PipelineEdge(source=source.id, target=target.id)
        for source, target in zip(nodes, nodes[1:])
    )
    return PipelineVersion(
        id=new_id("pipeline_version"),
        family_id=f"pipeline_{strategy.value}",
        version=1,
        created_by=owner_id,
        change_reason=f"compiled {strategy.value} capability-complete pipeline",
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
    variants = [
        _build_pipeline(
            strategy=strategy,
            state=state,
            spec=spec,
            owner_id=state["owner_id"],
            operator_library=operator_library,
        ).model_dump(mode="json")
        for strategy in PipelineStrategy
    ]
    return {
        "pipeline_variants": variants,
        "current_agent": "processing",
        "trace": append_trace(state, "processing:variants_generated"),
    }


def select_representative_pipelines(state: WorkOrderGraphState) -> dict:
    variants = [PipelineVersion.model_validate(item) for item in state["pipeline_variants"]]
    if {item.strategy for item in variants} != set(PipelineStrategy):
        raise ValueError("Exactly one compiled pipeline is required for each strategy")
    return {
        "representative_pipelines": [item.model_dump(mode="json") for item in variants],
        "next_action": "approve_pipeline",
        "trace": append_trace(state, "processing:representatives_selected"),
    }
