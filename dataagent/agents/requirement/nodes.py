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
from ..runner import AgentPlanner, AgentRunner, AgentTool
from ..shared import WorkOrderGraphState, append_trace
from .planner import RequirementPlanner, RequirementPlanningRequest


def _requirement_messages(state: WorkOrderGraphState) -> tuple[dict, ...]:
    messages = tuple(state.get("requirement_messages", ()))
    if messages:
        return messages
    return ({"role": "user", "content": state["requirement"]},)


def _grounding_text(state: WorkOrderGraphState) -> str:
    return "\n".join(
        str(item.get("content", "")).strip()
        for item in _requirement_messages(state)
        if item.get("role") == "user"
        and item.get("requirement_relevant", True)
        and str(item.get("content", "")).strip()
    )


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
    persisted_draft = state.get("requirement_draft")
    if persisted_draft:
        draft = RequirementDraft.model_validate(persisted_draft)
        if not draft.gaps:
            spec = _task_spec_from_draft(state, sources, draft)
            return {
                "task_spec": spec.model_dump(mode="json"),
                "current_agent": "requirement",
                "next_action": "confirm_task_spec",
                "trace": append_trace(
                    state,
                    "requirement:persisted_draft_compiled",
                ),
            }
    if requirement_planner is not None:
        draft = requirement_planner.plan(
            RequirementPlanningRequest(
                requirement=_grounding_text(state),
                data_sources=tuple(state["data_sources"]),
                work_order_id=state["work_order_id"],
                messages=_requirement_messages(state),
            )
        )
        if draft.gaps:
            return _requirement_gap_result(
                state,
                draft,
                trace_event="requirement:structured_gaps_identified",
            )
        spec = _task_spec_from_draft(state, sources, draft)
        return {
            "task_spec": spec.model_dump(mode="json"),
            "current_agent": "requirement",
            "next_action": "confirm_task_spec",
            "trace": append_trace(state, "requirement:draft_planned"),
        }
    if agent_planner is not None:
        return _generate_agent_task_spec(state, sources, agent_planner)

    raise RuntimeError(
        "REQUIREMENT_PLANNER_UNAVAILABLE: Requirement planning requires an "
        "Agent planner or RequirementPlanningGateway; deterministic keyword "
        "fallbacks are intentionally disabled."
    )


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
            _grounding_text(state),
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

    loop = AgentRunner(
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
            "requirement": _grounding_text(state),
            "messages": list(_requirement_messages(state)),
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
    if validated_draft.gaps:
        result = _requirement_gap_result(
            state,
            validated_draft,
            trace_event="requirement:structured_gaps_identified",
        )
        result["agent_observations"] = [
            *state.get("agent_observations", ()),
            observation,
        ]
        return result
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


def _requirement_gap_result(
    state: WorkOrderGraphState,
    draft: RequirementDraft,
    *,
    trace_event: str,
) -> dict:
    return {
        "requirement_draft": draft.model_dump(mode="json"),
        "requirement_clarification_request": {
            "summary": "The Requirement Agent found blocking semantic gaps.",
            "questions": [
                {
                    "gap_id": gap.id,
                    "question": gap.question,
                    "answer_schema": gap.answer_schema,
                }
                for gap in draft.gaps
            ],
            "gaps": [
                item.model_dump(mode="json")
                for item in draft.gaps
            ],
        },
        "current_agent": "requirement",
        "next_action": "clarify_requirement",
        "trace": append_trace(state, trace_event),
    }


def clarify_requirement(
    state: WorkOrderGraphState,
    *,
    requirement_planner: RequirementPlanner | None = None,
) -> dict:
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
    resolver = getattr(requirement_planner, "resolve_gaps", None)
    resolution: dict | None = None
    if callable(resolver):
        try:
            resolution = resolver(
                gaps=tuple(request.get("gaps", ())),
                answer=answer.strip(),
                messages=_requirement_messages(state),
                draft=dict(state.get("requirement_draft") or {}),
                requirement=(
                    f"{_grounding_text(state)}\n{answer.strip()}".strip()
                ),
                data_sources=tuple(state.get("data_sources", ())),
            )
        except Exception:
            resolution = {
                "reply": (
                    "我暂时无法可靠解析这次回答，原有需求缺口已保留。"
                    "请直接回答下面的问题；本轮不会重新生成整份 TaskSpec。"
                ),
                "requirement_relevant": False,
                "resolutions": [
                    {
                        "gap_id": item.get("id"),
                        "resolved": False,
                        "normalized_answer": "",
                    }
                    for item in request.get("gaps", ())
                    if isinstance(item, dict)
                ],
            }
    answer_message = {
        "role": "user",
        "content": answer.strip(),
        "responding_to": [
            item.get("gap_id")
            for item in request.get("questions", ())
            if isinstance(item, dict) and item.get("gap_id")
        ],
    }
    if resolution is not None:
        answer_message["requirement_relevant"] = bool(
            resolution.get("requirement_relevant", True)
        )
    messages = [
        *_requirement_messages(state),
        answer_message,
    ]
    if resolution is not None:
        resolved_ids = {
            str(item.get("gap_id"))
            for item in resolution.get("resolutions", ())
            if isinstance(item, dict) and item.get("resolved") is True
        }
        unresolved_gaps = [
            item
            for item in request.get("gaps", ())
            if isinstance(item, dict) and str(item.get("id")) not in resolved_ids
        ]
        if unresolved_gaps:
            unresolved_ids = {str(item.get("id")) for item in unresolved_gaps}
            questions = [
                item
                for item in request.get("questions", ())
                if isinstance(item, dict)
                and str(item.get("gap_id")) in unresolved_ids
            ]
            return {
                "requirement_messages": messages,
                "requirement_clarification_request": {
                    "summary": (
                        f"我已理解并保存本轮补充，但仍有 {len(unresolved_gaps)} "
                        "项需求缺口没有满足。请继续回答下列问题；"
                        "在信息完整前不会进入算子检索或 Pipeline 编排。"
                    ),
                    "questions": questions,
                    "gaps": unresolved_gaps,
                },
                "next_action": "clarify_requirement",
                "trace": append_trace(
                    state,
                    "requirement:clarification_still_open",
                ),
            }
        revised_payload = resolution.get("revised_draft")
        if isinstance(revised_payload, dict):
            revised_draft = RequirementDraft.model_validate(revised_payload)
            if revised_draft.gaps:
                raise ValueError(
                    "Resolved Requirement gaps produced a draft with open gaps"
                )
            return {
                "requirement_messages": messages,
                "requirement_draft": revised_draft.model_dump(mode="json"),
                "requirement_clarification_request": {},
                "next_action": "generate_task_spec",
                "trace": append_trace(
                    state,
                    "requirement:clarification_revised_draft",
                ),
            }
    return {
        "requirement_messages": messages,
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
        gaps=draft.gaps,
        acceptance=AcceptanceSpec(boundary_review_size=20),
    )


def validate_task_spec(state: WorkOrderGraphState) -> dict:
    TaskSpecVersion.model_validate(state["task_spec"])
    return {
        "trace": append_trace(state, "requirement:task_spec_validated"),
    }
