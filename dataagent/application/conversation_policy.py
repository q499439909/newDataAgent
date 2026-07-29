from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .conversation_actions import ConversationAction, ConversationIntent


@dataclass(frozen=True)
class ActionPolicyViolation:
    code: str
    message: str
    allowed_actions: tuple[str, ...]


_ACTIVE_RUN_STATUSES = {
    "QUEUED",
    "RUNNING",
    "PAUSING",
    "PAUSED",
    "CANCELLING",
    "EVALUATING",
}


def allowed_conversation_actions(context: dict[str, Any]) -> tuple[str, ...]:
    work_order_id = context.get("work_order_id")
    if not work_order_id:
        actions = [ConversationIntent.CHAT, ConversationIntent.START_WORK_ORDER]
        if context.get("pending_requirement"):
            actions.append(ConversationIntent.PROVIDE_SOURCE)
        return tuple(item.value for item in actions)

    state = context.get("agent_state") or {}
    waiting = state.get("waiting")
    next_action = state.get("next_action")
    latest_run = context.get("latest_run") or {}
    run_status = latest_run.get("status")

    actions = [ConversationIntent.CHAT, ConversationIntent.QUERY_CONTROL_FACTS]
    if waiting == "requirement_clarification":
        actions.append(ConversationIntent.CLARIFY_REQUIREMENT)
    elif waiting == "run_outcome_resolution":
        actions.append(ConversationIntent.RESOLVE_RUN_OUTCOME)
    elif waiting == "task_spec_confirmation":
        actions.extend(
            [
                ConversationIntent.EDIT_TASK_SPEC,
                ConversationIntent.APPROVE,
                ConversationIntent.REJECT,
            ]
        )
    elif waiting == "pipeline_approval":
        actions.extend(
            [ConversationIntent.SELECT_PIPELINE, ConversationIntent.REJECT]
        )
    elif waiting == "capability_resolution":
        actions.extend(
            [ConversationIntent.RESOLVE_GAP, ConversationIntent.EDIT_TASK_SPEC]
        )
    elif run_status in _ACTIVE_RUN_STATUSES:
        actions.extend([ConversationIntent.RUN_STATUS, ConversationIntent.CONTROL_RUN])
    else:
        if next_action == "submit_dataset_run":
            actions.append(ConversationIntent.SUBMIT_RUN)
        if context.get("task_spec", {}).get("confirmed"):
            actions.append(ConversationIntent.EDIT_TASK_SPEC)
        if run_status in {"FAILED", "SUCCEEDED", "CANCELLED"}:
            actions.extend(
                [
                    ConversationIntent.RETRY_RUN,
                    ConversationIntent.RERUN_PIPELINE,
                    ConversationIntent.RECOMPILE_PIPELINE,
                    ConversationIntent.SELECT_PIPELINE,
                ]
            )
    return tuple(dict.fromkeys(item.value for item in actions))


def validate_conversation_action(
    action: ConversationAction,
    context: dict[str, Any],
) -> ActionPolicyViolation | None:
    allowed = allowed_conversation_actions(context)
    if action.intent.value not in allowed:
        return ActionPolicyViolation(
            code="ACTION_NOT_ALLOWED_IN_CURRENT_STATE",
            message=(
                f"{action.intent.value} cannot run while the workflow is in its "
                "current state"
            ),
            allowed_actions=allowed,
        )

    state = context.get("agent_state") or {}
    waiting = state.get("waiting")
    if action.intent == ConversationIntent.APPROVE:
        task_spec = context.get("task_spec") or {}
        if waiting != "task_spec_confirmation":
            return ActionPolicyViolation(
                code="APPROVAL_TARGET_MISMATCH",
                message="APPROVE is only valid for the pending TaskSpec confirmation",
                allowed_actions=allowed,
            )
        if task_spec.get("ambiguities"):
            return ActionPolicyViolation(
                code="TASK_SPEC_STILL_AMBIGUOUS",
                message="Answer the pending TaskSpec questions before approving it",
                allowed_actions=allowed,
            )

    if action.intent == ConversationIntent.EDIT_TASK_SPEC:
        latest_run = context.get("latest_run") or {}
        if latest_run.get("status") in _ACTIVE_RUN_STATUSES:
            return ActionPolicyViolation(
                code="ACTIVE_RUN_IMMUTABLE",
                message=(
                    "The active Run is immutable. Ask whether the revision should apply "
                    "after it finishes or whether the current Run should be cancelled first."
                ),
                allowed_actions=allowed,
            )

    latest_run = context.get("latest_run") or {}
    if action.intent == ConversationIntent.RETRY_RUN and latest_run.get(
        "status"
    ) != "FAILED":
        return ActionPolicyViolation(
            code="RUN_IS_NOT_RETRYABLE",
            message="RETRY_RUN requires the latest Run to be FAILED",
            allowed_actions=allowed,
        )

    if action.intent == ConversationIntent.RERUN_PIPELINE:
        eligibility = context.get("latest_run_pipeline_eligibility") or {}
        qc_report = context.get("latest_qc_report") or {}
        if eligibility.get("eligible") is False:
            return ActionPolicyViolation(
                code="PIPELINE_RECOMPILE_REQUIRED",
                message=(
                    "The previous Pipeline is no longer eligible. Use "
                    "RECOMPILE_PIPELINE with the current Catalog instead of rerunning it."
                ),
                allowed_actions=allowed,
            )
        if qc_report.get("semantic_quality_verified") is False:
            return ActionPolicyViolation(
                code="SEMANTIC_RECOMPILE_REQUIRED",
                message=(
                    "The previous Run did not verify required semantic outputs. Use "
                    "RECOMPILE_PIPELINE instead of repeating the same Pipeline."
                ),
                allowed_actions=allowed,
            )

    return None
