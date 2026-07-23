from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..domain.operators import RuntimeBackend
from ..operators.catalog_matching import HybridOperatorCatalogMatcher
from ..operators.catalog_ranking import OperatorCandidateRanker, OperatorRankingPolicy
from .observations import ToolEvidence, ToolResult
from .spec import ToolConfirmation, ToolContext, ToolEffect, ToolSpec


class RetrieveOperatorsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(min_length=1)
    required_capabilities: tuple[str, ...] = ()
    allow_draft_candidates: bool = False
    available_runtime_backends: frozenset[RuntimeBackend] = frozenset(
        {RuntimeBackend.CPU}
    )
    limit: int = Field(default=20, ge=1, le=100)


def _retrieve_operators(
    context: ToolContext, request: RetrieveOperatorsInput
) -> ToolResult:
    if context.operator_registry is None:
        raise RuntimeError("OperatorRegistry is required")
    matcher = HybridOperatorCatalogMatcher(context.operator_registry)
    candidates = matcher.match(
        request.requirement,
        required_capabilities=request.required_capabilities,
        allow_draft_candidates=request.allow_draft_candidates,
    )
    ranked = OperatorCandidateRanker(context.operator_registry).rank(
        candidates,
        policy=OperatorRankingPolicy(
            available_runtime_backends=request.available_runtime_backends,
            allow_draft_candidates=request.allow_draft_candidates,
        ),
    )[: request.limit]
    payload = [item.model_dump(mode="json") for item in ranked]
    return ToolResult(
        ok=True,
        tool="retrieve_operators",
        status="succeeded",
        summary=f"Found {len(payload)} candidate operators.",
        data={"operators": payload},
        evidence=tuple(
            ToolEvidence(
                kind="operator_version",
                id=item.operator_version_id,
                metadata={
                    "executable": item.executable,
                    "status": item.status.value,
                },
            )
            for item in ranked
        ),
        next_actions=("compile_pipeline_artifact",) if ranked else (),
    )


def retrieve_operators_spec() -> ToolSpec:
    return ToolSpec(
        name="retrieve_operators",
        description="Retrieve and rank governed Operator versions for a requirement.",
        input_model=RetrieveOperatorsInput,
        executor=_retrieve_operators,
        tags=("planning", "operator_catalog", "read_only"),
        effect=ToolEffect.READ,
        confirmation=ToolConfirmation.AUTO,
    )


__all__ = ["RetrieveOperatorsInput", "retrieve_operators_spec"]
