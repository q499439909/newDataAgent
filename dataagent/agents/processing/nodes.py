from __future__ import annotations

from typing import Any

from ...domain.common import new_id
from ...domain.operators import OperatorCategory, RuntimeBackend
from ...domain.pipelines import (
    ConstraintCoverage,
    PipelineEdge,
    PipelineNode,
    PipelineStrategy,
    PipelineVersion,
    PromptBinding,
)
from ...domain.plans import (
    CapabilityCoverage,
    CapabilityCoverageStatus,
    OperatorPlanVersion,
)
from ...domain.specs import TaskSpecVersion
from ...experiences import PipelineExperienceMatch, PipelineExperienceRetriever
from ...operators import OperatorLibrary, build_operator_library
from ...operators.catalog_matching import OperatorCatalogMatch
from ...operators.validation import validate_parameters
from ...prompts import builtin_prompt_registry
from ..runner import AgentPlanner, AgentRunner, AgentTool
from ..shared import WorkOrderGraphState, append_trace
from ...domain.specs.binding import bind_constraint_parameters
from ...execution.pipeline_trial import PipelineTrialRunner


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
    "visual_semantic_selection": "visual_semantic_selection",
    "image_classification": "image_classification",
    "class_resolution": "class_resolution",
    "dataset_partition": "dataset_partition",
    "perceptual_deduplication": "deduplicate",
    "image_shape": "image_shape",
    "aspect_ratio": "aspect_ratio",
    "file_size": "file_size",
    "face_count": "face_count",
    "manifest": "manifest",
}

