from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    tool_name: str
    tool_input: dict[str, Any]
    data: dict[str, Any]


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["tool", "finish", "ask_user", "report_gap"]
    reason_summary: str = Field(min_length=1)
    tool_name: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_input", "output", mode="before")
    @classmethod
    def normalize_optional_objects(cls, value: Any) -> Any:
        return {} if value is None else value

    @model_validator(mode="after")
    def validate_action_payload(self) -> "AgentDecision":
        if self.action == "tool" and not self.tool_name:
            raise ValueError("Tool actions require tool_name")
        if self.action != "tool" and self.tool_name is not None:
            raise ValueError("Only tool actions may include tool_name")
        return self


class AgentPlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: str
    goal: str
    iteration: int = Field(ge=1)
    context: dict[str, Any]
    tools: tuple[dict[str, Any], ...]
    observations: tuple[AgentObservation, ...] = ()
    messages: tuple[AgentMessage, ...] = ()


class AgentPlanner(Protocol):
    def decide(
        self,
        request: AgentPlanningRequest,
    ) -> AgentDecision | Awaitable[AgentDecision]:
        ...


class AgentPlanningGateway(Protocol):
    def plan_agent_decision(
        self,
        request: AgentPlanningRequest,
    ) -> dict[str, Any]:
        ...


class GatewayAgentPlanner:
    """Validate model decisions before an Agent runtime may execute them."""

    def __init__(self, gateway: AgentPlanningGateway) -> None:
        self.gateway = gateway

    def decide(self, request: AgentPlanningRequest) -> AgentDecision:
        return AgentDecision.model_validate(
            self.gateway.plan_agent_decision(request)
        )


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    execute: Callable[
        [dict[str, Any]],
        dict[str, Any] | Awaitable[dict[str, Any]],
    ]
    summarize_input: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def as_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class AgentRunnerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "decision",
        "tool_started",
        "observation",
        "stopped",
    ]
    iteration: int = Field(ge=0)
    data: dict[str, Any] = Field(default_factory=dict)


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal[
        "finished",
        "needs_user",
        "gap",
        "cancelled",
        "exhausted",
    ]
    stop_reason: Literal[
        "finish",
        "ask_user",
        "gap",
        "cancelled",
        "max_iterations",
    ]
    output: dict[str, Any]
    decisions: tuple[AgentDecision, ...]
    observations: tuple[AgentObservation, ...]
    messages: tuple[AgentMessage, ...] = ()


EventSink = Callable[[AgentRunnerEvent], Any | Awaitable[Any]]
CancellationCheck = Callable[[], bool]


