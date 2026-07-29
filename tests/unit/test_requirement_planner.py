from __future__ import annotations

import pytest

from dataagent.agents.requirement import (
    GatewayRequirementPlanner,
    RequirementPlanner,
    build_requirement_graph,
)
from dataagent.agents.runner import AgentDecision
from dataagent.application.work_order_runtime import WorkOrderRuntime
from dataagent.domain.specs import ConstraintContract, RequirementDraft
from dataagent.domain.specs import (
    RequirementClauseTrace,
    validate_requirement_draft_grounding,
)


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


class RequirementLoopPlanner:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return next(self.decisions)


class ClarificationWorkOrderPlanner:
    def __init__(self) -> None:
        self.steps = {
            "requirement": iter(
                (
                    AgentDecision(
                        action="finish",
                        reason_summary="Run Requirement Agent.",
                        output={"action": "run_requirement_agent"},
                    ),
                    AgentDecision(
                        action="finish",
                        reason_summary="Ask the user to confirm TaskSpec.",
                        output={"action": "confirm_task_spec"},
                    ),
                )
            ),
            "requirement_planning": iter(
                (
                    AgentDecision(
                        action="ask_user",
                        reason_summary="A similarity threshold is required.",
                        output={
                            "questions": [
                                "What similarity threshold defines a duplicate?"
                            ]
                        },
                    ),
                    AgentDecision(
                        action="tool",
                        reason_summary="Validate the clarified requirement.",
                        tool_name="validate_requirement_draft",
                        tool_input={
                            "objective": "Remove similar documents.",
                            "constraints": [
                                {
                                    "id": "constraint_document_similarity",
                                    "source_text": "Remove similar documents",
                                    "scope": "dataset",
                                    "field": "document.similarity",
                                    "operator": "gte",
                                    "value": 0.8,
                                    "unit": "score",
                                    "required_evidence_type": (
                                        "document_similarity"
                                    ),
                                }
                            ],
                            "clause_traces": [
                                {
                                    "source_text": "Remove similar documents",
                                    "role": "constraint",
                                    "constraint_refs": [
                                        "constraint_document_similarity"
                                    ],
                                },
                                {
                                    "source_text": "Use threshold 0.8",
                                    "role": "definition",
                                    "constraint_refs": [
                                        "constraint_document_similarity"
                                    ],
                                    "normalized_effect": {
                                        "similarity_threshold": 0.8
                                    },
                                },
                            ],
                        },
                    ),
                    AgentDecision(
                        action="finish",
                        reason_summary="The clarified draft is grounded.",
                        output={"use_validated_draft": True},
                    ),
                )
            ),
        }

    def decide(self, request):
        return next(self.steps[request.agent_name])


def test_grounding_accepts_a_definition_linked_to_an_existing_constraint() -> None:
    requirement = (
        "保留主要人物穿深色服装的图片；"
        "主要人物指画面中可见面积最大的人物。"
    )
    draft = RequirementDraft(
        objective="保留主要人物穿深色服装的图片",
        constraints=(
            ConstraintContract(
                id="constraint_subject_clothing",
                source_text="保留主要人物穿深色服装的图片",
                scope="asset",
                field="image.main_subject.clothing_color",
                operator="eq",
                value="dark",
                unit="category",
                required_evidence_type="subject_clothing_color_classification",
            ),
        ),
        clause_traces=(
            RequirementClauseTrace(
                source_text="保留主要人物穿深色服装的图片",
                role="constraint",
                constraint_refs=("constraint_subject_clothing",),
            ),
            RequirementClauseTrace(
                source_text="主要人物指画面中可见面积最大的人物",
                role="definition",
                constraint_refs=("constraint_subject_clothing",),
                normalized_effect={
                    "subject_selector": "largest_visible_person_by_area"
                },
            ),
        ),
    )

    observation = validate_requirement_draft_grounding(requirement, draft)

    assert observation.ok is True
    assert observation.violations == ()


