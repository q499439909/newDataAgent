from __future__ import annotations

import re
import json
from pathlib import Path

from ...domain.common import new_id
from ...domain.operators import OperatorStatus, RuntimeBackend
from ...domain.operators import OperatorCategory
from ...domain.plans import (
    CapabilityCandidateEvidence,
    CapabilityCoverage,
    CapabilityCoverageStatus,
    RetrievalPlanVersion,
)
from ...domain.specs import TaskSpecVersion
from ...domain.specs.binding import bind_constraint_parameters
from ...operators.catalog_matching import (
    HybridOperatorCatalogMatcher,
    OperatorCatalogMatch,
)
from ...operators.catalog_ranking import OperatorCandidateRanker, OperatorRankingPolicy
from ...operators.registry import OperatorRegistry
from ...experiences import PipelineExperienceRetriever
from ..runtime import AgentDecisionLoop, AgentPlanner, AgentTool
from ..shared import WorkOrderGraphState, append_trace


_NATIVE_CAPABILITY_TAGS: dict[str, frozenset[str]] = {
    "image_decode": frozenset({"decode"}),
    "image_quality": frozenset({"quality", "filter"}),
    "authenticity_assessment": frozenset({"authenticity_assessment"}),
    "visual_semantic_selection": frozenset({"visual_semantic_selection"}),
    "class_resolution": frozenset({"class_resolution"}),
    "dataset_partition": frozenset({"dataset_partition"}),
    "perceptual_deduplication": frozenset({"deduplication"}),
    "manifest": frozenset({"manifest"}),
}

_COVERAGE_LIFECYCLE_ORDER = {
    OperatorStatus.PUBLIC_RELEASE.value: 0,
    OperatorStatus.PERSONAL_RELEASE.value: 1,
    OperatorStatus.EVALUATED.value: 2,
    OperatorStatus.PROVIDER_AVAILABLE.value: 3,
    OperatorStatus.DRAFT.value: 4,
    OperatorStatus.DEPRECATED.value: 5,
}


