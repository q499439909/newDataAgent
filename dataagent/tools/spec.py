from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from .observations import ToolResult


class ToolEffect(StrEnum):
    READ = "read"
    DRAFT = "draft"
    CONTROL = "control"


class ToolConfirmation(StrEnum):
    AUTO = "auto"
    DRAFT_ONLY = "draft_only"
    REQUIRED = "confirmation_required"


@dataclass(frozen=True)
class ToolContext:
    owner_id: str
    operator_registry: Any | None = None
    version_store: Any | None = None
    control_context: dict[str, Any] = field(default_factory=dict)
    control_facts: dict[str, Any] = field(default_factory=dict)
    confirmed: bool = False


ToolExecutor = Callable[[ToolContext, BaseModel], ToolResult]


class ToolSpec(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel] | None = None
    executor: ToolExecutor
    tags: tuple[str, ...] = ()
    effect: ToolEffect = ToolEffect.READ
    confirmation: ToolConfirmation = ToolConfirmation.AUTO


__all__ = [
    "ToolConfirmation",
    "ToolContext",
    "ToolEffect",
    "ToolExecutor",
    "ToolSpec",
]
