from __future__ import annotations

import asyncio

import pytest

from dataagent.agents.loop import AgentLoop
from dataagent.agents.runner import AgentDecision
from dataagent.agents.turn import TurnInput, TurnResult
from dataagent.application.work_order_runtime import WorkOrderRuntime
from dataagent.application.agent_sessions import (
    ConversationStoreAgentSessionRepository,
    InMemoryAgentSessionRepository,
)
from dataagent.application.agent_turns import WorkOrderRuntimeRootAgent
from dataagent.application.conversation import ConversationService
from apps.api.main import create_app


class ReplyingRootAgent:
    async def run_turn(
        self,
        turn,
        session,
        *,
        event_sink=None,
        cancellation_requested=None,
    ):
        return TurnResult(
            status="completed",
            reply=f"root:{turn.content}",
            work_order_id=session.work_order_id,
            stop_reason="completed",
        )


class EmptyReplyRootAgent:
    async def run_turn(
        self,
        turn,
        session,
        *,
        event_sink=None,
        cancellation_requested=None,
    ):
        return TurnResult(
            status="completed",
            reply=None,
            work_order_id=session.work_order_id,
            stop_reason="finish",
        )


def test_conversation_service_is_only_an_agent_loop_adapter(tmp_path) -> None:
    runtime = WorkOrderRuntime(tmp_path / "runtime", include_datajuicer=False)
    assert runtime.conversation_store is not None
    sessions = ConversationStoreAgentSessionRepository(
        runtime.conversation_store
    )
    loop = AgentLoop(root_agent=ReplyingRootAgent(), sessions=sessions)
    service = ConversationService(
        store=runtime.conversation_store,
        work_order_runtime=runtime,
        agent_loop=loop,
    )
    conversation = service.create("owner_1")

    response = service.send(
        thread_id=conversation["id"],
        owner_id="owner_1",
        content="继续当前任务",
    )

    assert response["reply"] == "root:继续当前任务"
    assert [item["role"] for item in response["messages"]] == [
        "user",
        "assistant",
    ]


def test_default_api_conversation_path_installs_agent_loop(tmp_path) -> None:
    runtime = WorkOrderRuntime(tmp_path / "runtime", include_datajuicer=False)

    app = create_app(runtime=runtime)

    assert app.state.conversation_service.agent_loop is not None


def test_conversation_adapter_rejects_an_empty_agent_reply(tmp_path) -> None:
    runtime = WorkOrderRuntime(
        tmp_path / "runtime",
        include_datajuicer=False,
    )
    assert runtime.conversation_store is not None
    sessions = ConversationStoreAgentSessionRepository(
        runtime.conversation_store
    )
    loop = AgentLoop(root_agent=EmptyReplyRootAgent(), sessions=sessions)
    service = ConversationService(
        store=runtime.conversation_store,
        work_order_runtime=runtime,
        agent_loop=loop,
    )
    conversation = service.create("owner_1")

    with pytest.raises(RuntimeError, match="AGENT_EMPTY_REPLY"):
        service.send(
            thread_id=conversation["id"],
            owner_id="owner_1",
            content="Explain the current state.",
        )


class ScriptedPlanner:
    def __init__(self):
        self.requests = []
        self.decisions = iter(
            (
                AgentDecision(
                    action="tool",
                    reason_summary="Create the WorkOrder from the user goal.",
                    tool_name="start_work_order",
                    tool_input={
                        "data_sources": [
                            {
                                "type": "local_directory",
                                "uri": "D:/generic/source",
                            }
                        ],
                    },
                ),
                AgentDecision(
                    action="finish",
                    reason_summary="The WorkOrder is waiting for confirmation.",
                    output={"reply": "Please confirm the TaskSpec."},
                ),
            )
        )

    def decide(self, request):
        self.requests.append(request)
        return next(self.decisions)


class FakeWorkOrderRuntime:
    def __init__(self):
        self.started = []
        self.inspected = []
        self.resumed = []
        self.current = None

    def start(self, **kwargs):
        self.started.append(kwargs)
        return {
            "work_order_id": "work_1",
            "state": {
                "next_action": "confirm_task_spec",
                "task_spec": {"id": "spec_1"},
            },
            "interrupts": [
                {
                    "value": {
                        "kind": "task_spec_confirmation",
                    }
                }
            ],
        }

    def state(self, **kwargs):
        self.inspected.append(kwargs)
        return self.current or {
            "work_order_id": kwargs["work_order_id"],
            "state": {"next_action": "confirm_task_spec"},
            "interrupts": [
                {"value": {"kind": "task_spec_confirmation"}}
            ],
        }

    def resume(self, **kwargs):
        self.resumed.append(kwargs)
        return {
            "work_order_id": kwargs["work_order_id"],
            "state": {"next_action": "confirm_task_spec"},
            "interrupts": [
                {"value": {"kind": "task_spec_confirmation"}}
            ],
        }


def test_plain_language_reaches_requirement_runner_and_work_order_runtime() -> None:
    planner = ScriptedPlanner()
    runtime = FakeWorkOrderRuntime()
    root = WorkOrderRuntimeRootAgent(runtime=runtime, planner=planner)
    sessions = InMemoryAgentSessionRepository()
    sessions.create(session_id="session_1", owner_id="owner_1")
    loop = AgentLoop(root_agent=root, sessions=sessions)

    result = asyncio.run(
        loop.handle_message(
            TurnInput(
                session_id="session_1",
                owner_id="owner_1",
                content="Please process my source according to constraint C.",
            )
        )
    )

    assert result.status == "waiting_for_user"
    assert runtime.started[0]["requirement"] == (
        "Please process my source according to constraint C."
    )
    assert planner.requests[0].messages[-1].content == (
        "Please process my source according to constraint C."
    )