def _classification_contract(task_spec: TaskSpecVersion) -> dict[str, Any]:
    classification = task_spec.classification
    if classification is None:
        return {
            "labels": [],
            "mixed_label": None,
            "unknown_label": None,
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


def _require_classification_contract(
    task_spec: TaskSpecVersion,
    *,
    capability: str,
) -> dict[str, Any]:
    contract = _classification_contract(task_spec)
    if not contract["labels"]:
        raise ValueError(
            f"Capability {capability} requires TaskSpec.classification; "
            "task labels must not be inferred by the pipeline compiler"
        )
    return contract


def _classification_label_ids(contract: dict[str, Any]) -> list[str]:
    return [
        value
        for value in (
            *(item["id"] for item in contract["labels"]),
            contract["mixed_label"],
            contract["unknown_label"],
        )
        if isinstance(value, str) and value
    ]


def _classification_prompt_context(task_spec: TaskSpecVersion) -> list[dict[str, Any]]:
    classification = task_spec.classification
    if classification is None:
        return []
    return [
        {
            "id": label.id,
            "display_name": label.display_name,
            "aliases": list(label.aliases),
        }
        for label in classification.labels
    ]


def _semantic_selection_variables(task_spec: TaskSpecVersion) -> dict[str, Any]:
    semantic_objective = (
        "Apply the confirmed visual semantic selection contract."
        if task_spec.constraints
        else task_spec.objective
    )
    return {
        "task_objective": semantic_objective,
        "semantic_requirements": (
            "; ".join(task_spec.semantic_requirements) or task_spec.objective
        ),
        "exclusion_requirements": (
            "; ".join(task_spec.exclusion_requirements) or "none"
        ),
        "classification_contract": _classification_prompt_context(task_spec),
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
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    coverage = [
        CapabilityCoverage.model_validate(item)
        for item in state.get("capability_coverage", ())
    ]
    if not coverage:
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
    constraint_order = {
        constraint.id: index for index, constraint in enumerate(spec.constraints)
    }

    def priority(item: CapabilityCoverage) -> tuple[int, int]:
        if item.capability == "image_decode":
            return (-2, 0)
        if item.capability_id in constraint_order:
            return (0, constraint_order[item.capability_id])
        if item.capability == "manifest":
            return (2, 0)
        return (1, 0)

    selected: list[tuple[str, str]] = []
    seen_operator_ids: set[str] = set()
    for item in sorted(coverage, key=priority):
        if not item.selected_operator_version_id:
            continue
        operator_id = str(item.selected_operator_version_id)
        if operator_id in seen_operator_ids:
            continue
        seen_operator_ids.add(operator_id)
        selected.append((item.capability, operator_id))
    return selected


def _node_id_for_operator(
    capability: str,
    operator_id: str,
    library: OperatorLibrary,
) -> str:
    if capability in _NODE_IDS:
        return _NODE_IDS[capability]
    reference = library.registry.get(operator_id).provider.provider_operator_ref
    normalized = reference.rsplit(".", 1)[-1]
    for suffix in ("_filter", "_mapper", "_operator"):
        normalized = normalized.removesuffix(suffix)
    for known_capability, node_id in sorted(
        _NODE_IDS.items(), key=lambda item: -len(item[0])
    ):
        if known_capability in normalized:
            return node_id
    return normalized


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


def _vlm_tag_groups(
    purpose: str,
    task_spec: TaskSpecVersion,
) -> list[list[str]]:
    if purpose == "authenticity":
        return [["authentic", "synthetic", "uncertain"]]
    if purpose == "classification":
        return [
            _classification_label_ids(
                _require_classification_contract(
                    task_spec,
                    capability="image_classification",
                )
            )
        ]
    if purpose == "semantic_selection":
        return [
            [
                "semantic_match",
                "semantic_mismatch",
                "semantic_uncertain",
            ]
        ]

    requested = {
        item.capability for item in task_spec.capability_requirements
    }.union(task_spec.required_capabilities)
    groups: list[list[str]] = []
    if "authenticity_assessment" in requested:
        groups.extend(
            [
                ["authentic", "synthetic", "uncertain"],
                [
                    "direct_photo",
                    "edited_photo",
                    "screenshot",
                    "illustration",
                    "composite",
                    "uncertain_capture",
                ],
            ]
        )
    if "visual_semantic_selection" in requested:
        groups.append(
            [
                "semantic_match",
                "semantic_mismatch",
                "semantic_uncertain",
            ]
        )
    if "image_classification" in requested:
        groups.append(
            _classification_label_ids(
                _require_classification_contract(
                    task_spec,
                    capability="image_classification",
                )
            )
        )
    return groups


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
    groups = _vlm_tag_groups(purpose, task_spec)
    if "tag_field_name" in properties:
        parameters["tag_field_name"] = {
            "authenticity": "authenticity_tags",
            "classification": "image_tags",
            "semantic_selection": "visual_tags",
            "shared_visual_tagging": "visual_tags",
        }[purpose]
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
        elif purpose == "classification":
            contract = _require_classification_contract(
                task_spec,
                capability="image_classification",
            )
            allowed = _classification_label_ids(contract)
            resolved = builtin_prompt_registry().resolve(
                "closed-set-image-classification",
                1,
                variables={"allowed_labels": ", ".join(allowed)},
            )
        elif purpose == "semantic_selection":
            resolved = builtin_prompt_registry().resolve(
                "image-semantic-selection",
                2,
                variables=_semantic_selection_variables(task_spec),
            )
        else:
            scope = task_spec.hard_constraints.get("authenticity_scope")
            exclusions = "; ".join(task_spec.exclusion_requirements)
            task_scope = "; ".join(str(item) for item in (scope, exclusions) if item)
            resolved = builtin_prompt_registry().resolve(
                "image-task-visual-tagging",
                3,
                variables={
                    "task_scope_instruction": task_scope or "No additional exclusions.",
                    "tag_contract_instruction": " ".join(
                        f"Group {index}: exactly one of {', '.join(group)}."
                        for index, group in enumerate(groups, start=1)
                    ),
                    **_semantic_selection_variables(task_spec),
                },
            )
        parameters["system_prompt"] = resolved.text
        if "allowed_tags" in properties:
            parameters["required_tag_groups"] = groups
            parameters["allowed_tags"] = list(
                dict.fromkeys(tag for group in groups for tag in group)
            )
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
        (operator_id, candidate)
        for operator_id, candidate in candidates.items()
        if candidate.runtime_backend == RuntimeBackend.REMOTE
        and "visual_understanding"
        in library.registry.get(operator_id).capability_tags
    ]
    if not eligible:
        raise ValueError(
            "Authenticity assessment requires an executable remote visual operator"
        )
    return sorted(
        eligible,
        key=lambda item: (
            library.registry.get(item[0]).provider.provider_id != "native",
            -item[1].score,
            item[0],
        ),
    )[0][0]


def _parameters_for_capability(
    capability: str,
    policy: dict[str, Any],
    task_spec: TaskSpecVersion,
    operator_parameter_schema: dict[str, Any],
) -> dict[str, Any]:
    bound, _ = bind_constraint_parameters(
        operator_parameter_schema,
        task_spec.constraints,
    )
    if bound:
        return bound
    constraints = {
        (item.field.rsplit(".", 1)[-1], item.operator): item.value
        for item in task_spec.constraints
    }
    if capability == "image_shape":
        return {
            "min_width": int(constraints[("width_px", "gte")]),
            "min_height": int(constraints[("height_px", "gte")]),
            "any_or_all": "all",
        }
    if capability == "aspect_ratio":
        return {
            "min_ratio": float(constraints[("aspect_ratio", "gte")]),
            "max_ratio": float(constraints[("aspect_ratio", "lte")]),
            "any_or_all": "all",
        }
    if capability == "file_size":
        minimum = int(constraints[("file_size_bytes", "gte")])
        maximum = int(constraints[("file_size_bytes", "lte")])
        return {
            "min_size": _format_binary_size(minimum),
            "max_size": _format_binary_size(maximum),
            "any_or_all": "all",
        }
    if capability == "face_count":
        if ("face_count", "lte") in constraints:
            upper_bound = int(constraints[("face_count", "lte")])
        else:
            upper_bound = int(constraints[("face_count", "lt")]) - 1
        return {
            "min_face_count": 0,
            "max_face_count": upper_bound,
            "any_or_all": "all",
        }
    if capability == "image_quality":
        return {"confidence_threshold": policy["quality_threshold"]}
    if capability == "authenticity_assessment":
        return {"uncertain_policy": policy["uncertain_policy"]}
    if capability == "visual_semantic_selection":
        return {"uncertain_policy": policy["uncertain_policy"]}
    if capability == "class_resolution":
        contract = _require_classification_contract(
            task_spec,
            capability=capability,
        )
        return {
            "mixed_policy": policy["mixed_policy"],
            "unknown_policy": policy["unknown_policy"],
            **contract,
        }
    if capability == "perceptual_deduplication":
        return {"distance_threshold": policy["dedup_distance"]}
    if capability == "dataset_partition":
        contract = _require_classification_contract(
            task_spec,
            capability=capability,
        )
        return {
            "directory_prefix": "classes",
            "allowed_labels": [item["id"] for item in contract["labels"]],
            "mixed_label": contract["mixed_label"],
            "unknown_label": contract["unknown_label"],
        }
    return {}


def _format_binary_size(value: int) -> str:
    if value % (1024 * 1024) == 0:
        return f"{value // (1024 * 1024)}MB"
    if value % 1024 == 0:
        return f"{value // 1024}KB"
    return str(value)


_CONSTRAINT_NODE_IDS = {
    "width_px": "image_shape",
    "height_px": "image_shape",
    "aspect_ratio": "aspect_ratio",
    "file_size_bytes": "file_size",
    "face_count": "face_count",
    "primary_subject_garment_color": "visual_tagging",
    "exact_duplicate_count": "deduplicate",
    "perceptual_duplicate_policy_applied": "deduplicate",
    "source_assets_immutable": "ingest",
}


def _constraint_coverage(
    spec: TaskSpecVersion,
    nodes: tuple[PipelineNode, ...],
    library: OperatorLibrary,
    state: WorkOrderGraphState,
) -> tuple[ConstraintCoverage, ...]:
    by_id = {node.id: node for node in nodes}
    by_operator_id = {node.operator_version_id: node for node in nodes}
    selected_by_constraint = {
        item.capability_id: item.selected_operator_version_id
        for item in (
            CapabilityCoverage.model_validate(payload)
            for payload in state.get("capability_coverage", ())
        )
        if item.selected_operator_version_id
    }
    coverage: list[ConstraintCoverage] = []
    for constraint in spec.constraints:
        normalized_field = constraint.field.rsplit(".", 1)[-1]
        node_id = _CONSTRAINT_NODE_IDS.get(normalized_field)
        node = by_operator_id.get(
            str(selected_by_constraint.get(constraint.id, ""))
        )
        if node is None:
            node = by_id.get(node_id or "")
        if node is None:
            for candidate in nodes:
                operator = library.registry.get(candidate.operator_version_id)
                _, covered = bind_constraint_parameters(
                    operator.parameter_schema,
                    (constraint,),
                )
                if covered:
                    node = candidate
                    break
        if node is None:
            continue
        coverage.append(
            ConstraintCoverage(
                constraint_id=constraint.id,
                node_id=node.id,
                operator_version_id=node.operator_version_id,
                evidence_type=constraint.required_evidence_type,
            )
        )
    return tuple(coverage)


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
    selected = dict(selection)
    shared_visual_operator_id: str | None = None
    authenticity_operator_id = selected.get("authenticity_assessment")
    semantic_selection_operator_id = selected.get("visual_semantic_selection")
    classification_operator_id = selected.get("image_classification")
    visual_capabilities = {
        capability
        for capability, operator_id in (
            ("authenticity_assessment", authenticity_operator_id),
            ("visual_semantic_selection", semantic_selection_operator_id),
            ("image_classification", classification_operator_id),
        )
        if operator_id
    }
    if visual_capabilities:
        selected_visual_providers = [
            operator_id
            for operator_id in (
                authenticity_operator_id,
                semantic_selection_operator_id,
                classification_operator_id,
            )
            if operator_id
            and "visual_understanding"
            in library.registry.get(operator_id).capability_tags
        ]
        if selected_visual_providers:
            shared_visual_operator_id = sorted(
                set(selected_visual_providers),
                key=lambda operator_id: (
                    library.registry.get(
                        operator_id
                    ).provider.provider_id
                    != "native",
                    operator_id,
                ),
            )[0]
        else:
            shared_visual_operator_id = _remote_visual_operator(
                library=library,
                candidates=candidates,
            )
    shared_visual_emitted = False

    for capability, operator_id in selection:
        operator = library.registry.get(operator_id)
        if (
            shared_visual_operator_id
            and capability in visual_capabilities
            and not shared_visual_emitted
        ):
            purpose = (
                "shared_visual_tagging"
                if len(visual_capabilities) > 1
                else {
                    "authenticity_assessment": "authenticity",
                    "visual_semantic_selection": "semantic_selection",
                    "image_classification": "classification",
                }[capability]
            )
            vlm_parameters, prompt_binding = _vlm_configuration(
                shared_visual_operator_id,
                purpose=purpose,
                task_spec=task_spec,
                library=library,
                candidates=candidates,
            )
            nodes.append(
                _node(
                    node_id="visual_tagging",
                    operator_id=shared_visual_operator_id,
                    parameters=vlm_parameters,
                    library=library,
                    candidates=candidates,
                    prompt_binding=prompt_binding,
                )
            )
            shared_visual_emitted = True
        if (
            capability == "image_classification"
            and operator_id == shared_visual_operator_id
        ):
            continue

        base_node_id = _node_id_for_operator(
            capability,
            operator_id,
            library,
        )
        used_node_ids[base_node_id] = used_node_ids.get(base_node_id, 0) + 1
        node_id = (
            base_node_id
            if used_node_ids[base_node_id] == 1
            else f"{base_node_id}_{used_node_ids[base_node_id]}"
        )
        parameters = _parameters_for_capability(
            capability,
            policy,
            task_spec,
            operator.parameter_schema,
        )
        if operator_id in candidates:
            parameters = {
                **candidates[operator_id].parameters,
                **parameters,
            }
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
    template_experience_id: str | None = None,
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
        created_from=(
            f"pipeline_experience:{template_experience_id}"
            if template_experience_id
            else "processing_agent"
        ),
        template_experience_id=template_experience_id,
        required_constraint_ids=tuple(item.id for item in spec.constraints),
        constraint_coverage=_constraint_coverage(
            spec,
            nodes,
            operator_library or build_operator_library(include_datajuicer=False),
            state,
        ),
    )


