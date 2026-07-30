from __future__ import annotations

import asyncio

from dataagent.agents.runner import (
    AgentDecision,
    AgentMessage,
    AgentRunner,
    AgentTool,
)


class ScriptedPlanner:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return next(self.decisions)


def test_agent_runner_streams_decisions_and_observations() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Inspect current facts.",
                tool_name="inspect",
                tool_input={"work_order_id": "work_1"},
            ),
            AgentDecision(
                action="finish",
                reason_summary="The facts are sufficient.",
                output={"answer": "ready"},
            ),
        ]
    )
    events = []
    runner = AgentRunner(
        agent_name="requirement",
        planner=planner,
        tools=(
            AgentTool(
                name="inspect",
                description="Inspect WorkOrder facts.",
                input_schema={"type": "object"},
                execute=lambda payload: {
                    "ok": True,
                    "work_order_id": payload["work_order_id"],
                },
            ),
        ),
    )

    result = runner.run(
        goal="Advance the WorkOrder.",
        context={"state": "runnable"},
        messages=(
            AgentMessage(role="user", content="Continue this WorkOrder."),
        ),
        event_sink=events.append,
    )

    assert result.status == "finished"
    assert result.stop_reason == "finish"
    assert result.output == {"answer": "ready"}
    assert planner.requests[0].messages[0].content == (
        "Continue this WorkOrder."
    )
    assert [event.kind for event in events] == [
        "decision",
        "tool_started",
        "observation",
        "decision",
        "stopped",
    ]


def test_agent_runner_supports_async_planners_and_tools() -> None:
    class AsyncPlanner:
        def __init__(self) -> None:
            self.step = 0

        async def decide(self, request):
            self.step += 1
            if self.step == 1:
                return AgentDecision(
                    action="tool",
                    reason_summary="Observe asynchronously.",
                    tool_name="inspect",
                )
            return AgentDecision(
                action="finish",
                reason_summary="Async observation received.",
                output={"ok": True},
            )

    async def inspect(_payload):
        await asyncio.sleep(0)
        return {"ok": True}

    runner = AgentRunner(
        agent_name="requirement",
        planner=AsyncPlanner(),
        tools=(
            AgentTool(
                name="inspect",
                description="Inspect asynchronously.",
                input_schema={"type": "object"},
                execute=inspect,
            ),
        ),
    )

    result = asyncio.run(
        runner.arun(goal="Advance.", context={})
    )

    assert result.status == "finished"
    assert result.observations[0].data == {"ok": True}


def test_agent_runner_returns_cancelled_without_calling_model() -> None:
    planner = ScriptedPlanner([])
    runner = AgentRunner(
        agent_name="requirement",
        planner=planner,
        tools=(),
    )

    result = runner.run(
        goal="Advance.",
        context={},
        cancellation_requested=lambda: True,
    )

    assert result.status == "cancelled"
    assert result.stop_reason == "cancelled"
    assert planner.requests == []


def test_agent_runner_reports_iteration_budget_exhaustion() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="The requested tool is unavailable.",
                tool_name="missing",
            )
        ]
    )
    runner = AgentRunner(
        agent_name="requirement",
        planner=planner,
        tools=(),
        max_iterations=1,
    )

    result = runner.run(goal="Advance.", context={})

    assert result.status == "exhausted"
    assert result.stop_reason == "max_iterations"
    assert result.observations[-1].data["error_type"] == (
        "unavailable_tool"
    )


def test_terminal_validator_returns_invalid_terminal_output_to_planner() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="finish",
                reason_summary="Finished without a user-facing reply.",
            ),
            AgentDecision(
                action="finish",
                reason_summary="Finished with a user-facing reply.",
                output={"reply": "The requirement is ready for confirmation."},
            ),
        ]
    )
    runner = AgentRunner(
        agent_name="requirement",
        planner=planner,
        tools=(),
        terminal_validator=lambda decision: {
            "ok": bool(decision.output.get("reply")),
            "error_type": "empty_user_reply",
        },
    )

    result = runner.run(goal="Handle the user turn.", context={})

    assert result.output["reply"] == (
        "The requirement is ready for confirmation."
    )
    assert result.observations[0].tool_name == "validate_terminal_decision"
    assert result.observations[0].data["error_type"] == "empty_user_reply"


def test_async_agent_can_run_a_sync_tool_with_its_own_agent_loop() -> None:
    nested_planner = ScriptedPlanner(
        [
            AgentDecision(
                action="finish",
                reason_summary="Nested planning is complete.",
                output={"ok": True},
            )
        ]
    )
    outer_planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Start the governed synchronous runtime.",
                tool_name="start_runtime",
            ),
            AgentDecision(
                action="finish",
                reason_summary="The runtime completed.",
                output={"reply": "done"},
            ),
        ]
    )

    def start_runtime(_payload):
        nested = AgentRunner(
            agent_name="nested",
            planner=nested_planner,
            tools=(),
        ).run(goal="Complete nested planning.", context={})
        return {"status": nested.status}

    result = asyncio.run(
        AgentRunner(
            agent_name="outer",
            planner=outer_planner,
            tools=(
                AgentTool(
                    name="start_runtime",
                    description="Start a synchronous governed runtime.",
                    input_schema={
                        "type": "object",
                        "additionalProperties": False,
                    },
                    execute=start_runtime,
                ),
            ),
        ).arun(goal="Run the nested runtime.", context={})
    )

    assert result.status == "finished"
    assert result.observations[0].data == {"status": "finished"}


def test_return_direct_tool_stops_before_another_model_decision() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Reach the governed user boundary.",
                tool_name="advance",
            )
        ]
    )
    events = []
    result = AgentRunner(
        agent_name="root",
        planner=planner,
        tools=(
            AgentTool(
                name="advance",
                description="Advance until the next user boundary.",
                input_schema={"type": "object"},
                execute=lambda _payload: {
                    "work_order_id": "work_1",
                    "interrupts": [
                        {"value": {"kind": "task_spec_confirmation"}}
                    ],
                },
                return_direct=True,
            ),
        ),
    ).run(
        goal="Advance once.",
        context={},
        event_sink=events.append,
    )

    assert result.status == "finished"
    assert result.output["tool_result"]["work_order_id"] == "work_1"
    assert len(planner.requests) == 1
    assert [event.kind for event in events] == [
        "decision",
        "tool_started",
        "observation",
        "stopped",
    ]
