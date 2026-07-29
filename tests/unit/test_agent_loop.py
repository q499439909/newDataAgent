from __future__ import annotations

import asyncio

from dataagent.agents.loop import AgentLoop
from dataagent.agents.runner import AgentMessage
from dataagent.agents.turn import TurnInput, TurnResult
from dataagent.application.agent_sessions import (
    ConversationStoreAgentSessionRepository,
    InMemoryAgentSessionRepository,
)
from dataagent.infrastructure import ConversationStore, SqliteDatabase


class ScriptedRootAgent:
    def __init__(self, results):
        self.results = iter(results)
        self.turns = []

    async def run_turn(
        self,
        turn,
        session,
        *,
        event_sink=None,
        cancellation_requested=None,
    ):
        self.turns.append(turn)
        return next(self.results)


def test_plain_language_enters_requirement_agent_and_persists_history() -> None:
    sessions = InMemoryAgentSessionRepository()
    sessions.create(session_id="session_1", owner_id="owner_1")
    root = ScriptedRootAgent(
        [
            TurnResult(
                status="waiting_for_user",
                reply="Please confirm the TaskSpec.",
                work_order_id="work_1",
                stop_reason="task_spec_confirmation",
            )
        ]
    )
    loop = AgentLoop(root_agent=root, sessions=sessions)

    result = asyncio.run(
        loop.handle_message(
            TurnInput(
                session_id="session_1",
                owner_id="owner_1",
                content="继续处理当前任务",
            )
        )
    )

    assert result.status == "waiting_for_user"
    assert root.turns[0].content == "继续处理当前任务"
    session = sessions.get(session_id="session_1", owner_id="owner_1")
    assert session.work_order_id == "work_1"
    assert [(item.role, item.content) for item in session.messages] == [
        ("user", "继续处理当前任务"),
        ("assistant", "Please confirm the TaskSpec."),
    ]


def test_known_slash_command_does_not_enter_requirement_agent() -> None:
    sessions = InMemoryAgentSessionRepository()
    sessions.create(session_id="session_1", owner_id="owner_1")
    root = ScriptedRootAgent([])
    loop = AgentLoop(root_agent=root, sessions=sessions)

    result = asyncio.run(
        loop.handle_message(
            TurnInput(
                session_id="session_1",
                owner_id="owner_1",
                content="/status",
            )
        )
    )

    assert result.status == "completed"
    assert result.stop_reason == "command_status"
    assert root.turns == []


def test_scheduled_turn_continues_without_another_user_message() -> None:
    sessions = InMemoryAgentSessionRepository()
    sessions.create(session_id="session_1", owner_id="owner_1")
    root = ScriptedRootAgent(
        [
            TurnResult(
                status="scheduled",
                work_order_id="work_1",
                stop_reason="tool_observation_ready",
            ),
            TurnResult(
                status="completed",
                reply="Pipeline candidates are ready.",
                work_order_id="work_1",
                stop_reason="completed",
            ),
        ]
    )
    loop = AgentLoop(root_agent=root, sessions=sessions)

    result = asyncio.run(
        loop.handle_message(
            TurnInput(
                session_id="session_1",
                owner_id="owner_1",
                content="Process the current WorkOrder.",
            )
        )
    )

    assert result.status == "completed"
    assert len(root.turns) == 2
    assert root.turns[1].source == "system"
    assert root.turns[1].metadata["continuation"] is True


def test_system_observation_reenters_the_same_agent_loop() -> None:
    sessions = InMemoryAgentSessionRepository()
    sessions.create(
        session_id="session_1",
        owner_id="owner_1",
        work_order_id="work_1",
    )
    root = ScriptedRootAgent(
        [
            TurnResult(
                status="completed",
                reply="Run outcome evaluated.",
                work_order_id="work_1",
                stop_reason="completed",
            )
        ]
    )
    loop = AgentLoop(root_agent=root, sessions=sessions)

    result = asyncio.run(
        loop.handle_system_observation(
            session_id="session_1",
            owner_id="owner_1",
            observation={
                "run_id": "run_1",
                "status": "SUCCEEDED",
            },
        )
    )

    assert result.status == "completed"
    assert root.turns[0].source == "system"
    assert root.turns[0].metadata["observation"]["run_id"] == "run_1"


def test_cancel_command_interrupts_an_active_turn() -> None:
    class BlockingRootAgent:
        async def run_turn(
            self,
            turn,
            session,
            *,
            event_sink=None,
            cancellation_requested=None,
        ):
            while not cancellation_requested():
                await asyncio.sleep(0)
            return TurnResult(
                status="cancelled",
                work_order_id=session.work_order_id,
                stop_reason="cancelled",
            )

    async def scenario():
        sessions = InMemoryAgentSessionRepository()
        sessions.create(session_id="session_1", owner_id="owner_1")
        loop = AgentLoop(root_agent=BlockingRootAgent(), sessions=sessions)
        active = asyncio.create_task(
            loop.handle_message(
                TurnInput(
                    session_id="session_1",
                    owner_id="owner_1",
                    content="Start a long planning turn.",
                )
            )
        )
        await asyncio.sleep(0)
        cancelled = await loop.handle_message(
            TurnInput(
                session_id="session_1",
                owner_id="owner_1",
                content="/cancel",
            )
        )
        return await active, cancelled

    active, command = asyncio.run(scenario())

    assert active.status == "cancelled"
    assert command.stop_reason == "command_cancel"


def test_same_session_turns_are_serialized() -> None:
    class ConcurrencyRootAgent:
        def __init__(self) -> None:
            self.active = 0
            self.maximum_active = 0

        async def run_turn(
            self,
            turn,
            session,
            *,
            event_sink=None,
            cancellation_requested=None,
        ):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return TurnResult(
                status="completed",
                reply=turn.content,
                stop_reason="completed",
            )

    async def scenario():
        sessions = InMemoryAgentSessionRepository()
        sessions.create(session_id="session_1", owner_id="owner_1")
        root = ConcurrencyRootAgent()
        loop = AgentLoop(root_agent=root, sessions=sessions)
        await asyncio.gather(
            *(
                loop.handle_message(
                    TurnInput(
                        session_id="session_1",
                        owner_id="owner_1",
                        content=f"message-{index}",
                    )
                )
                for index in range(2)
            )
        )
        return root

    root = asyncio.run(scenario())

    assert root.maximum_active == 1


def test_conversation_store_adapter_persists_agent_session(tmp_path) -> None:
    store = ConversationStore(SqliteDatabase(tmp_path / "control.db"))
    sessions = ConversationStoreAgentSessionRepository(store)
    sessions.create(
        session_id="session_1",
        owner_id="owner_1",
        work_order_id="work_1",
    )

    sessions.append_message(
        session_id="session_1",
        owner_id="owner_1",
        message=AgentMessage(
            role="user",
            content="Persist this message.",
        ),
    )
    sessions.set_work_order(
        session_id="session_1",
        owner_id="owner_1",
        work_order_id=None,
    )

    loaded = sessions.get(session_id="session_1", owner_id="owner_1")
    assert loaded.work_order_id is None
    assert loaded.messages[0].content == "Persist this message."
