from __future__ import annotations

import asyncio

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
                        "requirement": "Produce data satisfying constraint C.",
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
        "Produce data satisfying constraint C."
    )
    assert planner.requests[0].messages[-1].content == (
        "Please process my source according to constraint C."
    )
