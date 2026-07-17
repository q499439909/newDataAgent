from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..domain.operators import AnnotationRef, AssetRef, EmbeddingRef, OperatorSpecVersion


class OperatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    current_path: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[AssetRef] = Field(default_factory=list)
    annotations: list[AnnotationRef] = Field(default_factory=list)
    embeddings: list[EmbeddingRef] = Field(default_factory=list)


class OperatorContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    work_order_id: str
    owner_id: str
    purpose: Literal["preview", "development", "production"] = "production"
    shared: dict[str, Any] = Field(default_factory=dict)


class OperatorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_path: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[AssetRef] = Field(default_factory=list)
    annotations: list[AnnotationRef] = Field(default_factory=list)
    embeddings: list[EmbeddingRef] = Field(default_factory=list)
    decision: str = "continue"
    reason_codes: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    model_version_id: str | None = None


class Operator(Protocol):
    spec: OperatorSpecVersion

    def execute(
        self,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict[str, Any],
    ) -> OperatorResult: ...


__all__ = [
    "AnnotationRef",
    "AssetRef",
    "EmbeddingRef",
    "Operator",
    "OperatorContext",
    "OperatorInput",
    "OperatorResult",
]
