from __future__ import annotations

from dataagent.agents.requirement import (
    GatewayRequirementPlanner,
    RequirementPlanner,
)
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.domain.specs import ConstraintContract, RequirementDraft


class RecordingRequirementPlanner(RequirementPlanner):
    def __init__(self, draft: RequirementDraft) -> None:
        self.draft = draft
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return self.draft


class StubPlanningGateway:
    def plan_requirement_draft(self, *, requirement, data_sources):
        return {
            "objective": requirement,
            "constraints": [
                {
                    "id": "constraint_vehicle_count",
                    "source_text": "至少包含三辆汽车",
                    "scope": "asset",
                    "field": "image.vehicle_count",
                    "operator": "gte",
                    "value": 3,
                    "unit": "count",
                    "required_evidence_type": "detected_vehicle_count",
                }
            ],
        }


def test_requirement_draft_accepts_an_unseen_data_constraint() -> None:
    draft = RequirementDraft(
        objective="筛选至少包含三辆汽车的图片",
        constraints=(
            ConstraintContract(
                id="constraint_vehicle_count",
                source_text="至少包含三辆汽车",
                scope="asset",
                field="image.vehicle_count",
                operator="gte",
                value=3,
                unit="count",
                required_evidence_type="detected_vehicle_count",
            ),
        ),
    )

    assert draft.constraints[0].field == "image.vehicle_count"
    assert draft.constraints[0].value == 3
    assert draft.required_capabilities == ()


def test_gateway_requirement_planner_validates_a_model_draft() -> None:
    planner = GatewayRequirementPlanner(StubPlanningGateway())

    draft = planner.plan(
        type(
            "Request",
            (),
            {
                "requirement": "筛选至少包含三辆汽车的图片",
                "data_sources": ({"type": "local_directory", "uri": "D:/data"},),
            },
        )()
    )

    assert draft.constraints[0].id == "constraint_vehicle_count"
    assert draft.required_capabilities == ()


def test_agent_runtime_uses_requirement_planner_instead_of_fixed_requirement_rules() -> None:
    planner = RecordingRequirementPlanner(
        RequirementDraft(
            objective="筛选至少包含三辆汽车的图片",
            constraints=(
                ConstraintContract(
                    id="constraint_vehicle_count",
                    source_text="至少包含三辆汽车",
                    scope="asset",
                    field="image.vehicle_count",
                    operator="gte",
                    value=3,
                    unit="count",
                    required_evidence_type="detected_vehicle_count",
                ),
            ),
        )
    )
    runtime = AgentRuntime(
        include_datajuicer=False,
        requirement_planner=planner,
    )

    result = runtime.start(
        owner_id="owner",
        requirement="筛选至少包含三辆汽车的图片",
        data_sources=[{"type": "local_directory", "uri": "D:/data/images"}],
    )

    assert len(planner.requests) == 1
    constraint = result["state"]["task_spec"]["constraints"][0]
    assert constraint["field"] == "image.vehicle_count"
    assert constraint["value"] == 3
    assert result["state"]["task_spec"]["capability_requirements"] == []
