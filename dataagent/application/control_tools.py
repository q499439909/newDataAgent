from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..tools import (
    ControlToolExecutor,
    ToolContext,
    ToolEffect,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


class InspectWorkOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_order_id: str = Field(min_length=1)


class ResumeWorkOrderInput(InspectWorkOrderInput):
    decision: dict[str, Any]


class SubmitRunInput(InspectWorkOrderInput):
    idempotency_key: str = Field(min_length=1)


class WorkOrderControlTools:
    """Typed WorkOrder controls shared by Agent and UI adapters."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.executor = ControlToolExecutor(
            ToolRegistry(
                (
                    ToolSpec(
                        name="inspect_work_order",
                        description="Read the current WorkOrder state.",
                        input_model=InspectWorkOrderInput,
                        executor=self._inspect,
                    ),
                    ToolSpec(
                        name="resume_work_order",
                        description=(
                            "Resume the current LangGraph interrupt with an "
                            "explicit structured decision."
                        ),
                        input_model=ResumeWorkOrderInput,
                        executor=self._resume,
                        effect=ToolEffect.CONTROL,
                    ),
                    ToolSpec(
                        name="continue_work_order",
                        description=(
                            "Continue pending WorkOrder execution without "
                            "fabricating a user decision."
                        ),
                        input_model=InspectWorkOrderInput,
                        executor=self._continue,
                        effect=ToolEffect.CONTROL,
                    ),
                    ToolSpec(
                        name="submit_dataset_run",
                        description="Submit the approved Pipeline as a Run.",
                        input_model=SubmitRunInput,
                        executor=self._submit,
                        effect=ToolEffect.CONTROL,
                    ),
                )
            )
        )

    def execute(
        self,
        *,
        name: str,
        owner_id: str,
        raw_input: dict[str, Any],
    ) -> tuple[ToolResult, dict[str, Any]]:
        return self.executor.execute(
            name=name,
            stage=name,
            context=ToolContext(owner_id=owner_id),
            raw_input=raw_input,
        )

    def _inspect(
        self,
        context: ToolContext,
        request: InspectWorkOrderInput,
    ) -> ToolResult:
        return self._success(
            "inspect_work_order",
            self.runtime.state(
                work_order_id=request.work_order_id,
                owner_id=context.owner_id,
            ),
        )

    def _resume(
        self,
        context: ToolContext,
        request: ResumeWorkOrderInput,
    ) -> ToolResult:
        return self._success(
            "resume_work_order",
            self.runtime.resume(
                work_order_id=request.work_order_id,
                owner_id=context.owner_id,
                decision=request.decision,
            ),
        )

    def _continue(
        self,
        context: ToolContext,
        request: InspectWorkOrderInput,
    ) -> ToolResult:
        return self._success(
            "continue_work_order",
            self.runtime.continue_work_order(
                work_order_id=request.work_order_id,
                owner_id=context.owner_id,
            ),
        )

    def _submit(
        self,
        context: ToolContext,
        request: SubmitRunInput,
    ) -> ToolResult:
        return self._success(
            "submit_dataset_run",
            self.runtime.submit_dataset_run(
                work_order_id=request.work_order_id,
                owner_id=context.owner_id,
                idempotency_key=request.idempotency_key,
            ),
        )

    @staticmethod
    def _success(name: str, data: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=True,
            tool=name,
            status="succeeded",
            summary=f"{name} completed.",
            data=data,
        )


__all__ = ["WorkOrderControlTools"]