def _capability_coverage(
    spec: TaskSpecVersion,
    operator_candidates: tuple,
    *,
    operator_registry: OperatorRegistry,
    available_runtime_backends: frozenset[RuntimeBackend],
    requested_requirements: list[dict] | None = None,
) -> tuple[CapabilityCoverage, ...]:
    requested = list(requested_requirements or spec.capability_requirements)
    if not requested:
        requested = [
            {
                "id": capability,
                "capability": capability,
                "description": "",
                "required": True,
            }
            for capability in spec.required_capabilities
        ]
    coverage: list[CapabilityCoverage] = []
    all_operators = operator_registry.search(include_drafts=True)

    def has_executable_tag(tag: str) -> bool:
        for candidate in operator_candidates:
            if not candidate.executable:
                continue
            operator = operator_registry.get(candidate.operator_version_id)
            if tag in operator.capability_tags:
                return True
        for operator in all_operators:
            if operator.provider.provider_id != "native":
                continue
            supported = {profile.backend for profile in operator.supported_runtime_profiles}
            if (
                tag in operator.capability_tags
                and supported.intersection(available_runtime_backends)
                and operator.status
                not in {OperatorStatus.DRAFT, OperatorStatus.DEPRECATED}
            ):
                return True
        return False

    for requested_item in requested:
        if isinstance(requested_item, dict):
            capability_id = requested_item["id"]
            capability = requested_item["capability"]
            description = requested_item.get("description", "")
            required = bool(requested_item.get("required", True))
        else:
            capability_id = requested_item.id
            capability = requested_item.capability
            description = requested_item.description
            required = requested_item.required
        native_tags = _NATIVE_CAPABILITY_TAGS.get(capability, frozenset())
        evidence = []
        for item in operator_candidates:
            if (item.capability or item.intent) != capability:
                continue
            operator = operator_registry.get(item.operator_version_id)
            if (
                operator.provider.provider_id == "native"
                and native_tags
                and native_tags.issubset(operator.capability_tags)
            ):
                # Native policy operators are evaluated below together with their
                # governed upstream capability requirements.
                continue
            evidence.append(
                CapabilityCandidateEvidence(
                    operator_version_id=item.operator_version_id,
                    provider_id=item.provider_id,
                    provider_operator_ref=item.provider_operator_ref,
                    runtime_backend=item.runtime_backend.value,
                    lifecycle_status=item.status.value,
                    executable=item.executable,
                    score=item.score,
                    blocked_reason=item.blocked_reason,
                    runtime_resolution=item.runtime_resolution,
                )
            )
        for operator in all_operators:
            if operator.provider.provider_id != "native" or not native_tags:
                continue
            if not native_tags.issubset(operator.capability_tags):
                continue
            supported = {
                profile.backend for profile in operator.supported_runtime_profiles
            }
            available = supported.intersection(available_runtime_backends)
            upstream_tags = tuple(
                operator.resource_requirements.get("upstream_capability_tags", ())
            )
            missing_upstream = [
                tag for tag in upstream_tags if not has_executable_tag(str(tag))
            ]
            executable = bool(available) and not missing_upstream and operator.status not in {
                OperatorStatus.DRAFT,
                OperatorStatus.DEPRECATED,
            }
            backend = next(
                iter(available or supported),
                RuntimeBackend.CPU,
            )
            evidence.append(
                CapabilityCandidateEvidence(
                    operator_version_id=operator.id,
                    provider_id="native",
                    provider_operator_ref=operator.provider.provider_operator_ref,
                    runtime_backend=backend.value,
                    lifecycle_status=operator.status.value,
                    executable=executable,
                    score=500 if executable else 0,
                    blocked_reason=None
                    if executable
                    else (
                        "Missing executable upstream capabilities: "
                        + ", ".join(missing_upstream)
                        if missing_upstream
                        else "Native operator runtime or lifecycle is unavailable"
                    ),
                )
            )
        evidence.sort(
            key=lambda item: (
                not item.executable,
                _COVERAGE_LIFECYCLE_ORDER.get(item.lifecycle_status, 99),
                -item.score,
                item.operator_version_id,
            )
        )
        selected = next((item for item in evidence if item.executable), None)
        status = (
            CapabilityCoverageStatus.COVERED
            if selected is not None
            else CapabilityCoverageStatus.BLOCKED
            if evidence
            else CapabilityCoverageStatus.MISSING
        )
        coverage.append(
            CapabilityCoverage(
                capability_id=capability_id,
                capability=capability,
                description=description,
                required=required,
                status=status,
                selected_operator_version_id=(
                    selected.operator_version_id if selected is not None else None
                ),
                candidates=tuple(evidence),
            )
        )
    return tuple(coverage)


def _constraint_query(constraint) -> str:
    return " ".join(
        (
            constraint.field.replace(".", " ").replace("_", " "),
            constraint.required_evidence_type.replace("_", " "),
            constraint.source_text,
        )
    )


def _preferred_match_capability(match, registry: OperatorRegistry) -> str:
    operator = registry.get(match.operator_version_id)
    generic_tags = {
        "api",
        "candidate",
        "cpu",
        "cuda",
        "datajuicer",
        "filter",
        "image",
        "model",
        "remote",
    }
    query_tokens = set(
        _constraint_token
        for value in (match.matched_terms or ())
        for _constraint_token in value.lower().replace("_", " ").split()
    )
    semantic_tags = [
        tag
        for tag in operator.capability_tags
        if tag not in generic_tags
        and set(tag.replace("_", " ").split()).intersection(query_tokens)
    ]
    if semantic_tags:
        return sorted(semantic_tags, key=lambda item: (-len(item), item))[0]
    return match.capability or operator.secondary_category


