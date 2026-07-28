from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ...domain.specs import RequirementDraft


@dataclass(frozen=True)
class RequirementPlanningRequest:
    requirement: str
    data_sources: tuple[dict, ...]
    work_order_id: str


class RequirementPlanner(Protocol):
    """Port used by the Requirement Agent to interpret an open-ended request."""

    def plan(self, request: RequirementPlanningRequest) -> RequirementDraft:
        ...


class RequirementPlanningGateway(Protocol):
    def plan_requirement_draft(
        self,
        *,
        requirement: str,
        data_sources: tuple[dict, ...],
    ) -> dict[str, Any]:
        ...


class GatewayRequirementPlanner:
    """Adapter that validates an LLM planning response at the domain boundary."""

    def __init__(self, gateway: RequirementPlanningGateway) -> None:
        self.gateway = gateway

    def plan(self, request: RequirementPlanningRequest) -> RequirementDraft:
        payload = self.gateway.plan_requirement_draft(
            requirement=request.requirement,
            data_sources=request.data_sources,
        )
        return RequirementDraft.model_validate(payload)