def generate_pipeline_variants(
    state: WorkOrderGraphState,
    *,
    operator_library: OperatorLibrary | None = None,
    experience_retriever: PipelineExperienceRetriever | None = None,
    planner: AgentPlanner | None = None,
    trial_runner: PipelineTrialRunner | None = None,
) -> dict:
    if planner is not None:
        return _generate_agent_pipeline_variants(
            state,
            operator_library=operator_library,
            planner=planner,
            trial_runner=trial_runner,
        )
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    library = operator_library or build_operator_library(include_datajuicer=False)
    available_operator_ids = {
        item.id for item in library.registry.search(include_drafts=True)
    }
    experience_matches: tuple[PipelineExperienceMatch, ...] = ()
    if experience_retriever is not None:
        experience_matches = experience_retriever.search(
            spec,
            owner_id=state["owner_id"],
            available_operator_ids=available_operator_ids,
        )
    variants = [
        _build_pipeline(
            strategy=strategy,
            state=state,
            spec=spec,
            owner_id=state["owner_id"],
            operator_library=library,
            template_experience_id=next(
                (
                    match.experience_id
                    for match in experience_matches
                    if match.strategy == strategy.value
                ),
                experience_matches[0].experience_id if experience_matches else None,
            ),
        ).model_dump(mode="json")
        for strategy in PipelineStrategy
    ]
    return {
        "pipeline_variants": variants,
        "pipeline_experience_matches": [
            item.model_dump(mode="json") for item in experience_matches
        ],
        "current_agent": "processing",
        "trace": append_trace(state, "processing:variants_generated"),
    }


