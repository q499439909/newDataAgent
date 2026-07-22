from __future__ import annotations

from typing import Any

from ...domain.common import new_id
from ...domain.operators import RuntimeBackend
from ...domain.pipelines import (
    PipelineEdge,
    PipelineNode,
    PipelineStrategy,
    PipelineVersion,
    PromptBinding,
)
from ...domain.plans import CapabilityCoverage, CapabilityCoverageStatus
from ...domain.specs import TaskSpecVersion
from ...operators import OperatorLibrary, build_operator_library
from ...operators.catalog_matching import OperatorCatalogMatch
from ...operators.validation import validate_parameters
from ...prompts import builtin_prompt_registry
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

def _classification_contract(task_spec: TaskSpecVersion) -> dict[str, Any]:
    classification = task_spec.classification
    if classification is None:
        return {
            "labels": [
                {"id": "cat", "aliases": ["cat", "kitten", "feline"]},
                {"id": "dog", "aliases": ["dog", "puppy", "canine"]},
            ],
            "mixed_label": "mixed",
            "unknown_label": "unknown",
        }
    return {
        "labels": [
            {
                "id": label.id,
                "aliases": list(dict.fromkeys((label.id, label.display_name, *label.aliases))),
            }
            for label in classification.labels
        ],
        "mixed_label": classification.mixed_label,
        "unknown_label": classification.unknown_label,
    }


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


def _vlm_configuration(
    operator_id: str,
    *,
    purpose: str,
    task_spec: TaskSpecVersion,
    library: OperatorLibrary,
    candidates: dict[str, OperatorCatalogMatch],
) -> tuple[dict[str, Any], PromptBinding | None]:
    parameters = dict(candidates.get(operator_id).parameters if operator_id in candidates else {})
    properties = library.registry.get(operator_id).parameter_schema.get("properties", {})
    if "tag_field_name" in properties:
        parameters["tag_field_name"] = (
            "authenticity_tags" if purpose == "authenticity" else "image_tags"
        )
    if "system_prompt" in properties:
        if purpose == "authenticity":
            scope = task_spec.hard_constraints.get("authenticity_scope")
            exclusions = "; ".join(task_spec.exclusion_requirements)
            task_scope = "; ".join(str(item) for item in (scope, exclusions) if item)
            resolved = builtin_prompt_registry().resolve(
                "image-authenticity",
                1,
                variables={
                    "task_scope_instruction": (
                        f" Apply this task-specific exclusion scope: {task_scope}"
                        if task_scope
                        else ""
                    )
                },
            )
        else:
            contract = _classification_contract(task_spec)
            allowed = [
                *(item["id"] for item in contract["labels"]),
                contract["mixed_label"],
                contract["unknown_label"],
            ]
            resolved = builtin_prompt_registry().resolve(
                "closed-set-image-classification",
                1,
                variables={"allowed_labels": ", ".join(allowed)},
            )
        parameters["system_prompt"] = resolved.text
        return parameters, resolved.binding
    return parameters, None


def _node(
    *,
    node_id: str,
    operator_id: str,
    parameters: dict[str, Any],
    library: OperatorLibrary,
    candidates: dict[str, OperatorCatalogMatch],
    prompt_binding: PromptBinding | None = None,
) -> PipelineNode:
    operator = library.registry.get(operator_id)
    normalized_parameters = validate_parameters(
        operator.parameter_schema,
        parameters,
    )
    return PipelineNode(
        id=node_id,
        operator_version_id=operator_id,
        name=operator.display_name,
        category=operator.primary_category.value,
        parameters=normalized_parameters,
        runtime_backend=_runtime_for(
            operator_id, library=library, candidates=candidates
        ),
        required=True,
        prompt_binding=prompt_binding,
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
    task_spec: TaskSpecVersion,
) -> dict[str, Any]:
    if capability == "image_quality":
        return {"confidence_threshold": policy["quality_threshold"]}
    if capability == "authenticity_assessment":
        return {"uncertain_policy": policy["uncertain_policy"]}
    if capability == "class_resolution":
        return {
            "mixed_policy": policy["mixed_policy"],
            "unknown_policy": policy["unknown_policy"],
            **_classification_contract(task_spec),
        }
    if capability == "perceptual_deduplication":
        return {"distance_threshold": policy["dedup_distance"]}
    if capability == "dataset_partition":
        contract = _classification_contract(task_spec)
        return {
            "directory_prefix": "classes",
            "allowed_labels": [item["id"] for item in contract["labels"]],
            "mixed_label": contract["mixed_label"],
            "unknown_label": contract["unknown_label"],
        }
    return {}


def _compile_nodes(
    state: WorkOrderGraphState,
    policy: dict[str, Any],
    *,
    operator_library: OperatorLibrary | None = None,
) -> tuple[PipelineNode, ...]:
    library = operator_library or build_operator_library(include_datajuicer=False)
    task_spec = TaskSpecVersion.model_validate(state["task_spec"])
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
            vlm_parameters, prompt_binding = _vlm_configuration(
                vlm_id,
                purpose="authenticity",
                task_spec=task_spec,
                library=library,
                candidates=candidates,
            )
            nodes.append(
                _node(
                    node_id="authenticity_tagging",
                    operator_id=vlm_id,
                    parameters=vlm_parameters,
                    library=library,
                    candidates=candidates,
                    prompt_binding=prompt_binding,
                )
            )

        base_node_id = _NODE_IDS.get(capability, f"capability_{capability}")
        used_node_ids[base_node_id] = used_node_ids.get(base_node_id, 0) + 1
        node_id = (
            base_node_id
            if used_node_ids[base_node_id] == 1
            else f"{base_node_id}_{used_node_ids[base_node_id]}"
        )
        parameters = _parameters_for_capability(capability, policy, task_spec)
        if operator_id in candidates:
            parameters = dict(candidates[operator_id].parameters)
        prompt_binding = None
        if capability == "image_classification" and "visual_understanding" in operator.capability_tags:
            parameters, prompt_binding = _vlm_configuration(
                operator_id,
                purpose="classification",
                task_spec=task_spec,
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
                prompt_binding=prompt_binding,
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
