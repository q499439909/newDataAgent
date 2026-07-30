from __future__ import annotations

from typing import Any

from ..runner import AgentPlanner, AgentPlanningRequest
from ..shared import WorkOrderGraphState
from ..shared.state import CURRENT_AGENT_STATE_VERSION
from ..turn import AgentAction
from .guards import (
    allowed_requirement_actions,
    finish_policy_violation,
    next_requirement_action,
)


_TASK_ACTIONS = (
    "run_requirement_agent",
    "confirm_task_spec",
    "run_retrieval_agent",
    "resolve_capability_gaps",
    "confirm_operator_plan",
    "run_processing_agent",
    "approve_pipeline",
    "trial_selected_pipeline",
    "resolve_pipeline_trial",
    "run_strategy_agent",
    "complete_work_order",
    "retry_failed_assets",
    "rerun_pipeline",
    "reretrieve_candidates",
    "recompile_pipeline",
    "ask_user",
    "finish_planning",
    "terminate",
)


def build_task_plan(state: WorkOrderGraphState) -> list[dict[str, str]]:
    task_spec_ready = bool(state.get("task_spec"))
    confirmed = bool(state.get("task_spec_confirmed"))
    retrieved = bool(state.get("candidate_sufficient"))
    operator_plan_confirmed = bool(state.get("operator_plan_confirmed"))
    compiled = bool(state.get("representative_pipelines"))
    approved = bool(state.get("selected_pipeline_id"))
    trial_status = (state.get("selected_pipeline_trial") or {}).get("status")
    trial_ready = trial_status in {"passed", "static_validation_passed"}
    strategy_ready = bool(state.get("sampling_plan"))
    outcome = state.get("latest_run_observation") or {}
    run_id = outcome.get("run_id")
    resolved = bool(run_id and run_id in state.get("resolved_run_ids", ()))
    active_run_id = state.get("active_run_id")
    active_run = bool(active_run_id and active_run_id != run_id)
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
            "id": "confirm_operator_plan",
            "label": "Confirm capability coverage and Operator plan",
            "status": (
                "completed"
                if operator_plan_confirmed
                else "in_progress"
                if retrieved
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
                if operator_plan_confirmed
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
            "id": "trial_selected_pipeline",
            "label": "Trial the selected Pipeline on a bounded sample",
            "status": (
                "completed"
                if trial_ready
                else "in_progress"
                if approved
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
                if trial_ready
                else "pending"
            ),
        },
        {
            "id": "submit_dataset_run",
            "label": "Submit the approved dataset run",
            "status": (
                "completed"
                if active_run or outcome
                else "ready"
                if strategy_ready
                else "pending"
            ),
        },
        {
            "id": "execute_dataset",
            "label": "Execute the approved Pipeline",
            "status": (
                "completed"
                if outcome and not active_run
                else "in_progress"
                if active_run
                else "pending"
            ),
        },
        {
            "id": "evaluate_quality",
            "label": "Evaluate the produced DatasetVersion",
            "status": (
                "completed" if outcome and not active_run else "pending"
            ),
        },
        {
            "id": "resolve_run_outcome",
            "label": "Resolve the formal Run outcome",
            "status": (
                "completed"
                if resolved
                else "in_progress"
                if outcome
                else "pending"
            ),
        },
    ]


def decide_requirement_agent_turn(
    state: WorkOrderGraphState,
    *,
    planner: AgentPlanner | None = None,
) -> dict[str, Any]:
    """Return one governed root-Agent decision from current WorkOrder facts."""

    if planner is None:
        action, reason = next_requirement_action(state)
        source = "deterministic_fallback"
    else:
        allowed_actions = allowed_requirement_actions(state)
        if len(allowed_actions) == 1:
            action = allowed_actions[0]
            reason = (
                "The governed WorkOrder state permits exactly one next action."
            )
            source = "policy"
            model_decision = None
        else:
            action, reason, source, model_decision = _plan_allowed_action(
                state,
                planner,
                allowed_actions,
            )
    prior_decisions = list(state.get("requirement_agent_decisions", ()))
    decision = {
        "sequence": len(prior_decisions) + 1,
        "action": action,
        "reason_summary": reason,
        "source": source,
    }
    decisions = [*prior_decisions, decision]
    agent_action = _to_agent_action(action, reason)
    return {
        "agent_state_version": CURRENT_AGENT_STATE_VERSION,
        "current_agent": "requirement",
        "requirement_agent_action": action,
        "requirement_agent_decisions": decisions,
        "agent_action": agent_action.model_dump(mode="json"),
        "task_plan": build_task_plan(state),
    }


def _plan_allowed_action(
    state: WorkOrderGraphState,
    planner: AgentPlanner,
    allowed_actions: tuple[str, ...],
) -> tuple[str, str, str, Any]:
        policy_observations: list[dict[str, str]] = []
        model_decision = None
        action = ""
        for _attempt in range(3):
            prior_decisions = state.get("requirement_agent_decisions", ())
            model_decision = planner.decide(
                AgentPlanningRequest(
                    agent_name="requirement",
                    goal=(
                        "Advance the WorkOrder toward an accepted, executable "
                        "data production plan. Select exactly one allowed task "
                        "action."
                    ),
                    iteration=len(prior_decisions) + 1,
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
                        "operator_plan": state.get("operator_plan"),
                        "operator_plan_confirmed": bool(
                            state.get("operator_plan_confirmed")
                        ),
                        "representative_pipelines": state.get(
                            "representative_pipelines", ()
                        ),
                        "selected_pipeline_id": state.get(
                            "selected_pipeline_id"
                        ),
                        "sampling_plan": state.get("sampling_plan"),
                        "latest_run_observation": state.get(
                            "latest_run_observation"
                        ),
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
            finish_violation = finish_policy_violation(state, action)
            if (
                model_decision.action in {"tool", "finish"}
                and action in allowed_actions
                and finish_violation is None
            ):
                break
            if finish_violation is not None:
                policy_observations.append(finish_violation)
                continue
            policy_observations.append(
                {
                    "error": "ACTION_NOT_ALLOWED",
                    "proposed": action or model_decision.action,
                    "allowed": ", ".join(allowed_actions),
                }
            )
        else:
            raise ValueError(
                "Requirement Agent proposed an action outside the current policy: "
                f"{action or getattr(model_decision, 'action', '')}"
            )
        assert model_decision is not None
        reason = model_decision.reason_summary
        source = "model"
        return action, reason, source, model_decision


def _to_agent_action(action: str, reason: str) -> AgentAction:
    delegate_targets = {
        "run_requirement_agent": "requirement_planning_agent",
        "run_retrieval_agent": "retrieval_agent",
        "run_processing_agent": "processing_agent",
        "run_strategy_agent": "strategy_agent",
        "reretrieve_candidates": "retrieval_agent",
        "recompile_pipeline": "processing_agent",
    }
    if action in delegate_targets:
        return AgentAction(
            kind="delegate",
            target=delegate_targets[action],
            objective=reason,
            reason=reason,
        )
    if action in {
        "confirm_task_spec",
        "confirm_operator_plan",
        "approve_pipeline",
        "resolve_pipeline_trial",
        "resolve_capability_gaps",
        "ask_user",
    }:
        return AgentAction(
            kind="ask_user",
            target=action,
            objective=reason,
            reason=reason,
        )
    if action in {"finish_planning", "complete_work_order", "terminate"}:
        return AgentAction(kind="finish", reason=reason)
    return AgentAction(
        kind="call_tool",
        target=action,
        objective=reason,
        reason=reason,
    )


__all__ = ["build_task_plan", "decide_requirement_agent_turn"]
