from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    tool_name: str
    tool_input: dict[str, Any]
    data: dict[str, Any]


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


class AgentPlanner(Protocol):
    def decide(self, request: AgentPlanningRequest) -> AgentDecision:
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
    execute: Callable[[dict[str, Any]], dict[str, Any]]

    def as_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class AgentLoopResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["finished", "needs_user", "gap"]
    output: dict[str, Any]
    decisions: tuple[AgentDecision, ...]
    observations: tuple[AgentObservation, ...]


class AgentDecisionLoop:
    """Bounded Plan -> Tool -> Observation loop shared by autonomous Agents."""

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

    def run(self, *, goal: str, context: dict[str, Any]) -> AgentLoopResult:
        observations: list[AgentObservation] = []
        decisions: list[AgentDecision] = []
        tool_schemas = tuple(tool.as_schema() for tool in self.tools.values())
        for iteration in range(1, self.max_iterations + 1):
            decision = self.planner.decide(
                AgentPlanningRequest(
                    agent_name=self.agent_name,
                    goal=goal,
                    iteration=iteration,
                    context=context,
                    tools=tool_schemas,
                    observations=tuple(observations),
                )
            )
            decisions.append(decision)
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
                return AgentLoopResult(
                    status="finished",
                    output=decision.output,
                    decisions=tuple(decisions),
                    observations=tuple(observations),
                )
            if decision.action == "ask_user":
                return AgentLoopResult(
                    status="needs_user",
                    output=decision.output,
                    decisions=tuple(decisions),
                    observations=tuple(observations),
                )
            if decision.action == "report_gap":
                return AgentLoopResult(
                    status="gap",
                    output=decision.output,
                    decisions=tuple(decisions),
                    observations=tuple(observations),
                )
            try:
                tool = self.tools[str(decision.tool_name)]
            except KeyError as exc:
                raise ValueError(
                    f"{self.agent_name} selected an unavailable tool: "
                    f"{decision.tool_name}"
                ) from exc
            try:
                data = tool.execute(dict(decision.tool_input))
            except Exception as exc:
                data = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            observations.append(
                AgentObservation(
                    sequence=len(observations) + 1,
                    tool_name=tool.name,
                    tool_input=decision.tool_input,
                    data=data,
                )
            )
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
                    for item in decisions[-5:]
                ]
            )
            + "; last_observation="
            + repr(
                observations[-1].model_dump(mode="json")
                if observations
                else None
            )
        )


__all__ = [
    "AgentDecision",
    "AgentDecisionLoop",
    "AgentLoopResult",
    "AgentObservation",
    "AgentPlanner",
    "AgentPlanningRequest",
    "AgentTool",
    "GatewayAgentPlanner",
]