def _constraint_driven_candidates(
    spec: TaskSpecVersion,
    matcher: HybridOperatorCatalogMatcher,
    registry: OperatorRegistry,
    *,
    allow_draft_candidates: bool,
) -> tuple[tuple, list[dict]]:
    candidates = []
    requirements: list[dict] = []
    for constraint in spec.constraints:
        matches = matcher.match(
            _constraint_query(constraint),
            allow_draft_candidates=allow_draft_candidates,
        )
        if matches:
            preferred = matches[0]
            capability = _preferred_match_capability(preferred, registry)
            matching = tuple(
                item.model_copy(
                    update={"capability": capability, "intent": capability}
                )
                for item in matches
                if _preferred_match_capability(item, registry) == capability
            )
            if constraint.scope == "asset" and spec.semantic_requirements:
                semantic_matches = matcher.match(
                    " ".join(
                        (
                            _constraint_query(constraint),
                            *spec.semantic_requirements,
                        )
                    ),
                    required_capabilities=("visual_semantic_selection",),
                    allow_draft_candidates=allow_draft_candidates,
                )
                matching = tuple(
                    item.model_copy(
                        update={
                            "capability": "visual_semantic_selection",
                            "intent": "visual_semantic_selection",
                        }
                    )
                    for item in semantic_matches
                    if item.capability == "visual_semantic_selection"
                )
                if matching:
                    capability = "visual_semantic_selection"
            candidates.extend(matching)
        elif constraint.scope == "asset" and spec.semantic_requirements:
            semantic_matches = matcher.match(
                " ".join(spec.semantic_requirements),
                required_capabilities=("visual_semantic_selection",),
                allow_draft_candidates=allow_draft_candidates,
            )
            capability = "visual_semantic_selection"
            matching = tuple(
                item.model_copy(
                    update={"capability": capability, "intent": capability}
                )
                for item in semantic_matches
                if item.capability == capability
            )
            candidates.extend(matching)
        else:
            capability = constraint.field
        requirements.append(
            {
                "id": constraint.id,
                "capability": capability,
                "description": constraint.source_text,
                "required": constraint.hardness == "hard",
            }
        )
    unique = {
        (item.capability, item.operator_version_id): item for item in candidates
    }
    requirements.extend(
        (
            {
                "id": "system_image_decode",
                "capability": "image_decode",
                "description": "Validate and decode source assets before processing.",
                "required": True,
            },
            {
                "id": "system_manifest",
                "capability": "manifest",
                "description": "Emit the final reproducible result manifest.",
                "required": True,
            },
        )
    )
    return tuple(unique.values()), requirements