def _generate_agent_pipeline_variants(
    state: WorkOrderGraphState,
    *,
    operator_library: OperatorLibrary | None,
    planner: AgentPlanner,
    trial_runner: PipelineTrialRunner | None,
) -> dict:
    del trial_runner
    library = operator_library or build_operator_library(include_datajuicer=False)
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    operator_plan = OperatorPlanVersion.model_validate(
        state.get("operator_plan") or {}
    )
    if not operator_plan.confirmed or not state.get("operator_plan_confirmed"):
        raise ValueError(
            "Processing Agent requires a confirmed OperatorPlan"
        )
    candidate_payloads = [
        OperatorCatalogMatch.model_validate(item)
        for item in state.get("operator_candidates", ())
    ]
    candidates = {
        item.operator_version_id: item
        for item in candidate_payloads
        if item.executable
    }
    allowed_by_constraint: dict[str, set[str]] = {}
    for payload in state.get("capability_coverage", ()):
        coverage_item = CapabilityCoverage.model_validate(payload)
        allowed = {
            item.operator_version_id
            for item in coverage_item.candidates
            if item.executable
        }
        if coverage_item.selected_operator_version_id:
            allowed.add(coverage_item.selected_operator_version_id)
        allowed_by_constraint[coverage_item.capability_id] = allowed
    compiled: list[PipelineVersion] = []

    def compile_variants(payload: dict[str, Any]) -> dict[str, Any]:
        raw_pipelines = payload.get("pipelines")
        if not isinstance(raw_pipelines, list):
            raise ValueError("pipelines must be an array")
        by_strategy: dict[PipelineStrategy, dict[str, Any]] = {}
        for raw in raw_pipelines:
            if not isinstance(raw, dict):
                raise ValueError("Every Pipeline proposal must be an object")
            strategy = PipelineStrategy(str(raw.get("strategy", "")))
            if strategy in by_strategy:
                raise ValueError(f"Duplicate Pipeline strategy: {strategy.value}")
            by_strategy[strategy] = raw
        if set(by_strategy) != set(PipelineStrategy):
            raise ValueError("Exactly one proposal is required for each strategy")

        required_constraints = tuple(
            item.id for item in spec.constraints if item.hardness == "hard"
        )
        constraints = {item.id: item for item in spec.constraints}
        variants: list[PipelineVersion] = []
        required_system_capabilities = {
            action
            for action in spec.output_actions
            if any(
                operator.primary_category == OperatorCategory.OUTPUT
                and (
                    action in operator.capability_tags
                    or action == operator.secondary_category
                )
                for operator in library.registry.search(include_drafts=True)
            )
        }
        for strategy in PipelineStrategy:
            raw = by_strategy[strategy]
            raw_nodes = raw.get("nodes")
            if not isinstance(raw_nodes, list) or not raw_nodes:
                raise ValueError(f"{strategy.value} requires at least one node")
            nodes: list[PipelineNode] = []
            coverage: list[ConstraintCoverage] = []
            covered_ids: set[str] = set()
            for index, raw_node in enumerate(raw_nodes, start=1):
                if not isinstance(raw_node, dict):
                    raise ValueError("Pipeline nodes must be objects")
                operator_id = str(
                    raw_node.get("operator_version_id", "")
                )
                if operator_id not in candidates:
                    raise ValueError(
                        "Pipeline uses an Operator that was not returned as an "
                        f"executable retrieval candidate: {operator_id}"
                    )
                operator = library.registry.get(operator_id)
                parameters = dict(raw_node.get("parameters") or {})
                validate_parameters(operator.parameter_schema, parameters)
                node_id = f"{strategy.value}_{index:02d}"
                nodes.append(
                    PipelineNode(
                        id=node_id,
                        operator_version_id=operator.id,
                        name=operator.display_name,
                        category=operator.primary_category.value,
                        parameters=parameters,
                        runtime_backend=candidates[operator_id].runtime_backend,
                        required=bool(raw_node.get("required", True)),
                    )
                )
                for constraint_id in raw_node.get("constraint_ids") or ():
                    constraint_id = str(constraint_id)
                    if constraint_id not in constraints:
                        raise ValueError(
                            f"Unknown Constraint coverage: {constraint_id}"
                        )
                    if constraint_id in covered_ids:
                        continue
                    constraint = constraints[constraint_id]
                    allowed = allowed_by_constraint.get(constraint_id, set())
                    if allowed and operator.id not in allowed:
                        raise ValueError(
                            f"{operator.id} was not retrieved as evidence for "
                            f"Constraint {constraint_id}"
                        )
                    expected_parameters, bound = bind_constraint_parameters(
                        operator.parameter_schema,
                        (constraint,),
                    )
                    if bound and any(
                        parameters.get(name) != value
                        for name, value in expected_parameters.items()
                    ):
                        raise ValueError(
                            f"{operator.id} parameters do not implement "
                            f"Constraint {constraint_id}: expected "
                            f"{expected_parameters}"
                        )
                    coverage.append(
                        ConstraintCoverage(
                            constraint_id=constraint_id,
                            node_id=node_id,
                            operator_version_id=operator.id,
                            evidence_type=constraint.required_evidence_type,
                        )
                    )
                    covered_ids.add(constraint_id)
            missing = set(required_constraints).difference(covered_ids)
            if missing:
                raise ValueError(
                    f"{strategy.value} does not cover Constraints: "
                    + ", ".join(sorted(missing))
                )
            for capability in sorted(required_system_capabilities):
                if not any(
                    (
                        capability
                        in library.registry.get(
                            node.operator_version_id
                        ).capability_tags
                        or capability
                        == library.registry.get(
                            node.operator_version_id
                        ).secondary_category
                    )
                    for node in nodes
                ):
                    raise ValueError(
                        f"{strategy.value} lacks required system capability "
                        f"{capability}"
                    )
            edges = tuple(
                PipelineEdge(source=left.id, target=right.id)
                for left, right in zip(nodes, nodes[1:])
            )
            variants.append(
                PipelineVersion(
                    id=new_id("pipeline_version"),
                    family_id=f"pipeline_{strategy.value}",
                    version=1,
                    created_by=state["owner_id"],
                    change_reason=(
                        "Processing Agent compiled an ordered proposal through "
                        "the governed Artifact Tool"
                    ),
                    strategy=strategy,
                    task_spec_version_id=spec.id,
                    nodes=tuple(nodes),
                    edges=edges,
                    created_from="processing_agent_react",
                    required_constraint_ids=required_constraints,
                    constraint_coverage=tuple(coverage),
                )
            )
        compiled.clear()
        compiled.extend(variants)
        return {
            "ok": True,
            "pipelines": [
                item.model_dump(mode="json") for item in variants
            ],
        }

    def validate_finish(_payload: dict[str, Any]) -> dict[str, Any]:
        errors: list[dict[str, str]] = []
        if not compiled:
            errors.append(
                {
                    "code": "NO_VALIDATED_PIPELINES",
                    "message": (
                        "Call compile_pipeline_variants successfully before "
                        "finishing."
                    ),
                }
            )
        return {"ok": not errors, "errors": errors}

    loop = AgentRunner(
        agent_name="processing",
        planner=planner,
        tools=(
            AgentTool(
                name="compile_pipeline_variants",
                description=(
                    "Compile and validate three ordered Pipeline proposals. "
                    "Order and parameters are preserved exactly; the tool never "
                    "selects or reorders Operators."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "pipelines": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "strategy": {
                                        "enum": [
                                            item.value
                                            for item in PipelineStrategy
                                        ]
                                    },
                                    "nodes": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "operator_version_id": {
                                                    "type": "string"
                                                },
                                                "parameters": {
                                                    "type": "object"
                                                },
                                                "constraint_ids": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "string"
                                                    },
                                                },
                                                "required": {
                                                    "type": "boolean"
                                                },
                                            },
                                            "required": [
                                                "operator_version_id",
                                                "parameters",
                                                "constraint_ids",
                                            ],
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "required": ["strategy", "nodes"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["pipelines"],
                    "additionalProperties": False,
                },
                execute=compile_variants,
            ),
        ),
        max_iterations=4,
        finish_validator=validate_finish,
    )
    result = loop.run(
        goal=(
            "Use only retrieved candidates and confirmed Constraints to design "
            "retention_first, balanced, and quality_first Pipelines. Decide "
            "Operator selection, order, parameters, and Constraint coverage. "
            "Compile them with the Artifact Tool and repair any validation "
            "observation. Finish after static validation succeeds."
        ),
        context={
            "task_spec": spec.model_dump(mode="json"),
            "operator_candidates": [
                item.model_dump(mode="json") for item in candidates.values()
            ],
            "operator_plan": operator_plan.model_dump(mode="json"),
            "pipeline_experience_matches": state.get(
                "pipeline_experience_matches", ()
            ),
            "latest_run_feedback": state.get("latest_run_feedback"),
            "observations": state.get("agent_observations", ()),
        },
    )
    if result.status != "finished" or not compiled:
        raise ValueError(
            "Processing Agent finished without validated Pipeline variants"
        )
    observation = {
        "agent": "processing",
        "status": result.status,
        "summary": result.decisions[-1].reason_summary,
        "tool_observations": [
            item.model_dump(mode="json") for item in result.observations
        ],
    }
    return {
        "pipeline_variants": [
            item.model_dump(mode="json") for item in compiled
        ],
        "pipeline_experience_matches": state.get(
            "pipeline_experience_matches", []
        ),
        "agent_observations": [
            *state.get("agent_observations", ()),
            observation,
        ],
        "current_agent": "processing",
        "trace": append_trace(state, "processing:agent_loop_finished"),
    }


def select_representative_pipelines(state: WorkOrderGraphState) -> dict:
    variants = [PipelineVersion.model_validate(item) for item in state["pipeline_variants"]]
    if {item.strategy for item in variants} != set(PipelineStrategy):
        raise ValueError("Exactly one compiled pipeline is required for each strategy")
    incomplete: list[str] = []
    for pipeline in variants:
        covered = {item.constraint_id for item in pipeline.constraint_coverage}
        missing = set(pipeline.required_constraint_ids).difference(covered)
        if missing:
            incomplete.extend(sorted(missing))
    if incomplete:
        raise ValueError(
            "Pipelines cannot be offered for approval because required "
            "constraint coverage is missing: "
            + ", ".join(dict.fromkeys(incomplete))
        )
    return {
        "representative_pipelines": [item.model_dump(mode="json") for item in variants],
        "next_action": "approve_pipeline",
        "trace": append_trace(state, "processing:representatives_selected"),
    }
