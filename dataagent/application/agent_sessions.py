from __future__ import annotations

from threading import RLock
from typing import Any, Protocol

from ..agents.runner import AgentMessage
from ..agents.session import AgentSession, AgentSessionRepository


class InMemoryAgentSessionRepository:
    """Thread-safe AgentSession repository for local and test runtimes."""

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        session_id: str,
        owner_id: str,
        work_order_id: str | None = None,
    ) -> AgentSession:
        with self._lock:
            if session_id in self._sessions:
                raise ValueError(f"AgentSession already exists: {session_id}")
            session = AgentSession(
                id=session_id,
                owner_id=owner_id,
                work_order_id=work_order_id,
            )
            self._sessions[session_id] = session
            return session

    def get(
        self,
        *,
        session_id: str,
        owner_id: str,
    ) -> AgentSession:
        with self._lock:
            try:
                session = self._sessions[session_id]
            except KeyError as exc:
                raise KeyError(f"AgentSession not found: {session_id}") from exc
            if session.owner_id != owner_id:
                raise PermissionError("AgentSession belongs to another owner")
            return session.model_copy(deep=True)

    def append_message(
        self,
        *,
        session_id: str,
        owner_id: str,
        message: AgentMessage,
    ) -> AgentSession:
        with self._lock:
            session = self.get(session_id=session_id, owner_id=owner_id)
            updated = session.model_copy(
                update={"messages": (*session.messages, message)}
            )
            self._sessions[session_id] = updated
            return updated.model_copy(deep=True)

    def set_work_order(
        self,
        *,
        session_id: str,
        owner_id: str,
        work_order_id: str | None,
    ) -> AgentSession:
        with self._lock:
            session = self.get(session_id=session_id, owner_id=owner_id)
            updated = session.model_copy(
                update={"work_order_id": work_order_id}
            )
            self._sessions[session_id] = updated
            return updated.model_copy(deep=True)


class ConversationSessionStore(Protocol):
    def create(self, *, thread_id: str, owner_id: str) -> dict[str, Any]: ...

    def get(self, thread_id: str, owner_id: str) -> dict[str, Any]: ...

    def update(
        self,
        *,
        thread_id: str,
        owner_id: str,
        context: dict[str, Any],
        work_order_id: str | None = None,
    ) -> dict[str, Any]: ...

    def add_message(
        self,
        *,
        thread_id: str,
        owner_id: str,
        role: str,
        content: str,
        intent: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]: ...

    def messages(
        self,
        thread_id: str,
        owner_id: str,
    ) -> list[dict[str, Any]]: ...


class ConversationStoreAgentSessionRepository:
    """Adapter that reuses the existing durable conversation tables."""

    _WORK_ORDER_KEY = "agent_loop_work_order_id"
    _MESSAGE_METADATA_KEY = "agent_loop_message_metadata"

    def __init__(self, store: ConversationSessionStore) -> None:
        self.store = store

    def create(
        self,
        *,
        session_id: str,
        owner_id: str,
        work_order_id: str | None = None,
    ) -> AgentSession:
        self.store.create(thread_id=session_id, owner_id=owner_id)
        self.set_work_order(
            session_id=session_id,
            owner_id=owner_id,
            work_order_id=work_order_id,
        )
        return self.get(session_id=session_id, owner_id=owner_id)

    def get(
        self,
        *,
        session_id: str,
        owner_id: str,
    ) -> AgentSession:
        thread = self.store.get(session_id, owner_id)
        context = dict(thread.get("context") or {})
        metadata_by_sequence = context.get(self._MESSAGE_METADATA_KEY) or {}
        messages = []
        for item in self.store.messages(session_id, owner_id):
            role = str(item.get("role") or "system")
            normalized_role = (
                role
                if role in {"system", "user", "assistant", "tool"}
                else "system"
            )
            metadata = dict(
                metadata_by_sequence.get(str(item["sequence"])) or {}
            )
            if normalized_role != role:
                metadata["original_role"] = role
            messages.append(
                AgentMessage(
                    role=normalized_role,
                    content=str(item.get("content") or ""),
                    metadata=metadata,
                )
            )
        work_order_id = (
            context[self._WORK_ORDER_KEY]
            if self._WORK_ORDER_KEY in context
            else thread.get("work_order_id")
        )
        return AgentSession(
            id=session_id,
            owner_id=owner_id,
            work_order_id=work_order_id,
            messages=tuple(messages),
            context=context,
        )

    def append_message(
        self,
        *,
        session_id: str,
        owner_id: str,
        message: AgentMessage,
    ) -> AgentSession:
        stored = self.store.add_message(
            thread_id=session_id,
            owner_id=owner_id,
            role=message.role,
            content=message.content,
        )
        if message.metadata:
            thread = self.store.get(session_id, owner_id)
            context = dict(thread.get("context") or {})
            metadata = dict(context.get(self._MESSAGE_METADATA_KEY) or {})
            metadata[str(stored["sequence"])] = message.metadata
            context[self._MESSAGE_METADATA_KEY] = metadata
            self.store.update(
                thread_id=session_id,
                owner_id=owner_id,
                context=context,
            )
        return self.get(session_id=session_id, owner_id=owner_id)

    def set_work_order(
        self,
        *,
        session_id: str,
        owner_id: str,
        work_order_id: str | None,
    ) -> AgentSession:
        thread = self.store.get(session_id, owner_id)
        context = dict(thread.get("context") or {})
        context[self._WORK_ORDER_KEY] = work_order_id
        update_kwargs: dict[str, Any] = {
            "thread_id": session_id,
            "owner_id": owner_id,
            "context": context,
        }
        if work_order_id is not None:
            update_kwargs["work_order_id"] = work_order_id
        self.store.update(**update_kwargs)
        return self.get(session_id=session_id, owner_id=owner_id)


__all__ = [
    "AgentSession",
    "AgentSessionRepository",
    "ConversationStoreAgentSessionRepository",
    "InMemoryAgentSessionRepository",
]