def test_requirement_agent_repairs_a_draft_from_grounding_observation() -> None:
    requirement = (
        "保留主要人物穿深色服装的图片；"
        "主要人物指画面中可见面积最大的人物。"
    )
    constraint = {
        "id": "constraint_subject_clothing",
        "source_text": "保留主要人物穿深色服装的图片",
        "scope": "asset",
        "field": "image.main_subject.clothing_color",
        "operator": "eq",
        "value": "dark",
        "unit": "category",
        "required_evidence_type": "subject_clothing_color_classification",
    }
    invalid_draft = {
        "objective": "保留主要人物穿深色服装的图片",
        "constraints": [constraint],
        "clause_traces": [
            {
                "source_text": "保留主要人物穿深色服装的图片",
                "role": "constraint",
                "constraint_refs": ["constraint_subject_clothing"],
            }
        ],
    }
    valid_draft = {
        **invalid_draft,
        "clause_traces": [
            *invalid_draft["clause_traces"],
            {
                "source_text": "主要人物指画面中可见面积最大的人物",
                "role": "definition",
                "constraint_refs": ["constraint_subject_clothing"],
                "normalized_effect": {
                    "subject_selector": "largest_visible_person_by_area"
                },
            },
        ],
    }
    planner = RequirementLoopPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Validate the first grounded draft.",
                tool_name="validate_requirement_draft",
                tool_input=invalid_draft,
            ),
            AgentDecision(
                action="tool",
                reason_summary="Repair the missing definition trace.",
                tool_name="validate_requirement_draft",
                tool_input=valid_draft,
            ),
            AgentDecision(
                action="finish",
                reason_summary="The complete draft is grounded.",
                output={"use_validated_draft": True},
            ),
        ]
    )

    result = build_requirement_graph(agent_planner=planner).invoke(
        {
            "work_order_id": "work_1",
            "owner_id": "owner_1",
            "requirement": requirement,
            "data_sources": [
                {"type": "local_directory", "uri": "D:/data/images"}
            ],
            "trace": [],
        }
    )

    observations = result["agent_observations"][0]["tool_observations"]
    assert observations[0]["data"]["ok"] is False
    assert observations[0]["data"]["violations"][0]["code"] == (
        "UNGROUNDED_REQUIREMENT_CLAUSE"
    )
    assert observations[1]["data"]["ok"] is True
    assert result["task_spec"]["clause_traces"][1]["role"] == "definition"


@pytest.mark.parametrize(
    ("requirement", "constraint", "definition_traces"),
    [
        (
            (
                "排除静音超过5秒的音频；"
                "静音指连续500毫秒内RMS低于0.01。"
            ),
            ConstraintContract(
                id="constraint_silence_duration",
                source_text="排除静音超过5秒的音频",
                scope="asset",
                field="audio.silence_duration",
                operator="lte",
                value=5,
                unit="second",
                required_evidence_type="audio_silence_measurement",
            ),
            (
                RequirementClauseTrace(
                    source_text="静音指连续500毫秒内RMS低于0.01",
                    role="definition",
                    constraint_refs=("constraint_silence_duration",),
                    normalized_effect={
                        "window_ms": 500,
                        "rms_threshold": 0.01,
                    },
                ),
            ),
        ),
        (
            (
                "过滤包含敏感信息的文本；"
                "敏感信息包括身份证号和银行卡号；"
                "脱敏后的内容允许保留。"
            ),
            ConstraintContract(
                id="constraint_sensitive_information",
                source_text="过滤包含敏感信息的文本",
                scope="asset",
                field="document.sensitive_information_present",
                operator="eq",
                value=False,
                unit="boolean",
                required_evidence_type="sensitive_information_detection",
            ),
            (
                RequirementClauseTrace(
                    source_text="敏感信息包括身份证号和银行卡号",
                    role="definition",
                    constraint_refs=("constraint_sensitive_information",),
                    normalized_effect={
                        "included_types": ["identity_number", "bank_card_number"]
                    },
                ),
                RequirementClauseTrace(
                    source_text="脱敏后的内容允许保留",
                    role="definition",
                    constraint_refs=("constraint_sensitive_information",),
                    normalized_effect={"redacted_content_policy": "allow"},
                ),
            ),
        ),
    ],
)
def test_grounding_generalizes_definitions_across_modalities(
    requirement,
    constraint,
    definition_traces,
) -> None:
    draft = RequirementDraft(
        objective=requirement,
        constraints=(constraint,),
        clause_traces=(
            RequirementClauseTrace(
                source_text=constraint.source_text,
                role="constraint",
                constraint_refs=(constraint.id,),
            ),
            *definition_traces,
        ),
    )

    observation = validate_requirement_draft_grounding(requirement, draft)

    assert observation.ok is True