def generate_retrieval_plan(
    state: WorkOrderGraphState,
    *,
    operator_registry: OperatorRegistry | None = None,
    allow_draft_candidates: bool = False,
    available_runtime_backends: frozenset[RuntimeBackend] = frozenset(
        {RuntimeBackend.CPU}
    ),
    planner: AgentPlanner | None = None,
    experience_retriever: PipelineExperienceRetriever | None = None,
) -> dict:
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    if planner is not None:
        if operator_registry is None:
            raise RuntimeError("Retrieval Agent requires an OperatorRegistry")
        return _generate_agent_retrieval_plan(
            state,
            spec=spec,
            operator_registry=operator_registry,
            allow_draft_candidates=allow_draft_candidates,
            available_runtime_backends=available_runtime_backends,
            planner=planner,
            experience_retriever=experience_retriever,
        )
    effective_runtime_backends = frozenset(
        {
            *available_runtime_backends,
            *(
                RuntimeBackend(item)
                for item in state.get("runtime_backend_overrides", [])
            ),
        }
    )
    derived_requirements: list[dict] | None = None
    uses_legacy_requirement_parser = (
        spec.planning_origin == "legacy_compatibility"
    )
    if (
        operator_registry is not None
        and spec.constraints
        and not uses_legacy_requirement_parser
    ):
        # Confirmed constraints are the authoritative business contract.
        # Legacy capability lists may still be present on older TaskSpec
        # versions, but they must never suppress per-constraint retrieval.
        # The explicit compatibility marker is retained only for callers that
        # have not injected the new RequirementPlanner yet.
        recalled_candidates, derived_requirements = _constraint_driven_candidates(
            spec,
            HybridOperatorCatalogMatcher(operator_registry),
            operator_registry,
            allow_draft_candidates=allow_draft_candidates,
        )
    else:
        recalled_candidates = (
            HybridOperatorCatalogMatcher(operator_registry).match(
                spec.objective,
                required_capabilities=spec.required_capabilities,
                capability_requirements=spec.capability_requirements,
                allow_draft_candidates=allow_draft_candidates,
            )
            if operator_registry is not None
            else ()
        )
    operator_candidates = (
        OperatorCandidateRanker(operator_registry).rank(
            recalled_candidates,
            policy=OperatorRankingPolicy(
                available_runtime_backends=effective_runtime_backends,
                allow_draft_candidates=allow_draft_candidates,
                cost_preference=str(spec.preferences.get("cost_preference", "balanced")),
            ),
        )
        if operator_registry is not None
        else ()
    )
    coverage = (
        _capability_coverage(
            spec,
            operator_candidates,
            operator_registry=operator_registry,
            available_runtime_backends=effective_runtime_backends,
            requested_requirements=derived_requirements,
        )
        if operator_registry is not None
        else ()
    )
    target = max(sum(spec.quotas.values()) * 3, 1000)
    routes = tuple(
        {
            "source_type": source.type,
            "source_uri": source.uri,
            "collection": source.collection,
            "positive_queries": [spec.objective],
            "negative_queries": list(spec.exclusion_requirements),
            "retrievers": ["filesystem"]
            if source.type == "local_directory"
            else ["text_vector", "metadata_filter"],
        }
        for source in spec.data_sources
    )
    coverage_complete = bool(coverage) and all(
        not item.required or item.status == CapabilityCoverageStatus.COVERED
        for item in coverage
    )
    legacy_sufficient = not any(
        item for item in operator_candidates if not item.executable
    )
    previous_plan = state.get("retrieval_plan")
    plan = RetrievalPlanVersion(
        id=new_id("retrieval_plan"),
        version=int(previous_plan.get("version", 0)) + 1 if previous_plan else 1,
        parent_version_id=previous_plan.get("id") if previous_plan else None,
        created_by=state["owner_id"],
        change_reason=(
            "capability resolution retry" if previous_plan else "initial retrieval planning"
        ),
        task_spec_version_id=spec.id,
        routes=routes,
        target_candidate_count=target,
        sufficient=coverage_complete if coverage else legacy_sufficient,
        operator_candidates=tuple(
            item.model_dump(mode="json") for item in operator_candidates
        ),
        capability_coverage=coverage,
    )
    return {
        "retrieval_plan": plan.model_dump(mode="json"),
        "operator_candidates": [
            item.model_dump(mode="json") for item in operator_candidates
        ],
        "capability_coverage": [item.model_dump(mode="json") for item in coverage],
        "current_agent": "retrieval",
        "trace": append_trace(state, "retrieval:plan_generated"),
    }


def _operator_catalog_payload(operator) -> dict:
    return {
        "operator_version_id": operator.id,
        "display_name": operator.display_name,
        "summary": operator.summary,
        "description": operator.description,
        "primary_category": operator.primary_category.value,
        "secondary_category": operator.secondary_category,
        "capability_tags": sorted(operator.capability_tags),
        "parameter_schema": operator.parameter_schema,
        "provider_id": operator.provider.provider_id,
        "provider_operator_ref": operator.provider.provider_operator_ref,
        "status": operator.status.value,
        "runtime_profiles": [
            item.model_dump(mode="json")
            for item in operator.supported_runtime_profiles
        ],
        "limitations": list(operator.limitations),
    }


def _explicit_candidate(
    *,
    operator,
    capability: str,
    available_runtime_backends: frozenset[RuntimeBackend],
    allow_draft_candidates: bool,
) -> OperatorCatalogMatch:
    supported = {
        item.backend for item in operator.supported_runtime_profiles
    }
    available = sorted(
        supported.intersection(available_runtime_backends),
        key=lambda item: item.value,
    )
    runtime_backend = (
        available[0]
        if available
        else sorted(supported, key=lambda item: item.value)[0]
    )
    released = operator.status in {
        OperatorStatus.PROVIDER_AVAILABLE,
        OperatorStatus.PERSONAL_RELEASE,
        OperatorStatus.PUBLIC_RELEASE,
    }
    executable = bool(available) and (
        released
        or (allow_draft_candidates and operator.status == OperatorStatus.DRAFT)
    )
    blocked_reason = None
    if not available:
        blocked_reason = "No supported runtime backend is currently available"
    elif not executable:
        blocked_reason = f"Operator lifecycle status is {operator.status.value}"
    return OperatorCatalogMatch(
        intent=capability,
        capability=capability,
        operator_version_id=operator.id,
        provider_id=operator.provider.provider_id,
        provider_operator_ref=operator.provider.provider_operator_ref,
        display_name=operator.display_name,
        score=0,
        recall_sources=("agent_selection",),
        runtime_backend=runtime_backend,
        status=operator.status,
        executable=executable,
        blocked_reason=blocked_reason,
    )