class AgentRunner:
    """Run one bounded Model -> Tool -> Observation cycle."""

    def __init__(
        self,
        *,
        agent_name: str,
        planner: AgentPlanner,
        tools: tuple[AgentTool, ...],
        max_iterations: int = 8,
        finish_validator: Callable[
            [dict[str, Any]], dict[str, Any]
        ]
        | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        self.agent_name = agent_name
        self.planner = planner
        self.tools = {tool.name: tool for tool in tools}
        self.max_iterations = max_iterations
        self.finish_validator = finish_validator

    def run(
        self,
        *,
        goal: str,
        context: dict[str, Any],
        messages: tuple[AgentMessage, ...] = (),
        event_sink: EventSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> AgentRunResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "AgentRunner.run() cannot be called from an active event loop; "
                "use await AgentRunner.arun()"
            )
        return asyncio.run(
            self.arun(
                goal=goal,
                context=context,
                messages=messages,
                event_sink=event_sink,
                cancellation_requested=cancellation_requested,
            )
        )

    async def arun(
        self,
        *,
        goal: str,
        context: dict[str, Any],
        messages: tuple[AgentMessage, ...] = (),
        event_sink: EventSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> AgentRunResult:
        observations: list[AgentObservation] = []
        decisions: list[AgentDecision] = []
        tool_schemas = tuple(tool.as_schema() for tool in self.tools.values())
        for iteration in range(1, self.max_iterations + 1):
            if cancellation_requested is not None and cancellation_requested():
                result = AgentRunResult(
                    status="cancelled",
                    stop_reason="cancelled",
                    output={},
                    decisions=tuple(decisions),
                    observations=tuple(observations),
                    messages=messages,
                )
                await self._emit(
                    event_sink,
                    AgentRunnerEvent(
                        kind="stopped",
                        iteration=iteration - 1,
                        data={"stop_reason": result.stop_reason},
                    ),
                )
                return result
            planned = self.planner.decide(
                AgentPlanningRequest(
                    agent_name=self.agent_name,
                    goal=goal,
                    iteration=iteration,
                    context=context,
                    tools=tool_schemas,
                    observations=tuple(observations),
                    messages=messages,
                )
            )
            decision = (
                await planned if inspect.isawaitable(planned) else planned
            )
            decision = AgentDecision.model_validate(decision)
            decisions.append(decision)
            await self._emit(
                event_sink,
                AgentRunnerEvent(
                    kind="decision",
                    iteration=iteration,
                    data=decision.model_dump(mode="json"),
                ),
            )
            if decision.action == "finish":
                if self.finish_validator is not None:
                    validation = self.finish_validator(dict(decision.output))
                    if not bool(validation.get("ok")):
                        observations.append(
                            AgentObservation(
                                sequence=len(observations) + 1,
                                tool_name="validate_finish",
                                tool_input=decision.output,
                                data=validation,
                            )
                        )
                        continue
                result = AgentRunResult(
                    status="finished",
                    stop_reason="finish",
                    output=decision.output,
                    decisions=tuple(decisions),
                    observations=tuple(observations),
                    messages=messages,
                )
                await self._emit_stopped(event_sink, iteration, result)
                return result
            if decision.action == "ask_user":
                result = AgentRunResult(
                    status="needs_user",
                    stop_reason="ask_user",
                    output=decision.output,
                    decisions=tuple(decisions),
                    observations=tuple(observations),
                    messages=messages,
                )
                await self._emit_stopped(event_sink, iteration, result)
                return result
            if decision.action == "report_gap":
                result = AgentRunResult(
                    status="gap",
                    stop_reason="gap",
                    output=decision.output,
                    decisions=tuple(decisions),
                    observations=tuple(observations),
                    messages=messages,
                )
                await self._emit_stopped(event_sink, iteration, result)
                return result
            try:
                tool = self.tools[str(decision.tool_name)]
            except KeyError:
                observations.append(
                    AgentObservation(
                        sequence=len(observations) + 1,
                        tool_name=str(decision.tool_name),
                        tool_input=decision.tool_input,
                        data={
                            "ok": False,
                            "error_type": "unavailable_tool",
                            "error": "Tool is not available to this Agent",
                            "available_tools": sorted(self.tools),
                        },
                    )
                )
                continue
            await self._emit(
                event_sink,
                AgentRunnerEvent(
                    kind="tool_started",
                    iteration=iteration,
                    data={
                        "tool_name": tool.name,
                        "tool_input": decision.tool_input,
                    },
                ),
            )
            try:
                executed = tool.execute(dict(decision.tool_input))
                data = (
                    await executed
                    if inspect.isawaitable(executed)
                    else executed
                )
            except Exception as exc:
                data = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            observation_input = (
                tool.summarize_input(dict(decision.tool_input))
                if tool.summarize_input is not None
                else decision.tool_input
            )
            observation = AgentObservation(
                sequence=len(observations) + 1,
                tool_name=tool.name,
                tool_input=observation_input,
                data=data,
            )
            observations.append(observation)
            await self._emit(
                event_sink,
                AgentRunnerEvent(
                    kind="observation",
                    iteration=iteration,
                    data=observation.model_dump(mode="json"),
                ),
            )
        result = AgentRunResult(
            status="exhausted",
            stop_reason="max_iterations",
            output={},
            decisions=tuple(decisions),
            observations=tuple(observations),
            messages=messages,
        )
        await self._emit_stopped(event_sink, self.max_iterations, result)
        return result

    @staticmethod
    async def _emit(
        sink: EventSink | None,
        event: AgentRunnerEvent,
    ) -> None:
        if sink is None:
            return
        emitted = sink(event)
        if inspect.isawaitable(emitted):
            await emitted

    async def _emit_stopped(
        self,
        sink: EventSink | None,
        iteration: int,
        result: AgentRunResult,
    ) -> None:
        await self._emit(
            sink,
            AgentRunnerEvent(
                kind="stopped",
                iteration=iteration,
                data={
                    "status": result.status,
                    "stop_reason": result.stop_reason,
                },
            ),
        )


class AgentDecisionLoop(AgentRunner):
    """Compatibility Adapter preserving the original synchronous contract."""

    def run(self, *, goal: str, context: dict[str, Any]) -> AgentRunResult:
        result = super().run(goal=goal, context=context)
        if result.status == "exhausted":
            raise RuntimeError(
                f"{self.agent_name} exceeded {self.max_iterations} planning "
                "iterations; recent decisions="
                + repr(
                    [
                        {
                            "action": item.action,
                            "tool_name": item.tool_name,
                            "reason": item.reason_summary,
                        }
                        for item in result.decisions[-5:]
                    ]
                )
                + "; last_observation="
                + repr(
                    result.observations[-1].model_dump(mode="json")
                    if result.observations
                    else None
                )
            )
        return result


AgentLoopResult = AgentRunResult


__all__ = [
    "AgentDecision",
    "AgentDecisionLoop",
    "AgentMessage",
    "AgentRunner",
    "AgentRunnerEvent",
    "AgentRunResult",
    "AgentLoopResult",
    "AgentObservation",
    "AgentPlanner",
    "AgentPlanningRequest",
    "AgentTool",
    "GatewayAgentPlanner",
]
