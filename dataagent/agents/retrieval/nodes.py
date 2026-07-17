from __future__ import annotations

from ...domain.common import new_id
from ...domain.plans import RetrievalPlanVersion
from ...domain.specs import TaskSpecVersion
from ..shared import WorkOrderGraphState, append_trace


def generate_retrieval_plan(state: WorkOrderGraphState) -> dict:
    spec = TaskSpecVersion.model_validate(state["task_spec"])
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
    plan = RetrievalPlanVersion(
        id=new_id("retrieval_plan"),
        version=1,
        created_by=state["owner_id"],
        change_reason="initial retrieval planning",
        task_spec_version_id=spec.id,
        routes=routes,
        target_candidate_count=target,
        sufficient=True,
    )
    return {
        "retrieval_plan": plan.model_dump(mode="json"),
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