def _operator_supports_constraint(operator, constraint, spec) -> bool:
    """Validate support from Operator metadata/schema, never from task keywords."""

    _parameters, schema_supports = bind_constraint_parameters(
        operator.parameter_schema,
        (constraint,),
    )
    if schema_supports:
        return True
    constraint_text = " ".join(
        (
            constraint.field,
            constraint.required_evidence_type,
            constraint.unit,
        )
    ).lower()
    operator_text = " ".join(
        (
            operator.secondary_category,
            operator.summary,
            operator.description,
            operator.output_schema,
            *sorted(operator.capability_tags),
            json.dumps(operator.parameter_schema, ensure_ascii=True),
        )
    ).lower()
    ignored = {
        "asset",
        "boolean",
        "count",
        "data",
        "image",
        "label",
        "metadata",
        "output",
        "result",
    }
    constraint_terms = {
        item
        for item in re.findall(r"[a-z0-9]+", constraint_text)
        if len(item) >= 3 and item not in ignored
    }
    operator_terms = set(re.findall(r"[a-z0-9]+", operator_text))
    if constraint_terms.intersection(operator_terms):
        return True
    semantic_units = {"category", "class", "label"}
    is_semantic_model = bool(
        {"classification", "visual_understanding", "vlm_judgement"}.intersection(
            operator.capability_tags
        )
    )
    return (
        constraint.unit.lower() in semantic_units
        and bool(spec.semantic_requirements)
        and is_semantic_model
    )


