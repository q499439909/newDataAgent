from __future__ import annotations

from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from ..agents.runner import (
    AgentDecision,
    AgentPlanner,
    AgentRunResult,
    AgentRunner,
    AgentTool,
)
from ..agents.session import AgentSession
from ..agents.turn import TurnInput, TurnResult
from ..domain.specs import DataSourceSpec
from .control_tools import WorkOrderControlTools
from .source_references import (
    AmbiguousSourceReference,
    extract_explicit_data_sources,
)


class StartWorkOrderInput(BaseModel):
    data_sources: list[DataSourceSpec] = Field(min_length=1)


class WorkOrderTurnRuntime(Protocol):
    def start(
        self,
        *,
        owner_id: str,
        requirement: str,
        data_sources: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def state(
        self,
        *,
        work_order_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...

    def resume(
        self,
        *,
        work_order_id: str,
        owner_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]: ...

    def continue_work_order(
        self,
        *,
        work_order_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...


class WorkOrderRuntimeRootAgent:
    """Adapt the promoted Requirement Agent to AgentLoop's turn Interface."""

    def __init__(
        self,
        *,
        runtime: WorkOrderTurnRuntime,
        planner: AgentPlanner | None,
        max_iterations: int = 8,
        control_tools: WorkOrderControlTools | None = None,
    ) -> None:
        self.runtime = runtime
        self.planner = planner
        self.max_iterations = max_iterations
        self.control_tools = control_tools or WorkOrderControlTools(runtime)

    async def run_turn(
        self,
        turn: TurnInput,
        session: AgentSession,
        *,
        event_sink=None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> TurnResult:
        initial = self._start_from_explicit_sources(
            turn=turn,
            session=session,
        )
        if initial is not None:
            return initial

        clarification = self._active_requirement_clarification(
            turn=turn,
            session=session,
        )
        if clarification is not None:
            return clarification

        if self.planner is None:
            return self._run_without_model(turn, session)

        latest: dict[str, Any] | None = None

        def remember(result: dict[str, Any]) -> dict[str, Any]:
            nonlocal latest
            latest = result
            return result

        def active_work_order_id() -> str | None:
            if latest is not None and latest.get("work_order_id"):
                return str(latest["work_order_id"])
            return session.work_order_id

        def start_work_order(payload: dict[str, Any]) -> dict[str, Any]:
            validated = StartWorkOrderInput.model_validate(payload)
            return remember(
                self.runtime.start(
                    owner_id=turn.owner_id,
                    requirement=turn.content,
                    data_sources=[
                        item.model_dump(mode="json")
                        for item in validated.data_sources
                    ],
                )
            )

        def inspect_work_order(_payload: dict[str, Any]) -> dict[str, Any]:
            work_order_id = active_work_order_id()
            if not work_order_id:
                return {
                    "ok": False,
                    "error_type": "no_active_work_order",
                }
            result, _ = self.control_tools.execute(
                name="inspect_work_order",
                owner_id=turn.owner_id,
                raw_input={"work_order_id": work_order_id},
            )
            return remember(self._tool_data(result))

        def resume_work_order(payload: dict[str, Any]) -> dict[str, Any]:
            work_order_id = active_work_order_id()
            if not work_order_id:
                return {
                    "ok": False,
                    "error_type": "no_active_work_order",
                }
            result, _ = self.control_tools.execute(
                name="resume_work_order",
                owner_id=turn.owner_id,
                raw_input={
                    "work_order_id": work_order_id,
                    "decision": dict(payload["decision"]),
                },
            )
            return remember(self._tool_data(result))

        def continue_work_order(_payload: dict[str, Any]) -> dict[str, Any]:
            work_order_id = active_work_order_id()
            if not work_order_id:
                return {
                    "ok": False,
                    "error_type": "no_active_work_order",
                }
            result, _ = self.control_tools.execute(
                name="continue_work_order",
                owner_id=turn.owner_id,
                raw_input={"work_order_id": work_order_id},
            )
            return remember(self._tool_data(result))

        def submit_dataset_run(payload: dict[str, Any]) -> dict[str, Any]:
            work_order_id = active_work_order_id()
            if not work_order_id:
                return {
                    "ok": False,
                    "error_type": "no_active_work_order",
                }
            result, _ = self.control_tools.execute(
                name="submit_dataset_run",
                owner_id=turn.owner_id,
                raw_input={
                    "work_order_id": work_order_id,
                    "idempotency_key": str(payload["idempotency_key"]),
                },
            )
            return remember(self._tool_data(result))

        runner = AgentRunner(
            agent_name="requirement",
            planner=self.planner,
            tools=(
                AgentTool(
                    name="start_work_order",
                    description=(
                        "Create a WorkOrder for the latest user message. Extract "
                        "only explicitly supplied data-source references; the "
                        "runtime preserves the user's message as the authoritative "
                        "requirement."
                    ),
                    input_schema=StartWorkOrderInput.model_json_schema(),
                    execute=start_work_order,
                    return_direct=True,
                ),
                AgentTool(
                    name="inspect_work_order",
                    description="Read the current WorkOrder and interrupt facts.",
                    input_schema={
                        "type": "object",
                        "additionalProperties": False,
                    },
                    execute=inspect_work_order,
                ),
                AgentTool(
                    name="resume_work_order",
                    description=(
                        "Resume the current explicit LangGraph interrupt with "
                        "a structured user decision."
                    ),
                    input_schema={
                        "type": "object",
                        "required": ["decision"],
                        "properties": {
                            "decision": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    execute=resume_work_order,
                    return_direct=True,
                ),
                AgentTool(
                    name="continue_work_order",
                    description=(
                        "Continue a runnable WorkOrder that is not waiting for "
                        "a user decision."
                    ),
                    input_schema={
                        "type": "object",
                        "additionalProperties": False,
                    },
                    execute=continue_work_order,
                    return_direct=True,
                ),
                AgentTool(
                    name="submit_dataset_run",
                    description=(
                        "Submit the approved Pipeline when the WorkOrder state "
                        "says submit_dataset_run."
                    ),
                    input_schema={
                        "type": "object",
                        "required": ["idempotency_key"],
                        "properties": {
                            "idempotency_key": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    execute=submit_dataset_run,
                    return_direct=True,
                ),
            ),
            max_iterations=self.max_iterations,
            terminal_validator=self._validate_terminal_decision,
        )
        result = await runner.arun(
            goal=(
                "Handle the latest user or system message as the root "
                "Requirement Agent. Use WorkOrder facts and typed tools; do "
                "not classify the message into an intermediate intent. Never "
                "invent data-source references or silently approve an "
                "interrupt."
            ),
            context={
                "session_id": session.id,
                "work_order_id": session.work_order_id,
                "turn_source": turn.source,
                "turn_metadata": turn.metadata,
            },
            messages=session.messages,
            event_sink=event_sink,
            cancellation_requested=cancellation_requested,
        )
        return self._to_turn_result(result, latest, session)

    def _start_from_explicit_sources(
        self,
        *,
        turn: TurnInput,
        session: AgentSession,
    ) -> TurnResult | None:
        if session.work_order_id:
            return None
        supplied = turn.metadata.get("data_sources")
        if isinstance(supplied, list) and supplied:
            raw_sources = supplied
        else:
            try:
                raw_sources = extract_explicit_data_sources(turn.content)
            except AmbiguousSourceReference:
                return TurnResult(
                    status="waiting_for_user",
                    reply=(
                        "我无法可靠区分数据源路径和紧邻的需求文字。"
                        "请用引号包住完整路径，或在路径后加一个空格后重新发送。"
                    ),
                    stop_reason="ambiguous_source_reference",
                )
        if not raw_sources:
            return None
        sources = [
            DataSourceSpec.model_validate(item).model_dump(mode="json")
            for item in raw_sources
        ]
        result = self.runtime.start(
            owner_id=turn.owner_id,
            requirement=turn.content,
            data_sources=sources,
        )
        return self._runtime_turn_result(result)

    def _active_requirement_clarification(
        self,
        *,
        turn: TurnInput,
        session: AgentSession,
    ) -> TurnResult | None:
        """Forward clarification text to the owning Requirement Agent.

        The LangGraph interrupt already identifies both the target agent and
        the expected action. Replanning that routing decision with the root
        model adds latency and can distort the user's answer. The Requirement
        planner remains responsible for interpreting the answer semantically.
        """

        if not session.work_order_id:
            return None
        current = self.runtime.state(
            work_order_id=session.work_order_id,
            owner_id=turn.owner_id,
        )
        interrupts = current.get("interrupts") or ()
        if not interrupts:
            return None
        value = interrupts[0].get("value") or {}
        if value.get("kind") != "requirement_clarification":
            return None
        resumed = self.runtime.resume(
            work_order_id=session.work_order_id,
            owner_id=turn.owner_id,
            decision={"answer": turn.content},
        )
        return self._runtime_turn_result(resumed)

    @staticmethod
    def _validate_terminal_decision(
        decision: AgentDecision,
    ) -> dict[str, Any]:
        if decision.action in {"finish", "ask_user"}:
            reply = decision.output.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                return {
                    "ok": False,
                    "error_type": "empty_user_reply",
                    "message": (
                        "Root Requirement Agent terminal decisions require "
                        "a non-empty output.reply."
                    ),
                }
        return {"ok": True}

    def _run_without_model(
        self,
        turn: TurnInput,
        session: AgentSession,
    ) -> TurnResult:
        if not session.work_order_id:
            data_sources = turn.metadata.get("data_sources")
            if not isinstance(data_sources, list) or not data_sources:
                return TurnResult(
                    status="waiting_for_user",
                    reply=(
                        "A data-source reference is required before creating "
                        "the WorkOrder."
                    ),
                    stop_reason="data_source_required",
                )
            result = self.runtime.start(
                owner_id=turn.owner_id,
                requirement=turn.content,
                data_sources=data_sources,
            )
            return self._runtime_turn_result(result)

        current = self.runtime.state(
            work_order_id=session.work_order_id,
            owner_id=turn.owner_id,
        )
        if current.get("interrupts"):
            return self._runtime_turn_result(current)
        return self._runtime_turn_result(
            self.runtime.continue_work_order(
                work_order_id=session.work_order_id,
                owner_id=turn.owner_id,
            )
        )

    def _to_turn_result(
        self,
        result: AgentRunResult,
        runtime_result: dict[str, Any] | None,
        session: AgentSession,
    ) -> TurnResult:
        if runtime_result is not None:
            reply = str(result.output.get("reply") or "") or None
            if reply is None and result.status in {
                "cancelled",
                "exhausted",
                "gap",
            }:
                reply = (
                    "The Requirement Agent stopped before completing this "
                    f"turn ({result.stop_reason}). The latest WorkOrder state "
                    "was preserved; no automatic approval was applied."
                )
            return self._runtime_turn_result(runtime_result, reply=reply)
        if result.status == "needs_user":
            return TurnResult(
                status="waiting_for_user",
                reply=str(
                    result.output.get("reply")
                    or result.output.get("summary")
                    or "The Requirement Agent needs more information."
                ),
                work_order_id=session.work_order_id,
                stop_reason=result.stop_reason,
            )
        if result.status == "finished":
            return TurnResult(
                status="completed",
                reply=str(result.output.get("reply") or "") or None,
                work_order_id=session.work_order_id,
                stop_reason=result.stop_reason,
            )
        return TurnResult(
            status=(
                "cancelled"
                if result.status == "cancelled"
                else "failed"
            ),
            reply=(
                str(result.output.get("reply") or "") or
                (
                    "The Requirement Agent could not complete this turn "
                    f"within its bounded planning loop ({result.stop_reason}). "
                    "No automatic approval or semantic fallback was applied."
                )
            ),
            work_order_id=session.work_order_id,
            stop_reason=result.stop_reason,
        )

    @staticmethod
    def _tool_data(result) -> dict[str, Any]:
        if result.ok:
            return dict(result.data)
        return {
            "ok": False,
            "error_type": result.error_type,
            "summary": result.summary,
        }

    @staticmethod
    def _runtime_turn_result(
        result: dict[str, Any],
        *,
        reply: str | None = None,
    ) -> TurnResult:
        work_order_id = result.get("work_order_id")
        interrupts = result.get("interrupts") or ()
        state = result.get("state") or {}
        if interrupts:
            value = interrupts[0].get("value") or {}
            kind = str(value.get("kind") or "user_decision")
            default_replies = {
                "operator_plan_confirmation": (
                    "算子检索与能力覆盖已经完成。请检查 OperatorPlan 中的"
                    "算子、运行时、成本和限制；确认后我才会开始编排 Pipeline。"
                ),
                "pipeline_approval": (
                    "Pipeline 候选已经通过静态校验，请选择并批准一个方案。"
                ),
                "task_spec_confirmation": (
                    "需求已整理为 TaskSpec，请确认目标、约束和验收标准。"
                ),
            }
            return TurnResult(
                status="waiting_for_user",
                reply=reply or str(
                    value.get("summary")
                    or default_replies.get(
                        kind,
                        f"The WorkOrder is waiting for {kind}.",
                    )
                ),
                work_order_id=work_order_id,
                stop_reason=kind,
            )
        if state.get("terminated"):
            return TurnResult(
                status="completed",
                reply=reply,
                work_order_id=work_order_id,
                stop_reason="terminated",
            )
        return TurnResult(
            status="completed",
            reply=reply,
            work_order_id=work_order_id,
            stop_reason=str(state.get("next_action") or "completed"),
        )


__all__ = ["WorkOrderRuntimeRootAgent", "WorkOrderTurnRuntime"]
