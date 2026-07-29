from __future__ import annotations

from langgraph.types import interrupt

from ...domain.common import new_id
from ...domain.specs import (
    AcceptanceSpec,
    DataSourceSpec,
    RequirementDraft,
    TaskSpecVersion,
    validate_requirement_draft_grounding,
)
from ...domain.specs.constraints import parse_requirement_contract
from ...operators.planning import (
    decompose_task_capabilities,
    infer_output_actions,
    infer_required_capabilities,
)
from ..runtime import AgentDecisionLoop, AgentPlanner, AgentTool
from ..shared import WorkOrderGraphState, append_trace
from .clarification import infer_task_ambiguities
from .planner import RequirementPlanner, RequirementPlanningRequest


def generate_task_spec(
    state: WorkOrderGraphState,
    *,
    requirement_planner: RequirementPlanner | None = None,
    agent_planner: AgentPlanner | None = None,
) -> dict:
    if state.get("task_spec"):
        return {
            "current_agent": "requirement",
            "next_action": "confirm_task_spec",
            "trace": append_trace(state, "requirement:reuse_task_spec"),
        }
    sources = tuple(DataSourceSpec.model_validate(item) for item in state["data_sources"])
    if agent_planner is not None:
        return _generate_agent_task_spec(state, sources, agent_planner)
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


def _generate_agent_task_spec(
    state: WorkOrderGraphState,
    sources: tuple[DataSourceSpec, ...],
    planner: AgentPlanner,
) -> dict:
    validated_draft: RequirementDraft | None = None

    def validate_draft(payload: dict) -> dict:
        nonlocal validated_draft
        draft = RequirementDraft.model_validate(payload)
        observation = validate_requirement_draft_grounding(
            state["requirement"],
            draft,
        )
        if observation.ok:
            validated_draft = draft
        return observation.model_dump(mode="json")

    def validate_finish(output: dict) -> dict:
        del output
        if validated_draft is None:
            return {
                "ok": False,
                "violations": [
                    {
                        "code": "NO_VALIDATED_REQUIREMENT_DRAFT",
                        "source_text": "",
                        "message": (
                            "Validate a complete RequirementDraft before finishing"
                        ),
                    }
                ],
            }
        return {"ok": True}

    loop = AgentDecisionLoop(
        agent_name="requirement_planning",
        planner=planner,
        tools=(
            AgentTool(
                name="validate_requirement_draft",
                description=(
                    "Validate an implementation-neutral RequirementDraft against "
                    "the user's exact source clauses. Definitions qualify existing "
                    "Constraints through ClauseTrace references; this tool never "
                    "chooses Operators or repairs the draft."
                ),
                input_schema=RequirementDraft.model_json_schema(),
                execute=validate_draft,
                summarize_input=lambda payload: {
                    "objective": payload.get("objective"),
                    "constraint_count": len(payload.get("constraints", ())),
                    "clause_trace_count": len(payload.get("clause_traces", ())),
                },
            ),
        ),
        max_iterations=15,
        finish_validator=validate_finish,
    )
    result = loop.run(
        goal=(
            "Produce a complete source-grounded RequirementDraft. Represent every "
            "independently testable condition as a Constraint and classify every "
            "meaningful clause with a ClauseTrace. Definitions and qualifiers must "
            "reference existing Constraints rather than inventing capabilities. "
            "Interpret only the requirement text and declared data-source references; "
            "do not inspect or require access to source files during requirement "
            "planning. Record genuinely blocking semantic ambiguity in the draft or "
            "ask the user, but do not treat unavailable file-inspection tools as a "
            "requirement gap."
        ),
        context={
            "requirement": state["requirement"],
            "data_sources": list(state["data_sources"]),
        },
    )
    observation = {
        "agent": "requirement",
        "status": result.status,
        "summary": result.decisions[-1].reason_summary,
        "tool_observations": [
            item.model_dump(mode="json") for item in result.observations
        ],
    }
    if result.status in {"needs_user", "gap"}:
        return {
            "requirement_clarification_request": {
                **dict(result.output),
                "summary": result.output.get("summary")
                or result.decisions[-1].reason_summary,
            },
            "agent_observations": [
                *state.get("agent_observations", ()),
                observation,
            ],
            "current_agent": "requirement",
            "next_action": "clarify_requirement",
            "trace": append_trace(
                state,
                "requirement:clarification_requested",
            ),
        }
    if result.status != "finished" or validated_draft is None:
        raise ValueError(
            "Requirement Agent finished without a validated RequirementDraft"
        )
    spec = _task_spec_from_draft(state, sources, validated_draft)
    return {
        "task_spec": spec.model_dump(mode="json"),
        "agent_observations": [
            *state.get("agent_observations", ()),
            observation,
        ],
        "current_agent": "requirement",
        "next_action": "confirm_task_spec",
        "trace": append_trace(state, "requirement:agent_loop_finished"),
    }


def clarify_requirement(state: WorkOrderGraphState) -> dict:
    request = state.get("requirement_clarification_request", {})
    response = interrupt(
        {
            "kind": "requirement_clarification",
            "work_order_id": state["work_order_id"],
            "questions": request.get("questions", ()),
            "summary": request.get("summary")
            or "The Requirement Agent needs additional information.",
            "allowed_actions": ["answer", "terminate"],
        }
    )
    if not isinstance(response, dict):
        raise ValueError("Requirement clarification requires an object response")
    answer = response.get("answer") or response.get("message")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Requirement clarification requires a non-empty answer")
    return {
        "requirement": (
            state["requirement"].rstrip()
            + "\n用户补充："
            + answer.strip()
        ),
        "requirement_clarification_request": {},
        "next_action": "generate_task_spec",
        "trace": append_trace(state, "requirement:clarification_received"),
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
        clause_traces=draft.clause_traces,
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
