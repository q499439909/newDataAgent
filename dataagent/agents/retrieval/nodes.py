from __future__ import annotations

from ...domain.common import new_id
from ...domain.plans import RetrievalPlanVersion
from ...domain.specs import TaskSpecVersion
from ...operators.catalog_matching import DataJuicerCatalogMatcher
from ...operators.registry import OperatorRegistry
from ..shared import WorkOrderGraphState, append_trace


def generate_retrieval_plan(
    state: WorkOrderGraphState,
    *,
    operator_registry: OperatorRegistry | None = None,
    allow_draft_candidates: bool = False,
) -> dict:
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    operator_candidates = (
        DataJuicerCatalogMatcher(operator_registry).match(
            spec.objective,
            required_capabilities=spec.required_capabilities,
            capability_requirements=spec.capability_requirements,
            allow_draft_candidates=allow_draft_candidates,
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
    blocked_candidates = [
        item for item in operator_candidates if not item.executable
    ]
    plan = RetrievalPlanVersion(
        id=new_id("retrieval_plan"),
        version=1,
        created_by=state["owner_id"],
        change_reason="initial retrieval planning",
        task_spec_version_id=spec.id,
        routes=routes,
        target_candidate_count=target,
        sufficient=not blocked_candidates,
        operator_candidates=tuple(
            item.model_dump(mode="json") for item in operator_candidates
        ),
    )
    return {
        "retrieval_plan": plan.model_dump(mode="json"),
        "operator_candidates": [
            item.model_dump(mode="json") for item in operator_candidates
        ],
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
