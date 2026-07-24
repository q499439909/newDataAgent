from __future__ import annotations

from ...domain.common import new_id
from ...domain.operators import OperatorStatus, RuntimeBackend
from ...domain.plans import (
    CapabilityCandidateEvidence,
    CapabilityCoverage,
    CapabilityCoverageStatus,
    RetrievalPlanVersion,
)
from ...domain.specs import TaskSpecVersion
from ...operators.catalog_matching import HybridOperatorCatalogMatcher
from ...operators.catalog_ranking import OperatorCandidateRanker, OperatorRankingPolicy
from ...operators.registry import OperatorRegistry
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
) -> tuple[CapabilityCoverage, ...]:
    requested = list(spec.capability_requirements)
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


def generate_retrieval_plan(
    state: WorkOrderGraphState,
    *,
    operator_registry: OperatorRegistry | None = None,
    allow_draft_candidates: bool = False,
    available_runtime_backends: frozenset[RuntimeBackend] = frozenset(
        {RuntimeBackend.CPU}
    ),
) -> dict:
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    effective_runtime_backends = frozenset(
        {
            *available_runtime_backends,
            *(
                RuntimeBackend(item)
                for item in state.get("runtime_backend_overrides", [])
            ),
        }
    )
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


def assess_candidate_sufficiency(state: WorkOrderGraphState) -> dict:
    plan = RetrievalPlanVersion.model_validate(state["retrieval_plan"])
    return {
        "candidate_sufficient": plan.sufficient,
        "next_action": "generate_pipeline_candidates"
        if plan.sufficient
        else "expand_retrieval",
        "trace": append_trace(state, "retrieval:candidate_sufficiency_checked"),
    }
