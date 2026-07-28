from __future__ import annotations

from typing import Any

from ..shared import WorkOrderGraphState
from ..runtime import AgentPlanner, AgentPlanningRequest


_TASK_ACTIONS = (
    "run_requirement_agent",
    "confirm_task_spec",
    "run_retrieval_agent",
    "resolve_capability_gaps",
    "run_processing_agent",
    "approve_pipeline",
    "run_strategy_agent",
    "finish_planning",
    "terminate",
)


def _task_plan(state: WorkOrderGraphState) -> list[dict[str, str]]:
    task_spec_ready = bool(state.get("task_spec"))
    confirmed = bool(state.get("task_spec_confirmed"))
    retrieved = bool(state.get("candidate_sufficient"))
    compiled = bool(state.get("representative_pipelines"))
    approved = bool(state.get("selected_pipeline_id"))
    strategy_ready = bool(state.get("sampling_plan"))
    return [
        {
            "id": "understand_requirement",
            "label": "Understand and structure the requirement",
            "status": "completed" if task_spec_ready else "in_progress",
        },
        {
            "id": "confirm_task_spec",
            "label": "Confirm the complete TaskSpec",
            "status": (
                "completed"
                if confirmed
                else "in_progress"
                if task_spec_ready
                else "pending"
            ),
        },
        {
            "id": "retrieve_candidates",
            "label": "Retrieve Operator and Pipeline experience candidates",
            "status": (
                "completed"
                if retrieved
                else "in_progress"
                if confirmed
                else "pending"
            ),
        },
        {
            "id": "compile_pipelines",
            "label": "Compile and validate three PipelineArtifacts",
            "status": (
                "completed"
                if compiled
                else "in_progress"
                if retrieved
                else "pending"
            ),
        },
        {
            "id": "approve_pipeline",
            "label": "Approve one production-eligible PipelineArtifact",
            "status": (
                "completed"
                if approved
                else "in_progress"
                if compiled
                else "pending"
            ),
        },
        {
            "id": "prepare_strategy",
            "label": "Prepare the data strategy",
            "status": (
                "completed"
                if strategy_ready
                else "in_progress"
                if approved
                else "pending"
            ),
        },
        {
            "id": "submit_dataset_run",
            "label": "Submit the approved dataset run",
            "status": "ready" if strategy_ready else "pending",
        },
    ]


def _next_action(state: WorkOrderGraphState) -> tuple[str, str]:
    if state.get("terminated"):
        return "terminate", "The work order was terminated."
    if state.get("next_action") == "edit_task_spec":
        return "finish_planning", "Task revision requires a new user turn."
    if not state.get("task_spec"):
        return "run_requirement_agent", "No structured TaskSpec exists."
    if not state.get("task_spec_confirmed"):
        return "confirm_task_spec", "The TaskSpec requires explicit confirmation."
    if (
        not state.get("retrieval_plan")
        or state.get("next_action") == "run_retrieval_agent"
    ):
        return "run_retrieval_agent", "Confirmed constraints require candidates."
    if not state.get("candidate_sufficient"):
        return (
            "resolve_capability_gaps",
            "Required capability coverage is incomplete.",
        )
    if not state.get("representative_pipelines"):
        return (
            "run_processing_agent",
            "Candidate coverage is sufficient for Pipeline compilation.",
        )
    if not state.get("selected_pipeline_id"):
        return "approve_pipeline", "Three validated Pipeline candidates are ready."
    if not state.get("sampling_plan"):
        return "run_strategy_agent", "The approved Pipeline needs a data strategy."
    return "finish_planning", "All planning completion gates are satisfied."


