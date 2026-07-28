from __future__ import annotations

from ...domain.common import new_id
from ...domain.specs import (
    AcceptanceSpec,
    DataSourceSpec,
    RequirementDraft,
    TaskSpecVersion,
)
from ...domain.specs.constraints import parse_requirement_contract
from ...operators.planning import (
    decompose_task_capabilities,
    infer_output_actions,
    infer_required_capabilities,
)
from ..shared import WorkOrderGraphState, append_trace
from .clarification import infer_task_ambiguities
from .planner import RequirementPlanner, RequirementPlanningRequest


def generate_task_spec(
    state: WorkOrderGraphState,
    *,
    requirement_planner: RequirementPlanner | None = None,
) -> dict:
    if state.get("task_spec"):
        return {
            "current_agent": "requirement",
            "trace": append_trace(state, "requirement:reuse_task_spec"),
        }
    sources = tuple(DataSourceSpec.model_validate(item) for item in state["data_sources"])
    if requirement_planner is not None:
        draft = requirement_planner.plan(
            RequirementPlanningRequest(
                requirement=state["requirement"],
                data_sources=tuple(state["data_sources"]),
                work_order_id=state["work_order_id"],
            )
        )
        spec = _task_spec_from_draft(state, sources, draft)
        return {
            "task_spec": spec.model_dump(mode="json"),
            "current_agent": "requirement",
            "next_action": "confirm_task_spec",
            "trace": append_trace(state, "requirement:draft_planned"),
        }

    # Compatibility path for callers that have not injected a planner yet.
    contract = parse_requirement_contract(state["requirement"])
    capability_requirements = (
        contract.capability_requirements
        if contract.constraints
        else decompose_task_capabilities(state["requirement"])
    )
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by=state["owner_id"],
        change_reason="initial requirement planning",
        work_order_id=state["work_order_id"],
        objective=state["requirement"],
        planning_origin="legacy_compatibility",
        output_actions=infer_output_actions(capability_requirements),
        required_capabilities=infer_required_capabilities(state["requirement"]),
        capability_requirements=capability_requirements,
        constraints=contract.constraints,
        classification=contract.classification,
        semantic_requirements=contract.semantic_requirements,
        hard_constraints={"preserve_source": True}
        if contract.constraints
        else {},
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


def _task_spec_from_draft(
    state: WorkOrderGraphState,
    sources: tuple[DataSourceSpec, ...],
    draft: RequirementDraft,
) -> TaskSpecVersion:
    return TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by=state["owner_id"],
        change_reason="planned from requirement draft",
        work_order_id=state["work_order_id"],
        objective=draft.objective,
        planning_origin="agent_planner",
        data_sources=sources,
        constraints=draft.constraints,
        classification=draft.classification,
        semantic_requirements=draft.semantic_requirements,
        exclusion_requirements=draft.exclusion_requirements,
        hard_constraints=draft.hard_constraints,
        preferences=draft.preferences,
        ambiguities=draft.ambiguities,
        acceptance=AcceptanceSpec(boundary_review_size=20),
    )


def validate_task_spec(state: WorkOrderGraphState) -> dict:
    TaskSpecVersion.model_validate(state["task_spec"])
    return {
        "trace": append_trace(state, "requirement:task_spec_validated"),
    }
