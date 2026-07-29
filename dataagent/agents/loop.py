from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .runner import AgentMessage
from .session import AgentSession, AgentSessionRepository
from .turn import TurnInput, TurnResult


class AgentLoopEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)


AgentLoopEventSink = Callable[
    [AgentLoopEvent],
    Any | Awaitable[Any],
]


class RootAgent(Protocol):
    async def run_turn(
        self,
        turn: TurnInput,
        session: AgentSession,
        *,
        event_sink: AgentLoopEventSink | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> TurnResult: ...


class AgentLoop:
    """Own one session's message, continuation, and cancellation lifecycle."""

    _COMMANDS = frozenset({"/help", "/status", "/new", "/cancel"})

    def __init__(
        self,
        *,
        root_agent: RootAgent,
        sessions: AgentSessionRepository,
        max_continuations: int = 8,
    ) -> None:
        if max_continuations < 0:
            raise ValueError("max_continuations cannot be negative")
        self.root_agent = root_agent
        self.sessions = sessions
        self.max_continuations = max_continuations
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._cancellations: dict[tuple[str, str], asyncio.Event] = {}

    async def handle_message(
        self,
        turn: TurnInput,
        event_sink: AgentLoopEventSink | None = None,
    ) -> TurnResult:
        command = self._parse_command(turn.content)
        if command == "/cancel":
            return await self._cancel(turn, event_sink)

        key = (turn.owner_id, turn.session_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if command is not None:
                return await self._handle_command(
                    turn,
                    command,
                    event_sink,
                )
            cancellation = asyncio.Event()
            self._cancellations[key] = cancellation
            try:
                return await self._run_turn(
                    turn,
                    event_sink=event_sink,
                    cancellation=cancellation,
                )
            finally:
                if self._cancellations.get(key) is cancellation:
                    self._cancellations.pop(key, None)

    async def handle_system_observation(
        self,
        *,
        session_id: str,
        owner_id: str,
        observation: dict[str, Any],
        event_sink: AgentLoopEventSink | None = None,
    ) -> TurnResult:
        return await self.handle_message(
            TurnInput(
                session_id=session_id,
                owner_id=owner_id,
                source="system",
                content="A new WorkOrder observation is available.",
                metadata={"observation": observation},
            ),
            event_sink,
        )

    async def _run_turn(
        self,
        turn: TurnInput,
        *,
        event_sink: AgentLoopEventSink | None,
        cancellation: asyncio.Event,
    ) -> TurnResult:
        session = self.sessions.append_message(
            session_id=turn.session_id,
            owner_id=turn.owner_id,
            message=AgentMessage(
                role="user" if turn.source == "user" else "system",
                content=turn.content,
                metadata=turn.metadata,
            ),
        )
        await self._emit(
            event_sink,
            AgentLoopEvent(
                kind="message_received",
                data={
                    "source": turn.source,
                    "session_id": turn.session_id,
                },
            ),
        )

        current = turn
        result: TurnResult | None = None
        for continuation in range(self.max_continuations + 1):
            result = await self.root_agent.run_turn(
                current,
                session,
                event_sink=event_sink,
                cancellation_requested=cancellation.is_set,
            )
            if result.work_order_id != session.work_order_id:
                session = self.sessions.set_work_order(
                    session_id=turn.session_id,
                    owner_id=turn.owner_id,
                    work_order_id=result.work_order_id,
                )
            if result.status != "scheduled":
                break
            if continuation >= self.max_continuations:
                result = result.model_copy(
                    update={"stop_reason": "continuation_budget_exhausted"}
                )
                break
            current = TurnInput(
                session_id=turn.session_id,
                owner_id=turn.owner_id,
                source="system",
                content="Continue the runnable WorkOrder.",
                metadata={
                    "continuation": True,
                    "previous_stop_reason": result.stop_reason,
                },
            )
            session = self.sessions.append_message(
                session_id=turn.session_id,
                owner_id=turn.owner_id,
                message=AgentMessage(
                    role="system",
                    content=current.content,
                    metadata=current.metadata,
                ),
            )
            await self._emit(
                event_sink,
                AgentLoopEvent(
                    kind="continuation_scheduled",
                    data={"sequence": continuation + 1},
                ),
            )

        assert result is not None
        if result.reply:
            self.sessions.append_message(
                session_id=turn.session_id,
                owner_id=turn.owner_id,
                message=AgentMessage(
                    role="assistant",
                    content=result.reply,
                ),
            )
        await self._emit_result(event_sink, result)
        return result

    async def _handle_command(
        self,
        turn: TurnInput,
        command: str,
        event_sink: AgentLoopEventSink | None,
    ) -> TurnResult:
        session = self.sessions.append_message(
            session_id=turn.session_id,
            owner_id=turn.owner_id,
            message=AgentMessage(role="user", content=turn.content),
        )
        if command == "/new":
            session = self.sessions.set_work_order(
                session_id=turn.session_id,
                owner_id=turn.owner_id,
                work_order_id=None,
            )
            reply = "Started a new Agent task context."
            reason = "command_new"
        elif command == "/status":
            reply = (
                f"Active WorkOrder: {session.work_order_id}"
                if session.work_order_id
                else "No active WorkOrder."
            )
            reason = "command_status"
        else:
            reply = "Commands: /help, /status, /new, /cancel"
            reason = "command_help"
        result = TurnResult(
            status="completed",
            reply=reply,
            work_order_id=session.work_order_id,
            stop_reason=reason,
        )
        self.sessions.append_message(
            session_id=turn.session_id,
            owner_id=turn.owner_id,
            message=AgentMessage(role="assistant", content=reply),
        )
        await self._emit(
            event_sink,
            AgentLoopEvent(kind="command", data={"command": command}),
        )
        await self._emit_result(event_sink, result)
        return result

    async def _cancel(
        self,
        turn: TurnInput,
        event_sink: AgentLoopEventSink | None,
    ) -> TurnResult:
        session = self.sessions.append_message(
            session_id=turn.session_id,
            owner_id=turn.owner_id,
            message=AgentMessage(role="user", content=turn.content),
        )
        cancellation = self._cancellations.get(
            (turn.owner_id, turn.session_id)
        )
        if cancellation is not None:
            cancellation.set()
            reply = "Cancellation requested for the active Agent turn."
        else:
            reply = "There is no active Agent turn to cancel."
        result = TurnResult(
            status="completed",
            reply=reply,
            work_order_id=session.work_order_id,
            stop_reason="command_cancel",
        )
        self.sessions.append_message(
            session_id=turn.session_id,
            owner_id=turn.owner_id,
            message=AgentMessage(role="assistant", content=reply),
        )
        await self._emit(
            event_sink,
            AgentLoopEvent(kind="command", data={"command": "/cancel"}),
        )
        return result

    @classmethod
    def _parse_command(cls, content: str) -> str | None:
        token = content.strip().split(maxsplit=1)[0].lower()
        return token if token in cls._COMMANDS else None

    @staticmethod
    async def _emit(
        sink: AgentLoopEventSink | None,
        event: AgentLoopEvent,
    ) -> None:
        if sink is None:
            return
        emitted = sink(event)
        if inspect.isawaitable(emitted):
            await emitted

    async def _emit_result(
        self,
        sink: AgentLoopEventSink | None,
        result: TurnResult,
    ) -> None:
        await self._emit(
            sink,
            AgentLoopEvent(
                kind="turn_completed",
                data=result.model_dump(mode="json"),
            ),
        )


__all__ = [
    "AgentLoop",
    "AgentLoopEvent",
    "AgentLoopEventSink",
    "RootAgent",
]
