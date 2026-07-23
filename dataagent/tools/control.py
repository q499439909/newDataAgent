from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from ..application.conversation_actions import (
    ConversationActionError,
    parse_conversation_action,
)
from ..application.conversation_policy import validate_conversation_action
from .observations import ToolResult
from .spec import ToolConfirmation, ToolContext, ToolEffect, ToolSpec


class ProposeControlActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: dict[str, Any]


def _propose_control_action(
    context: ToolContext, request: ProposeControlActionInput
) -> ToolResult:
    try:
        action = parse_conversation_action(request.action)
    except ConversationActionError:
        return ToolResult(
            ok=False,
            tool="propose_control_action",
            status="failed",
            summary="Control action schema validation failed.",
            data={"executed": False},
            error_type="action_validation_error",
        )
    violation = validate_conversation_action(action, context.control_context)
    if violation is not None:
        return ToolResult(
            ok=False,
            tool="propose_control_action",
            status="policy_violation",
            summary=violation.message,
            data={
                "executed": False,
                "proposal": action.model_dump(mode="json"),
                "allowed_actions": list(violation.allowed_actions),
            },
            next_actions=violation.allowed_actions,
            error_type=violation.code,
        )
    return ToolResult(
        ok=True,
        tool="propose_control_action",
        status="succeeded",
        summary="Control action proposal passed schema and policy validation.",
        data={
            "executed": False,
            "proposal": action.model_dump(mode="json"),
        },
        next_actions=("request_user_confirmation",),
        requires_confirmation=True,
    )


def propose_control_action_spec() -> ToolSpec:
    return ToolSpec(
        name="propose_control_action",
        description="Validate a control action proposal without executing it.",
        input_model=ProposeControlActionInput,
        executor=_propose_control_action,
        tags=("control", "policy", "draft_only"),
        effect=ToolEffect.CONTROL,
        confirmation=ToolConfirmation.DRAFT_ONLY,
    )


__all__ = ["ProposeControlActionInput", "propose_control_action_spec"]