def _generate_agent_retrieval_plan(
    state: WorkOrderGraphState,
    *,
    spec: TaskSpecVersion,
    operator_registry: OperatorRegistry,
    allow_draft_candidates: bool,
    available_runtime_backends: frozenset[RuntimeBackend],
    planner: AgentPlanner,
    experience_retriever: PipelineExperienceRetriever | None,
) -> dict:
    def list_catalog(payload: dict) -> dict:
        limit = max(1, min(int(payload.get("limit", 200)), 500))
        return {
            "operators": [
                _operator_catalog_payload(item)
                for item in operator_registry.search(include_drafts=True)[:limit]
            ]
        }

    def search_catalog(payload: dict) -> dict:
        query = str(payload.get("query", "")).strip().lower()
        if not query:
            raise ValueError("query is required")
        query_terms = set(re.findall(r"[a-z0-9]+", query))
        scored: list[tuple[int, object]] = []
        for operator in operator_registry.search(include_drafts=True):
            document = " ".join(
                (
                    operator.id,
                    operator.display_name,
                    operator.summary,
                    operator.description,
                    operator.secondary_category,
                    operator.provider.provider_operator_ref,
                    *sorted(operator.capability_tags),
                )
            ).lower()
            document_terms = set(re.findall(r"[a-z0-9]+", document))
            score = len(query_terms.intersection(document_terms))
            if query in document:
                score += 3
            if score:
                scored.append((score, operator))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        limit = max(1, min(int(payload.get("limit", 30)), 100))
        return {
            "operators": [
                {
                    **_operator_catalog_payload(operator),
                    "retrieval_score": score,
                }
                for score, operator in scored[:limit]
            ],
            "query": query,
        }

    def inspect_dataset_source(payload: dict) -> dict:
        source_index = int(payload.get("source_index", 0))
        try:
            source = spec.data_sources[source_index]
        except IndexError as exc:
            raise ValueError("Unknown TaskSpec data source index") from exc
        if source.type != "local_directory":
            return {
                "source": source.model_dump(mode="json"),
                "inspection": "deferred_to_data_retrieval_adapter",
            }
        root = Path(source.uri)
        if not root.is_dir():
            return {
                "source": source.model_dump(mode="json"),
                "exists": False,
                "asset_count": 0,
                "sample": [],
            }
        files = [item for item in root.rglob("*") if item.is_file()]
        return {
            "source": source.model_dump(mode="json"),
            "exists": True,
            "asset_count": len(files),
            "extensions": sorted(
                {
                    item.suffix.lower()
                    for item in files
                    if item.suffix
                }
            ),
            "sample": [str(item) for item in files[:10]],
        }

    def inspect_operator(payload: dict) -> dict:
        operator_id = str(payload.get("operator_version_id", ""))
        return {
            "operator": _operator_catalog_payload(
                operator_registry.get(operator_id)
            )
        }

    def search_pipeline_experience(payload: dict) -> dict:
        if experience_retriever is None:
            return {
                "matches": [],
                "availability": "history_store_not_configured",
            }
        matches = experience_retriever.search(
            spec,
            owner_id=state["owner_id"],
            include_candidates=bool(payload.get("include_candidates", False)),
            limit=max(1, min(int(payload.get("limit", 5)), 20)),
        )
        return {
            "matches": [
                item.model_dump(mode="json") for item in matches
            ],
            "availability": "available",
        }

    def validate_bundle(payload: dict) -> dict:
        candidate_ids = {
            str(item)
            for item in payload.get("candidate_operator_ids", ())
            if str(item)
        }
        assignments = {
            str(item.get("constraint_id")): str(
                item.get("operator_version_id", "")
            )
            for item in payload.get("constraint_assignments", ())
            if isinstance(item, dict) and item.get("constraint_id")
        }
        errors: list[dict[str, str]] = []
        known_constraints = {item.id: item for item in spec.constraints}
        for constraint in spec.constraints:
            if constraint.hardness != "hard":
                continue
            operator_id = assignments.get(constraint.id)
            if not operator_id:
                errors.append(
                    {
                        "constraint_id": constraint.id,
                        "code": "NO_CANDIDATE_ASSIGNMENT",
                        "message": "No Operator candidate supports this Constraint.",
                    }
                )
                continue
            try:
                operator = operator_registry.get(operator_id)
            except KeyError:
                errors.append(
                    {
                        "constraint_id": constraint.id,
                        "code": "UNKNOWN_OPERATOR",
                        "message": f"Unknown Operator version: {operator_id}",
                    }
                )
                continue
            candidate = _explicit_candidate(
                operator=operator,
                capability=constraint.field,
                available_runtime_backends=available_runtime_backends,
                allow_draft_candidates=allow_draft_candidates,
            )
            if not candidate.executable:
                errors.append(
                    {
                        "constraint_id": constraint.id,
                        "code": "OPERATOR_NOT_EXECUTABLE",
                        "message": candidate.blocked_reason or operator_id,
                    }
                )
                continue
            if not _operator_supports_constraint(operator, constraint, spec):
                errors.append(
                    {
                        "constraint_id": constraint.id,
                        "code": "UNSUPPORTED_CONSTRAINT",
                        "message": (
                            f"{operator_id} metadata/schema does not support "
                            f"{constraint.field}; retrieve another candidate."
                        ),
                    }
                )
        required_system_capabilities = {
            action
            for action in spec.output_actions
            if any(
                operator.primary_category == OperatorCategory.OUTPUT
                and (
                    action in operator.capability_tags
                    or action == operator.secondary_category
                )
                for operator in operator_registry.search(include_drafts=True)
            )
        }
        for capability in sorted(required_system_capabilities):
            supporting = []
            for candidate_id in candidate_ids:
                try:
                    operator = operator_registry.get(candidate_id)
                except KeyError:
                    continue
                if (
                    capability in operator.capability_tags
                    or capability == operator.secondary_category
                ):
                    supporting.append(candidate_id)
            if not supporting:
                errors.append(
                    {
                        "constraint_id": f"system:{capability}",
                        "code": "MISSING_SYSTEM_CAPABILITY",
                        "message": (
                            f"Candidate bundle must include an executable "
                            f"Operator for system capability {capability}."
                        ),
                    }
                )
        unknown = set(assignments).difference(known_constraints)
        for constraint_id in sorted(unknown):
            errors.append(
                {
                    "constraint_id": constraint_id,
                    "code": "UNKNOWN_CONSTRAINT",
                    "message": "Assignment references an unknown Constraint.",
                }
            )
        return {"ok": not errors, "errors": errors}

    loop = AgentDecisionLoop(
        agent_name="retrieval",
        planner=planner,
        tools=(
            AgentTool(
                name="inspect_dataset_source",
                description=(
                    "Inspect one confirmed data source before selecting "
                    "Operator candidates."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "source_index": {
                            "type": "integer",
                            "minimum": 0,
                        }
                    },
                    "additionalProperties": False,
                },
                execute=inspect_dataset_source,
            ),
            AgentTool(
                name="search_operator_catalog",
                description=(
                    "Search Operator descriptions, capability tags, provider "
                    "references, and schemas using a model-chosen query."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                execute=search_catalog,
            ),
            AgentTool(
                name="list_operator_catalog",
                description=(
                    "List governed Operator metadata. This is recall evidence, "
                    "not a Pipeline decision."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 500,
                        }
                    },
                    "additionalProperties": False,
                },
                execute=list_catalog,
            ),
            AgentTool(
                name="inspect_operator",
                description=(
                    "Read one Operator's exact capability and parameter schema."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "operator_version_id": {"type": "string"}
                    },
                    "required": ["operator_version_id"],
                    "additionalProperties": False,
                },
                execute=inspect_operator,
            ),
            AgentTool(
                name="search_pipeline_experience",
                description=(
                    "Retrieve historical Pipeline and experiment evidence for "
                    "the confirmed TaskSpec."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "include_candidates": {"type": "boolean"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                        },
                    },
                    "additionalProperties": False,
                },
                execute=search_pipeline_experience,
            ),
        ),
        max_iterations=15,
        finish_validator=validate_bundle,
    )
    result = loop.run(
        goal=(
            "Find governed Operator candidates for every hard Constraint. "
            "Report candidate_operator_ids, constraint_assignments, sufficient, "
            "and gaps. Do not design Pipeline order or parameters."
        ),
        context={
            "task_spec": spec.model_dump(mode="json"),
            "available_runtime_backends": sorted(
                item.value for item in available_runtime_backends
            ),
            "allow_draft_candidates": allow_draft_candidates,
            "finish_contract": {
                "candidate_operator_ids": ["string"],
                "constraint_assignments": [
                    {
                        "constraint_id": "string",
                        "operator_version_id": "string",
                    }
                ],
                "sufficient": "boolean",
                "gaps": ["string"],
            },
            "required_system_capabilities": [
                action
                for action in spec.output_actions
                if any(
                    operator.primary_category == OperatorCategory.OUTPUT
                    and (
                        action in operator.capability_tags
                        or action == operator.secondary_category
                    )
                    for operator in operator_registry.search(
                        include_drafts=True
                    )
                )
            ],
        },
    )
    output = result.output
    requested_ids = tuple(
        dict.fromkeys(
            str(item)
            for item in output.get("candidate_operator_ids", ())
            if str(item)
        )
    )
    assignments = {
        str(item.get("constraint_id")): str(
            item.get("operator_version_id", "")
        )
        for item in output.get("constraint_assignments", ())
        if isinstance(item, dict) and item.get("constraint_id")
    }
    candidates_by_id: dict[str, OperatorCatalogMatch] = {}
    coverage: list[CapabilityCoverage] = []
    for constraint in spec.constraints:
        operator_id = assignments.get(constraint.id)
        candidate = None
        if operator_id:
            operator = operator_registry.get(operator_id)
            candidate = _explicit_candidate(
                operator=operator,
                capability=constraint.field,
                available_runtime_backends=available_runtime_backends,
                allow_draft_candidates=allow_draft_candidates,
            )
            candidates_by_id[operator_id] = candidate
        status = (
            CapabilityCoverageStatus.COVERED
            if candidate is not None and candidate.executable
            else CapabilityCoverageStatus.BLOCKED
            if candidate is not None
            else CapabilityCoverageStatus.MISSING
        )
        evidence = ()
        if candidate is not None:
            evidence = (
                CapabilityCandidateEvidence(
                    operator_version_id=candidate.operator_version_id,
                    provider_id=candidate.provider_id,
                    provider_operator_ref=candidate.provider_operator_ref,
                    runtime_backend=candidate.runtime_backend.value,
                    lifecycle_status=candidate.status.value,
                    executable=candidate.executable,
                    score=candidate.score,
                    blocked_reason=candidate.blocked_reason,
                ),
            )
        coverage.append(
            CapabilityCoverage(
                capability_id=constraint.id,
                capability=constraint.field,
                description=constraint.source_text,
                required=constraint.hardness == "hard",
                status=status,
                selected_operator_version_id=(
                    candidate.operator_version_id if candidate else None
                ),
                candidates=evidence,
            )
        )
    for operator_id in requested_ids:
        if operator_id in candidates_by_id:
            continue
        operator = operator_registry.get(operator_id)
        candidates_by_id[operator_id] = _explicit_candidate(
            operator=operator,
            capability=operator.secondary_category,
            available_runtime_backends=available_runtime_backends,
            allow_draft_candidates=allow_draft_candidates,
        )
    sufficient = bool(coverage) and all(
        not item.required
        or item.status == CapabilityCoverageStatus.COVERED
        for item in coverage
    )
    previous_plan = state.get("retrieval_plan")
    routes = tuple(
        {
            "source_type": source.type,
            "source_uri": source.uri,
            "collection": source.collection,
        }
        for source in spec.data_sources
    )
    plan = RetrievalPlanVersion(
        id=new_id("retrieval_plan"),
        version=int(previous_plan.get("version", 0)) + 1 if previous_plan else 1,
        parent_version_id=previous_plan.get("id") if previous_plan else None,
        created_by=state["owner_id"],
        change_reason="retrieval Agent completed a governed tool loop",
        task_spec_version_id=spec.id,
        routes=routes,
        target_candidate_count=max(len(candidates_by_id), 1),
        sufficient=sufficient,
        operator_candidates=tuple(
            item.model_dump(mode="json")
            for item in candidates_by_id.values()
        ),
        capability_coverage=tuple(coverage),
    )
    observation = {
        "agent": "retrieval",
        "status": result.status,
        "summary": result.decisions[-1].reason_summary,
        "tool_observations": [
            item.model_dump(mode="json") for item in result.observations
        ],
        "gaps": list(output.get("gaps", ())),
    }
    experience_matches = next(
        (
            item.data.get("matches", [])
            for item in reversed(result.observations)
            if item.tool_name == "search_pipeline_experience"
        ),
        [],
    )
    return {
        "retrieval_plan": plan.model_dump(mode="json"),
        "operator_candidates": list(plan.operator_candidates),
        "capability_coverage": [
            item.model_dump(mode="json") for item in coverage
        ],
        "candidate_sufficient": sufficient,
        "pipeline_experience_matches": experience_matches,
        "next_action": (
            "generate_pipeline_candidates" if sufficient else "expand_retrieval"
        ),
        "agent_observations": [
            *state.get("agent_observations", ()),
            observation,
        ],
        "current_agent": "retrieval",
        "trace": append_trace(state, "retrieval:agent_loop_finished"),
    }


def assess_candidate_sufficiency(state: WorkOrderGraphState) -> dict:
    plan = RetrievalPlanVersion.model_validate(state["retrieval_plan"])
    return {
        "candidate_sufficient": plan.sufficient,
        "next_action": "generate_pipeline_candidates"
        if plan.sufficient
        else "expand_retrieval",
        "trace": append_trace(state, "retrieval:candidate_sufficiency_checked"),
    }
