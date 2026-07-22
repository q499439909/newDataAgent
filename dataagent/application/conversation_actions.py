from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    TypeAdapter,
    ValidationError,
    model_validator,
)


class ConversationIntent(StrEnum):
    CHAT = "CHAT"
    START_WORK_ORDER = "START_WORK_ORDER"
    PROVIDE_SOURCE = "PROVIDE_SOURCE"
    EDIT_TASK_SPEC = "EDIT_TASK_SPEC"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    SELECT_PIPELINE = "SELECT_PIPELINE"
    SUBMIT_RUN = "SUBMIT_RUN"
    RETRY_RUN = "RETRY_RUN"
    RERUN_PIPELINE = "RERUN_PIPELINE"
    RECOMPILE_PIPELINE = "RECOMPILE_PIPELINE"
    RUN_STATUS = "RUN_STATUS"
    CONTROL_RUN = "CONTROL_RUN"
    QUERY_CONTROL_FACTS = "QUERY_CONTROL_FACTS"
    RESOLVE_GAP = "RESOLVE_GAP"


class ConversationActionError(ValueError):
    def __init__(self, message: str, *, details: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.details = details or []


class _ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = ""
    _resolved_by: str = PrivateAttr(default="model")
    _fallback_reason: str | None = PrivateAttr(default=None)

    @property
    def resolved_by(self) -> str:
        return self._resolved_by

    @property
    def fallback_reason(self) -> str | None:
        return self._fallback_reason

    def with_resolution(
        self,
        *,
        resolved_by: str,
        fallback_reason: str | None = None,
    ) -> "_ActionBase":
        resolved = self.model_copy(deep=True)
        resolved._resolved_by = resolved_by
        resolved._fallback_reason = fallback_reason
        return resolved


class ChatAction(_ActionBase):
    intent: Literal[ConversationIntent.CHAT] = ConversationIntent.CHAT


class StartWorkOrderAction(_ActionBase):
    intent: Literal[ConversationIntent.START_WORK_ORDER]
    requirement: str = Field(min_length=1)
    source: str | None = None


class ProvideSourceAction(_ActionBase):
    intent: Literal[ConversationIntent.PROVIDE_SOURCE]
    source: str = Field(min_length=1)


class EditTaskSpecAction(_ActionBase):
    intent: Literal[ConversationIntent.EDIT_TASK_SPEC]
    action: Literal["accept_defaults"] | None = None
    task_spec_patch: dict[str, Any] | None = None
    confirm_after_edit: bool = False

    @model_validator(mode="after")
    def require_explicit_change(self) -> "EditTaskSpecAction":
        if self.action != "accept_defaults" and not self.task_spec_patch:
            raise ValueError(
                "EDIT_TASK_SPEC requires a non-empty task_spec_patch or "
                'action="accept_defaults"'
            )
        return self


class ApproveAction(_ActionBase):
    intent: Literal[ConversationIntent.APPROVE]


class RejectAction(_ActionBase):
    intent: Literal[ConversationIntent.REJECT]
    reason: str | None = None


class SelectPipelineAction(_ActionBase):
    intent: Literal[ConversationIntent.SELECT_PIPELINE]
    strategy: Literal["retention_first", "balanced", "quality_first"]


class SubmitRunAction(_ActionBase):
    intent: Literal[ConversationIntent.SUBMIT_RUN]


class RetryRunAction(_ActionBase):
    intent: Literal[ConversationIntent.RETRY_RUN]
    run_id: str | None = None


class RerunPipelineAction(_ActionBase):
    intent: Literal[ConversationIntent.RERUN_PIPELINE]
    run_id: str | None = None


class RecompilePipelineAction(_ActionBase):
    intent: Literal[ConversationIntent.RECOMPILE_PIPELINE]
    run_id: str | None = None


class RunStatusAction(_ActionBase):
    intent: Literal[ConversationIntent.RUN_STATUS]
    run_id: str | None = None


class ControlRunAction(_ActionBase):
    intent: Literal[ConversationIntent.CONTROL_RUN]
    action: Literal["pause", "resume", "cancel"]
    run_id: str | None = None


FactFacet = Literal[
    "work_order",
    "run",
    "pipeline",
    "operators",
    "task_spec",
    "dataset",
    "outcome",
    "audit",
]


class QueryControlFactsAction(_ActionBase):
    intent: Literal[ConversationIntent.QUERY_CONTROL_FACTS]
    facets: tuple[FactFacet, ...] = Field(min_length=1)


class ResolveGapAction(_ActionBase):
    intent: Literal[ConversationIntent.RESOLVE_GAP]
    action: Literal["retry", "revise_task", "terminate"]
    runtime_backend: Literal["cpu", "cuda", "remote"] | None = None


ConversationAction = Annotated[
    ChatAction
    | StartWorkOrderAction
    | ProvideSourceAction
    | EditTaskSpecAction
    | ApproveAction
    | RejectAction
    | SelectPipelineAction
    | SubmitRunAction
    | RetryRunAction
    | RerunPipelineAction
    | RecompilePipelineAction
    | RunStatusAction
    | ControlRunAction
    | QueryControlFactsAction
    | ResolveGapAction,
    Field(discriminator="intent"),
]


_ACTION_ADAPTER = TypeAdapter(ConversationAction)


def parse_conversation_action(payload: dict[str, Any]) -> ConversationAction:
    try:
        return _ACTION_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise ConversationActionError(
            "Model returned an invalid conversation action",
            details=exc.errors(include_url=False),
        ) from exc


def conversation_action_json_schema() -> dict[str, Any]:
    return _ACTION_ADAPTER.json_schema()