def _allowed_actions(state: WorkOrderGraphState) -> tuple[str, ...]:
    if state.get("terminated"):
        return ("terminate",)
    if state.get("next_action") == "edit_task_spec":
        return ("finish_planning", "terminate")
    if not state.get("task_spec"):
        return ("run_requirement_agent", "terminate")
    if not state.get("task_spec_confirmed"):
        return ("confirm_task_spec", "terminate")
    if not state.get("retrieval_plan"):
        return ("run_retrieval_agent", "finish_planning", "terminate")
    if not state.get("candidate_sufficient"):
        return (
            "run_retrieval_agent",
            "resolve_capability_gaps",
            "finish_planning",
            "terminate",
        )
    if not state.get("representative_pipelines"):
        return (
            "run_processing_agent",
            "run_retrieval_agent",
            "finish_planning",
            "terminate",
        )
    if not state.get("selected_pipeline_id"):
        return (
            "approve_pipeline",
            "run_processing_agent",
            "run_retrieval_agent",
            "finish_planning",
            "terminate",
        )
    if not state.get("sampling_plan"):
        return (
            "run_strategy_agent",
            "run_processing_agent",
            "finish_planning",
            "terminate",
        )
    return (
        "finish_planning",
        "run_strategy_agent",
        "run_processing_agent",
        "terminate",
    )


def decide_main_agent_turn(
    state: WorkOrderGraphState,
    *,
    planner: AgentPlanner | None = None,
) -> dict[str, Any]:
    """Return one governed task-level decision from the current observations."""

    if planner is None:
        action, reason = _next_action(state)
        source = "deterministic_fallback"
    else:
        allowed_actions = _allowed_actions(state)
        policy_observations: list[dict[str, str]] = []
        model_decision = None
        action = ""
        for _attempt in range(3):
            model_decision = planner.decide(
                AgentPlanningRequest(
                    agent_name="main",
                    goal=(
                        "Advance the WorkOrder toward an accepted, executable "
                        "data production plan. Select exactly one allowed task "
                        "action."
                    ),
                    iteration=len(state.get("main_agent_decisions", ())) + 1,
                    context={
                        "allowed_actions": list(allowed_actions),
                        "task_spec": state.get("task_spec"),
                        "task_spec_confirmed": bool(
                            state.get("task_spec_confirmed")
                        ),
                        "retrieval_plan": state.get("retrieval_plan"),
                        "candidate_sufficient": bool(
                            state.get("candidate_sufficient")
                        ),
                        "representative_pipelines": state.get(
                            "representative_pipelines", ()
                        ),
                        "selected_pipeline_id": state.get(
                            "selected_pipeline_id"
                        ),
                        "sampling_plan": state.get("sampling_plan"),
                        "observations": state.get("agent_observations", ()),
                        "policy_observations": policy_observations,
                    },
                    tools=tuple(
                        {
                            "name": item,
                            "description": (
                                "Governed task-level action executed by "
                                "LangGraph."
                            ),
                            "input_schema": {
                                "type": "object",
                                "additionalProperties": False,
                            },
                        }
                        for item in allowed_actions
                    ),
                    observations=(),
                )
            )
            action = (
                str(model_decision.tool_name)
                if model_decision.action == "tool"
                else str(model_decision.output.get("action", ""))
            )
            if (
                model_decision.action in {"tool", "finish"}
                and action in allowed_actions
            ):
                break
            policy_observations.append(
                {
                    "error": "ACTION_NOT_ALLOWED",
                    "proposed": action or model_decision.action,
                    "allowed": ", ".join(allowed_actions),
                }
            )
        else:
            raise ValueError(
                "Main Agent proposed an action outside the current policy: "
                f"{action or getattr(model_decision, 'action', '')}"
            )
        assert model_decision is not None
        reason = model_decision.reason_summary
        source = "model"
    decision = {
        "sequence": len(state.get("main_agent_decisions", ())) + 1,
        "action": action,
        "reason_summary": reason,
        "source": source,
    }
    return {
        "current_agent": "main",
        "main_agent_action": action,
        "main_agent_decisions": [
            *state.get("main_agent_decisions", ()),
            decision,
        ],
        "task_plan": _task_plan(state),
    }


__all__ = ["decide_main_agent_turn"]
