from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .runner import AgentMessage


class AgentSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    work_order_id: str | None = None
    messages: tuple[AgentMessage, ...] = ()
    context: dict[str, Any] = Field(default_factory=dict)


class AgentSessionRepository(Protocol):
    def create(
        self,
        *,
        session_id: str,
        owner_id: str,
        work_order_id: str | None = None,
    ) -> AgentSession: ...

    def get(
        self,
        *,
        session_id: str,
        owner_id: str,
    ) -> AgentSession: ...

    def append_message(
        self,
        *,
        session_id: str,
        owner_id: str,
        message: AgentMessage,
    ) -> AgentSession: ...

    def set_work_order(
        self,
        *,
        session_id: str,
        owner_id: str,
        work_order_id: str | None,
    ) -> AgentSession: ...


__all__ = ["AgentSession", "AgentSessionRepository"]
