from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TurnInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source: Literal["user", "system"] = "user"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["respond", "delegate", "call_tool", "ask_user", "finish"]
    target: str | None = None
    objective: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target(self) -> "AgentAction":
        if self.kind in {"delegate", "call_tool", "ask_user"} and not self.target:
            raise ValueError(f"{self.kind} actions require target")
        if self.kind in {"respond", "finish"} and self.target is not None:
            raise ValueError(f"{self.kind} actions cannot include target")
        return self


class TurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal[
        "completed",
        "waiting_for_user",
        "scheduled",
        "failed",
        "cancelled",
    ]
    reply: str | None = None
    work_order_id: str | None = None
    stop_reason: str = Field(min_length=1)


class WorkOrderLiveness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["runnable", "waiting", "terminal"]
    reason: str = Field(min_length=1)
    required_action: str | None = None

    @model_validator(mode="after")
    def validate_required_action(self) -> "WorkOrderLiveness":
        if self.status == "terminal" and self.required_action is not None:
            raise ValueError("terminal WorkOrders cannot require another action")
        if self.status != "terminal" and not self.required_action:
            raise ValueError(f"{self.status} WorkOrders require an action")
        return self


__all__ = [
    "AgentAction",
    "TurnInput",
    "TurnResult",
    "WorkOrderLiveness",
]