def test_explicit_source_starts_main_requirement_agent_without_root_routing() -> None:
    class PlannerMustNotRun:
        def decide(self, request):
            raise AssertionError("Explicit source must enter Requirement runtime")

    runtime = FakeWorkOrderRuntime()
    sessions = InMemoryAgentSessionRepository()
    sessions.create(session_id="session_direct", owner_id="owner_1")

    result = asyncio.run(
        AgentLoop(
            root_agent=WorkOrderRuntimeRootAgent(
                runtime=runtime,
                planner=PlannerMustNotRun(),
            ),
            sessions=sessions,
        ).handle_message(
            TurnInput(
                session_id="session_direct",
                owner_id="owner_1",
                content=(
                    r"D:\generic\records apply the supplied retention policy."
                ),
            )
        )
    )

    assert result.work_order_id == "work_1"
    assert runtime.started == [
        {
            "owner_id": "owner_1",
            "requirement": (
                r"D:\generic\records apply the supplied retention policy."
            ),
            "data_sources": [
                {
                    "type": "local_directory",
                    "uri": r"D:\generic\records",
                    "collection": None,
                    "mapping": {},
                }
            ],
        }
    ]


def test_root_agent_stops_at_work_order_user_boundary_in_the_same_turn() -> None:
    planner = type(
        "StartInspectPlanner",
        (),
        {
            "__init__": lambda self: setattr(
                self,
                "decisions",
                iter(
                    (
                        AgentDecision(
                            action="tool",
                            reason_summary="Create the WorkOrder.",
                            tool_name="start_work_order",
                            tool_input={
                                "data_sources": [
                                    {
                                        "type": "local_directory",
                                        "uri": "D:/generic/source",
                                        "mapping": {},
                                    }
                                ],
                            },
                        ),
                        AgentDecision(
                            action="tool",
                            reason_summary="Inspect the created WorkOrder.",
                            tool_name="inspect_work_order",
                            tool_input={},
                        ),
                        AgentDecision(
                            action="finish",
                            reason_summary="The TaskSpec needs confirmation.",
                            output={"reply": "Please confirm the TaskSpec."},
                        ),
                    )
                ),
            ),
            "decide": lambda self, request: next(self.decisions),
        },
    )()
    runtime = FakeWorkOrderRuntime()
    root = WorkOrderRuntimeRootAgent(runtime=runtime, planner=planner)
    sessions = InMemoryAgentSessionRepository()
    sessions.create(session_id="session_same_turn", owner_id="owner_1")

    result = asyncio.run(
        AgentLoop(root_agent=root, sessions=sessions).handle_message(
            TurnInput(
                session_id="session_same_turn",
                owner_id="owner_1",
                content="Apply constraint C to my source.",
            )
        )
    )

    assert result.reply == (
        "The WorkOrder is waiting for task_spec_confirmation."
    )
    assert runtime.inspected == []


@pytest.mark.parametrize(
    "answer",
    [
        "Use the largest detected object and a similarity threshold of 0.91.",
        "保留最长的一段音频，并使用 0.82 作为相似度阈值。",
    ],
)
def test_active_requirement_clarification_goes_directly_to_requirement_agent(
    answer,
) -> None:
    class PlannerMustNotRun:
        def decide(self, request):
            raise AssertionError("Root planner must not reinterpret clarification")

    runtime = FakeWorkOrderRuntime()
    runtime.current = {
        "work_order_id": "work_existing",
        "state": {"next_action": "clarify_requirement"},
        "interrupts": [
            {"value": {"kind": "requirement_clarification"}}
        ],
    }
    root = WorkOrderRuntimeRootAgent(
        runtime=runtime,
        planner=PlannerMustNotRun(),
    )
    sessions = InMemoryAgentSessionRepository()
    sessions.create(
        session_id="session_clarification",
        owner_id="owner_1",
        work_order_id="work_existing",
    )

    result = asyncio.run(
        AgentLoop(root_agent=root, sessions=sessions).handle_message(
            TurnInput(
                session_id="session_clarification",
                owner_id="owner_1",
                content=answer,
            )
        )
    )

    assert result.status == "waiting_for_user"
    assert runtime.resumed == [
        {
            "work_order_id": "work_existing",
            "owner_id": "owner_1",
            "decision": {"answer": answer},
        }
    ]


def test_non_requirement_interrupt_still_uses_root_planner() -> None:
    class AskUserPlanner:
        def __init__(self):
            self.called = False

        def decide(self, request):
            self.called = True
            return AgentDecision(
                action="ask_user",
                reason_summary="Approval needs an explicit decision.",
                output={"reply": "Do you approve the TaskSpec?"},
            )

    planner = AskUserPlanner()
    runtime = FakeWorkOrderRuntime()
    runtime.current = {
        "work_order_id": "work_existing",
        "state": {"next_action": "confirm_task_spec"},
        "interrupts": [
            {"value": {"kind": "task_spec_confirmation"}}
        ],
    }
    sessions = InMemoryAgentSessionRepository()
    sessions.create(
        session_id="session_confirmation",
        owner_id="owner_1",
        work_order_id="work_existing",
    )

    result = asyncio.run(
        AgentLoop(
            root_agent=WorkOrderRuntimeRootAgent(
                runtime=runtime,
                planner=planner,
            ),
            sessions=sessions,
        ).handle_message(
            TurnInput(
                session_id="session_confirmation",
                owner_id="owner_1",
                content="What exactly will be approved?",
            )
        )
    )

    assert planner.called
    assert result.reply == "Do you approve the TaskSpec?"
