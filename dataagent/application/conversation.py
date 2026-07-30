from __future__ import annotations

import asyncio
from typing import Any, Callable, Protocol

from ..agents.loop import AgentLoop
from ..agents.turn import TurnInput
from ..domain.common import new_id
from ..infrastructure import ConversationStore


class WorkOrderStateReader(Protocol):
    def state(
        self,
        *,
        work_order_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...


class ConversationService:
    """Compatibility Adapter from the existing Web API to ``AgentLoop``."""

    def __init__(
        self,
        *,
        store: ConversationStore,
        work_order_runtime: WorkOrderStateReader,
        agent_loop: AgentLoop,
    ) -> None:
        self.store = store
        self.work_order_runtime = work_order_runtime
        self.agent_loop = agent_loop

    def create(self, owner_id: str) -> dict[str, Any]:
        thread = self.store.create(
            thread_id=new_id("conversation"),
            owner_id=owner_id,
        )
        return self._public_thread(thread)

    def get(self, thread_id: str, owner_id: str) -> dict[str, Any]:
        return self._public_thread(self.store.get(thread_id, owner_id))

    def bind_work_order(
        self,
        *,
        thread_id: str,
        owner_id: str,
        work_order_id: str,
    ) -> dict[str, Any]:
        self.work_order_runtime.state(
            work_order_id=work_order_id,
            owner_id=owner_id,
        )
        thread = self.store.get(thread_id, owner_id)
        updated = self.store.update(
            thread_id=thread_id,
            owner_id=owner_id,
            context=thread["context"],
            work_order_id=work_order_id,
        )
        return self._public_thread(updated)

    def send(
        self,
        *,
        thread_id: str,
        owner_id: str,
        content: str,
        action_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("Message must not be empty")
        action_trace: list[dict[str, Any]] = []

        def receive_event(event: Any) -> None:
            payload = (
                event.model_dump(mode="json")
                if hasattr(event, "model_dump")
                else dict(event)
            )
            data = payload.get("data", {})
            trace = {
                "id": new_id("action_trace"),
                "stage": payload.get("kind", "agent_turn"),
                "stage_label": payload.get("kind", "agent_turn"),
                "kind": "agent",
                "tool": data.get("tool_name"),
                "display_name": payload.get("kind", "Agent event"),
                "status": data.get("status", "succeeded"),
                "parameters": data,
                "duration_ms": 0,
                "summary": payload.get("kind", "Agent event"),
                "evidence_ids": [],
                "error_type": data.get("error_type"),
            }
            action_trace.append(trace)
            if action_sink is not None:
                action_sink(trace)

        result = asyncio.run(
            self.agent_loop.handle_message(
                TurnInput(
                    session_id=thread_id,
                    owner_id=owner_id,
                    content=content,
                ),
                receive_event,
            )
        )
        if not result.reply or not result.reply.strip():
            raise RuntimeError(
                "AGENT_EMPTY_REPLY: the root Agent ended the turn without "
                "a user-facing reply"
            )
        thread = self.store.get(thread_id, owner_id)
        work_order_id = result.work_order_id or thread.get("work_order_id")
        turn = None
        if work_order_id:
            try:
                turn = self.work_order_runtime.state(
                    work_order_id=work_order_id,
                    owner_id=owner_id,
                )
            except KeyError:
                turn = None
        return {
            "reply": result.reply or "",
            "turn": turn,
            "run": None,
            "action_trace": action_trace,
            "conversation_id": thread_id,
            "work_order_id": work_order_id,
            "messages": self.store.messages(thread_id, owner_id),
        }

    def _public_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": thread["id"],
            "work_order_id": thread["work_order_id"],
            "messages": self.store.messages(thread["id"], thread["owner_id"]),
            "action_trace_history": list(
                thread["context"].get("action_trace_history") or ()
            ),
        }


__all__ = ["ConversationService", "WorkOrderStateReader"]
