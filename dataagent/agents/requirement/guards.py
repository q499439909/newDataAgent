from __future__ import annotations

from ..shared import WorkOrderGraphState
from ..turn import WorkOrderLiveness


def next_requirement_action(
    state: WorkOrderGraphState,
) -> tuple[str, str]:
    if state.get("terminated"):
        return "terminate", "The WorkOrder was terminated."
    if state.get("next_action") == "edit_task_spec":
        return "finish_planning", "Task revision requires a new user turn."
    outcome = state.get("latest_run_observation") or {}
    if (
        outcome.get("run_id")
        and outcome.get("run_id") not in state.get("resolved_run_ids", ())
    ):
        return (
            "finish_planning",
            "A formal Run outcome awaits a Requirement Agent decision.",
        )
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
        return "approve_pipeline", "Validated Pipeline candidates are ready."
    if not state.get("sampling_plan"):
        return "run_strategy_agent", "The approved Pipeline needs a data strategy."
    return "finish_planning", "All planning completion gates are satisfied."


def allowed_requirement_actions(
    state: WorkOrderGraphState,
) -> tuple[str, ...]:
    if state.get("terminated"):
        return ("terminate",)
    if state.get("next_action") == "edit_task_spec":
        return ("finish_planning", "terminate")
    outcome = state.get("latest_run_observation") or {}
    if (
        outcome.get("run_id")
        and outcome.get("run_id") not in state.get("resolved_run_ids", ())
    ):
        if (
            outcome.get("run_status") == "SUCCEEDED"
            and outcome.get("qc_status") == "PASSED"
        ):
            return ("complete_work_order", "ask_user", "terminate")
        if outcome.get("run_status") == "CANCELLED":
            return ("rerun_pipeline", "ask_user", "terminate")
        actions = [
            "reretrieve_candidates",
            "recompile_pipeline",
            "ask_user",
            "terminate",
        ]
        if outcome.get("retryable"):
            actions.insert(0, "retry_failed_assets")
        return tuple(actions)
    if not state.get("task_spec"):
        return ("run_requirement_agent", "terminate")
    if not state.get("task_spec_confirmed"):
        return ("confirm_task_spec", "terminate")
    if not state.get("retrieval_plan"):
        return ("run_retrieval_agent", "terminate")
    if not state.get("candidate_sufficient"):
        return (
            "run_retrieval_agent",
            "resolve_capability_gaps",
            "terminate",
        )
    if not state.get("representative_pipelines"):
        return (
            "run_processing_agent",
            "run_retrieval_agent",
            "terminate",
        )
    if not state.get("selected_pipeline_id"):
        return (
            "approve_pipeline",
            "run_processing_agent",
            "run_retrieval_agent",
            "terminate",
        )
    if not state.get("sampling_plan"):
        return (
            "run_strategy_agent",
            "run_processing_agent",
            "terminate",
        )
    return (
        "finish_planning",
        "run_strategy_agent",
        "run_processing_agent",
        "terminate",
    )


def finish_policy_violation(
    state: WorkOrderGraphState,
    action: str,
) -> dict[str, str] | None:
    if action != "finish_planning":
        return None
    expected, reason = next_requirement_action(state)
    if expected == "finish_planning":
        return None
    return {
        "error": "FINISH_BEFORE_COMPLETION_GATES",
        "proposed": action,
        "required_next_action": expected,
        "message": reason,
    }


def assess_work_order_liveness(
    state: WorkOrderGraphState,
) -> WorkOrderLiveness:
    if state.get("terminated") or state.get("next_action") in {
        "completed",
        "terminated",
    }:
        return WorkOrderLiveness(
            status="terminal",
            reason="The WorkOrder has reached a terminal state.",
        )

    waiting = state.get("waiting")
    if waiting:
        waiting_actions = {
            "requirement_clarification": "run_requirement_agent",
            "task_spec_confirmation": "confirm_task_spec",
            "pipeline_approval": "approve_pipeline",
            "capability_resolution": "resolve_capability_gaps",
            "run_outcome_resolution": "ask_user",
        }
        return WorkOrderLiveness(
            status="waiting",
            reason=f"The WorkOrder is waiting for {waiting}.",
            required_action=waiting_actions.get(waiting, str(state.get("next_action") or waiting)),
        )

    action, reason = next_requirement_action(state)
    if action == "terminate":
        return WorkOrderLiveness(
            status="terminal",
            reason=reason,
        )
    return WorkOrderLiveness(
        status="runnable",
        reason=reason,
        required_action=action,
    )


__all__ = [
    "allowed_requirement_actions",
    "assess_work_order_liveness",
    "finish_policy_violation",
    "next_requirement_action",
]
