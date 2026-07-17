from __future__ import annotations

from ...domain.common import new_id
from ...domain.plans import SamplingPlanVersion
from ...domain.specs import TaskSpecVersion
from ..shared import WorkOrderGraphState, append_trace


def generate_sampling_plan(state: WorkOrderGraphState) -> dict:
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    plan = SamplingPlanVersion(
        id=new_id("sampling_plan"),
        version=1,
        created_by=state["owner_id"],
        change_reason="initial data strategy",
        task_spec_version_id=spec.id,
        quota=spec.quotas,
        priority_weights={"uncertain_sample": 1.5, "failure_neighbor": 1.8},
        diversity_constraints={
            "max_per_visual_cluster": 50,
            "cross_split_near_duplicate": False,
        },
        random_seed=42,
    )
    return {
        "sampling_plan": plan.model_dump(mode="json"),
        "current_agent": "strategy",
        "next_action": "submit_dataset_run",
        "trace": append_trace(state, "strategy:sampling_plan_generated"),
    }