def test_grounding_rejects_a_definition_referencing_an_unknown_constraint() -> None:
    requirement = (
        "保留短文本；短文本指字符数不超过100。"
    )
    draft = RequirementDraft(
        objective="保留短文本",
        constraints=(
            ConstraintContract(
                id="constraint_text_length",
                source_text="保留短文本",
                scope="asset",
                field="document.character_count",
                operator="lte",
                value=100,
                unit="character",
                required_evidence_type="document_length",
            ),
        ),
        clause_traces=(
            RequirementClauseTrace(
                source_text="保留短文本",
                role="constraint",
                constraint_refs=("constraint_text_length",),
            ),
            RequirementClauseTrace(
                source_text="短文本指字符数不超过100",
                role="definition",
                constraint_refs=("constraint_missing",),
            ),
        ),
    )

    observation = validate_requirement_draft_grounding(requirement, draft)

    assert observation.ok is False
    assert {
        item.code for item in observation.violations
    } == {"UNKNOWN_CONSTRAINT_REFERENCE"}


def test_grounding_does_not_hide_a_clause_before_a_definition_prefix() -> None:
    requirement = (
        "去除相似文件。补充定义："
        "相似文件指内容指纹距离不超过2。"
    )
    draft = RequirementDraft(
        objective="去除相似文件",
        constraints=(
            ConstraintContract(
                id="constraint_similarity",
                source_text="相似文件指内容指纹距离不超过2",
                scope="dataset",
                field="document.fingerprint_distance",
                operator="lte",
                value=2,
                unit="distance",
                required_evidence_type="content_fingerprint",
            ),
        ),
        clause_traces=(
            RequirementClauseTrace(
                source_text="相似文件指内容指纹距离不超过2",
                role="constraint",
                constraint_refs=("constraint_similarity",),
            ),
        ),
    )

    observation = validate_requirement_draft_grounding(requirement, draft)

    assert observation.ok is False
    assert any(
        item.code == "UNGROUNDED_REQUIREMENT_CLAUSE"
        and item.source_text == "去除相似文件"
        for item in observation.violations
    )


def test_requirement_agent_turns_a_semantic_gap_into_a_human_interrupt() -> None:
    planner = RequirementLoopPlanner(
        [
            AgentDecision(
                action="report_gap",
                reason_summary="The duplicate threshold needs user confirmation.",
                output={
                    "questions": [
                        "What similarity threshold defines a duplicate?"
                    ]
                },
            )
        ]
    )

    result = build_requirement_graph(agent_planner=planner).invoke(
        {
            "work_order_id": "work_1",
            "owner_id": "owner_1",
            "requirement": "Remove similar documents.",
            "data_sources": [
                {"type": "local_directory", "uri": "D:/data/documents"}
            ],
            "trace": [],
        }
    )

    request = result["__interrupt__"][0].value
    assert request["kind"] == "requirement_clarification"
    assert request["questions"] == [
        "What similarity threshold defines a duplicate?"
    ]


def test_requirement_agent_resumes_after_human_clarification() -> None:
    runtime = WorkOrderRuntime(
        include_datajuicer=False,
        agent_planner=ClarificationWorkOrderPlanner(),
    )
    started = runtime.start(
        owner_id="owner_1",
        requirement="Remove similar documents.",
        data_sources=[
            {"type": "local_directory", "uri": "D:/data/documents"}
        ],
    )

    assert started["interrupts"][0]["value"]["kind"] == (
        "requirement_clarification"
    )

    resumed = runtime.resume(
        work_order_id=started["work_order_id"],
        owner_id="owner_1",
        decision={"answer": "Use threshold 0.8."},
    )

    assert resumed["interrupts"][0]["value"]["kind"] == (
        "task_spec_confirmation"
    )
    assert resumed["state"]["task_spec"]["constraints"][0]["value"] == 0.8


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
    runtime = WorkOrderRuntime(
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
