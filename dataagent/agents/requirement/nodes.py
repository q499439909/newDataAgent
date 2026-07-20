from __future__ import annotations

from ...domain.common import new_id
from ...domain.specs import AcceptanceSpec, DataSourceSpec, TaskSpecVersion
from ...operators.planning import (
    decompose_task_capabilities,
    infer_output_actions,
    infer_required_capabilities,
)
from ..shared import WorkOrderGraphState, append_trace
from .clarification import infer_task_ambiguities


def generate_task_spec(state: WorkOrderGraphState) -> dict:
    if state.get("task_spec"):
        return {
            "current_agent": "requirement",
            "trace": append_trace(state, "requirement:reuse_task_spec"),
        }
    sources = tuple(DataSourceSpec.model_validate(item) for item in state["data_sources"])
    capability_requirements = decompose_task_capabilities(state["requirement"])
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by=state["owner_id"],
        change_reason="initial requirement planning",
        work_order_id=state["work_order_id"],
        objective=state["requirement"],
        output_actions=infer_output_actions(capability_requirements),
        required_capabilities=infer_required_capabilities(state["requirement"]),
        capability_requirements=capability_requirements,
        data_sources=sources,
        acceptance=AcceptanceSpec(boundary_review_size=20),
    )
    spec = spec.model_copy(
        update={
            "ambiguities": infer_task_ambiguities(
                spec.capability_requirements,
                hard_constraints=spec.hard_constraints,
                semantic_requirements=spec.semantic_requirements,
                exclusion_requirements=spec.exclusion_requirements,
                preferences=spec.preferences,
            )
        }
    )
    return {
        "task_spec": spec.model_dump(mode="json"),
        "current_agent": "requirement",
        "next_action": "confirm_task_spec",
        "trace": append_trace(state, "requirement:task_spec_generated"),
    }


def validate_task_spec(state: WorkOrderGraphState) -> dict:
    TaskSpecVersion.model_validate(state["task_spec"])
    return {
        "trace": append_trace(state, "requirement:task_spec_validated"),
    }
