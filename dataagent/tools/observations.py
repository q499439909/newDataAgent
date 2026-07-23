from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ToolStatus = Literal[
    "succeeded",
    "failed",
    "policy_violation",
    "confirmation_required",
]


class ToolEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    id: str
    uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    tool: str
    status: ToolStatus
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[ToolEvidence, ...] = ()
    next_actions: tuple[str, ...] = ()
    requires_confirmation: bool = False
    error_type: str | None = None


__all__ = ["ToolEvidence", "ToolResult", "ToolStatus"]
