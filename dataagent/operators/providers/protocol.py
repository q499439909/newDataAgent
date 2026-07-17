from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ...domain.operators import OperatorCategory, OperatorSpecVersion, RuntimeBackend
from ..protocol import OperatorContext, OperatorInput, OperatorResult


class ProviderHealthStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    provider_version: str
    status: ProviderHealthStatus
    message: str = ""


class ProviderOperatorDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    provider_version: str
    provider_operator_ref: str
    display_name: str
    description: str
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
    tags: frozenset[str] = frozenset()
    source_digest: str = ""
    suggested_category: OperatorCategory | None = None
    suggested_secondary_category: str | None = None
    operator_spec: OperatorSpecVersion | None = None


class ProviderValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    normalized_parameters: dict[str, Any] = Field(default_factory=dict)
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ProviderExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_operator_ref: str
    runtime_backend: RuntimeBackend
    context: OperatorContext
    input_data: OperatorInput
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProviderExecuteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    result: OperatorResult | None = None
    error_type: str | None = None
    message: str = ""
    duration_seconds: float = Field(default=0, ge=0)
    stdout_tail: str = ""
    stderr_tail: str = ""


class ProviderDatasetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    input_data: OperatorInput


class ProviderDatasetExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_operator_ref: str
    runtime_backend: RuntimeBackend
    context: OperatorContext
    items: tuple[ProviderDatasetItem, ...]
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProviderDatasetItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    result: OperatorResult


class ProviderDatasetExecuteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    items: tuple[ProviderDatasetItemResult, ...] = ()
    error_type: str | None = None
    message: str = ""
    duration_seconds: float = Field(default=0, ge=0)
    stdout_tail: str = ""
    stderr_tail: str = ""


class OperatorProvider(Protocol):
    provider_id: str
    provider_version: str

    def discover(self) -> list[ProviderOperatorDescriptor]: ...

    def describe(self, provider_operator_ref: str) -> ProviderOperatorDescriptor: ...

    def validate(
        self,
        provider_operator_ref: str,
        parameters: dict[str, Any],
        runtime_backend: RuntimeBackend,
    ) -> ProviderValidationResult: ...

    def execute(self, request: ProviderExecuteRequest) -> ProviderExecuteResult: ...

    def execute_dataset(
        self, request: ProviderDatasetExecuteRequest
    ) -> ProviderDatasetExecuteResult: ...

    def health(self) -> ProviderHealth: ...


__all__ = [
    "OperatorProvider",
    "ProviderExecuteRequest",
    "ProviderExecuteResult",
    "ProviderDatasetExecuteRequest",
    "ProviderDatasetExecuteResult",
    "ProviderDatasetItem",
    "ProviderDatasetItemResult",
    "ProviderHealth",
    "ProviderHealthStatus",
    "ProviderOperatorDescriptor",
    "ProviderValidationResult",
]
