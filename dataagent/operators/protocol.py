from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..domain.operators import OperatorSpecVersion


class OperatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    current_path: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, Any] = Field(default_factory=dict)


class OperatorContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    work_order_id: str
    owner_id: str
    shared: dict[str, Any] = Field(default_factory=dict)


class OperatorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_path: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, Any] = Field(default_factory=dict)
    decision: str = "continue"
    reason_codes: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class Operator(Protocol):
    spec: OperatorSpecVersion

    def execute(
        self,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict[str, Any],
    ) -> OperatorResult: ...
