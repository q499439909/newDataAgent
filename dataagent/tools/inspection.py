from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .observations import ToolEvidence, ToolResult
from .spec import ToolConfirmation, ToolContext, ToolEffect, ToolSpec


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


class QueryControlFactsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facets: tuple[FactFacet, ...] = Field(min_length=1)


def _evidence_for_facts(facts: dict[str, Any]) -> tuple[ToolEvidence, ...]:
    evidence: list[ToolEvidence] = []
    for facet, value in facts.items():
        if not isinstance(value, dict):
            continue
        entity_id = value.get("id")
        if entity_id:
            evidence.append(ToolEvidence(kind=facet, id=str(entity_id)))
    return tuple(evidence)


def _query_control_facts(
    context: ToolContext, request: QueryControlFactsInput
) -> ToolResult:
    facts = {
        facet: context.control_facts[facet]
        for facet in request.facets
        if facet in context.control_facts
    }
    missing = tuple(facet for facet in request.facets if facet not in facts)
    return ToolResult(
        ok=True,
        tool="query_control_facts",
        status="succeeded",
        summary=f"Returned {len(facts)} grounded control fact facets.",
        data={"facts": facts, "missing_facets": missing},
        evidence=_evidence_for_facts(facts),
        next_actions=("query_control_facts",) if missing else (),
    )


def query_control_facts_spec() -> ToolSpec:
    return ToolSpec(
        name="query_control_facts",
        description="Return selected facts supplied by the control-plane evidence renderer.",
        input_model=QueryControlFactsInput,
        executor=_query_control_facts,
        tags=("inspection", "grounded_facts", "read_only"),
        effect=ToolEffect.READ,
        confirmation=ToolConfirmation.AUTO,
    )


__all__ = ["FactFacet", "QueryControlFactsInput", "query_control_facts_spec"]
