from __future__ import annotations

import json
from pathlib import Path

from dataagent.config import Settings
from dataagent.agents.runner import AgentMessage, AgentPlanningRequest
from dataagent.gateway import ModelGateway
from dataagent.model_routing import ModelRoutingPolicy, ModelTaskKind
from dataagent.models import ModelResult


def _policy() -> ModelRoutingPolicy:
    return ModelRoutingPolicy(
        fast_text_model="glm-5.2",
        reasoning_model="glm-5.2",
        vision_model="qwen3.7-plus",
        image_generation_model="wan2.7-image",
        image_generation_pro_model="wan2.7-image-pro",
        text_image_model="qwen-image-2.0-pro",
    )


def test_text_and_vision_tasks_route_by_capability_not_agent_name() -> None:
    policy = _policy()

    assert policy.route(ModelTaskKind.CONVERSATION).model_id == "glm-5.2"
    assert policy.route(ModelTaskKind.INTENT_CLASSIFICATION).model_id == "glm-5.2"
    assert policy.route(ModelTaskKind.REQUIREMENT_PLANNING).model_id == "glm-5.2"
    assert policy.route(ModelTaskKind.PROCESSING_PLANNING).model_id == "glm-5.2"
    assert policy.route(ModelTaskKind.STRATEGY_PLANNING).model_id == "glm-5.2"
    assert policy.route(ModelTaskKind.CODE_GENERATION).model_id == "glm-5.2"
    assert policy.route(ModelTaskKind.VISION_EVALUATION).model_id == "qwen3.7-plus"


def test_image_generation_uses_operator_routes() -> None:
    policy = _policy()

    assert policy.route(ModelTaskKind.IMAGE_GENERATION).model_id == "wan2.7-image"
    assert (
        policy.route(ModelTaskKind.IMAGE_GENERATION_PRO).model_id
        == "wan2.7-image-pro"
    )
    assert (
        policy.route(ModelTaskKind.TEXT_IMAGE_GENERATION).model_id
        == "qwen-image-2.0-pro"
    )


def test_gateway_uses_planning_model_for_healthcheck(monkeypatch) -> None:
    settings = Settings(
        api_key="test-key",
        base_url="https://example.invalid",
        planning_model="glm-5.2",
        vision_model="qwen3.7-plus",
        home=Path(".dataagent"),
        owner="test-user",
        env_path=None,
        fast_text_model="glm-5.2",
    )
    gateway = ModelGateway(settings)
    called_models: list[str] = []

    def fake_messages(model, system, content, max_tokens=2048):
        del system, content, max_tokens
        called_models.append(model)
        return ModelResult(
            text='{"intent":"CHAT","reply":"ok"}',
            model=model,
        )

    monkeypatch.setattr(gateway, "_messages", fake_messages)

    gateway.healthcheck()

    assert called_models == ["glm-5.2"]


def test_agent_planning_payload_includes_conversation_messages(
    monkeypatch,
) -> None:
    gateway = ModelGateway(
        Settings(
            api_key="test-key",
            base_url="https://example.invalid",
            planning_model="glm-5.2",
            vision_model="qwen3.7-plus",
            home=Path(".dataagent"),
            owner="test-user",
            env_path=None,
        )
    )
    captured: dict = {}

    def fake_messages(model, system, content, max_tokens=2048):
        del system, max_tokens
        captured.update(json.loads(content))
        return ModelResult(
            text=json.dumps(
                {
                    "action": "finish",
                    "reason_summary": "The request was answered.",
                    "output": {"reply": "Understood."},
                }
            ),
            model=model,
        )

    monkeypatch.setattr(gateway, "_messages", fake_messages)
    gateway.plan_agent_decision(
        AgentPlanningRequest(
            agent_name="requirement",
            goal="Handle the latest user message.",
            iteration=1,
            context={"session_id": "session_1"},
            tools=(),
            messages=(
                AgentMessage(
                    role="user",
                    content="Process the declared source under constraint A.",
                ),
                AgentMessage(
                    role="assistant",
                    content="Please clarify the acceptance threshold.",
                ),
                AgentMessage(
                    role="user",
                    content="Use threshold 0.8.",
                ),
            ),
        )
    )

    assert captured["messages"] == [
        {
            "role": "user",
            "content": "Process the declared source under constraint A.",
            "metadata": {},
        },
        {
            "role": "assistant",
            "content": "Please clarify the acceptance threshold.",
            "metadata": {},
        },
        {
            "role": "user",
            "content": "Use threshold 0.8.",
            "metadata": {},
        },
    ]


def test_requirement_planning_repairs_constraints_not_grounded_in_user_text(
    monkeypatch,
) -> None:
    settings = Settings(
        api_key="test-key",
        base_url="https://example.invalid",
        planning_model="glm-5.2",
        vision_model="qwen3.7-plus",
        home=Path(".dataagent"),
        owner="test-user",
        env_path=None,
    )
    gateway = ModelGateway(settings)
    responses = iter(
        (
            {
                "objective": "错误地改写成婴幼儿筛选",
                "constraints": [
                    {
                        "id": "constraint_face_age",
                        "source_text": "年龄范围 0.3 到 3.5 岁",
                        "scope": "asset",
                        "field": "image.face_age",
                        "operator": "lte",
                        "value": 3.5,
                        "unit": "year",
                        "required_evidence_type": "face_age_estimation",
                    }
                ],
            },
                {
                    "objective": "筛选宽高比合格的图片",
                    "constraints": [
                    {
                        "id": "constraint_aspect_ratio",
                        "source_text": "宽高比在 0.3 到 3.5 之间",
                        "scope": "asset",
                        "field": "image.aspect_ratio",
                        "operator": "gte",
                        "value": 0.3,
                        "unit": "ratio",
                            "required_evidence_type": "image_aspect_ratio",
                        }
                    ],
                    "clause_traces": [
                        {
                            "source_text": "宽高比在 0.3 到 3.5 之间",
                            "role": "constraint",
                            "constraint_refs": ["constraint_aspect_ratio"],
                        }
                    ],
                },
        )
    )
    calls = []

    def fake_messages(model, system, content, max_tokens=2048):
        del system, max_tokens
        calls.append(content)
        return ModelResult(text=json.dumps(next(responses)), model=model)

    monkeypatch.setattr(gateway, "_messages", fake_messages)

    payload = gateway.plan_requirement_draft(
        requirement="宽高比在 0.3 到 3.5 之间",
        data_sources=({"type": "local_directory", "uri": "D:/data"},),
    )

    assert payload["constraints"][0]["field"] == "image.aspect_ratio"
    assert len(calls) == 2
    assert "not an exact span" in calls[1]


def test_requirement_planning_repairs_an_omitted_requirement_clause(
    monkeypatch,
) -> None:
    settings = Settings(
        api_key="test-key",
        base_url="https://example.invalid",
        planning_model="glm-5.2",
        vision_model="qwen3.7-plus",
        home=Path(".dataagent"),
        owner="test-user",
        env_path=None,
    )
    gateway = ModelGateway(settings)
    base_constraint = {
        "id": "constraint_face_count",
        "source_text": "人脸数量在2个及以下",
        "scope": "asset",
        "field": "image.face_count",
        "operator": "lte",
        "value": 2,
        "unit": "count",
        "required_evidence_type": "detected_face_count",
    }
    responses = iter(
        (
            {
                "objective": "筛选图片",
                "constraints": [base_constraint],
                "clause_traces": [
                    {
                        "source_text": "人脸数量在2个及以下",
                        "role": "constraint",
                        "constraint_refs": ["constraint_face_count"],
                    }
                ],
            },
            {
                "objective": "筛选并去重图片",
                "constraints": [
                    base_constraint,
                    {
                        "id": "constraint_duplicate",
                        "source_text": "去除重复图片",
                        "scope": "dataset",
                        "field": "dataset.duplicate_count",
                        "operator": "eq",
                        "value": 0,
                        "unit": "count",
                        "required_evidence_type": "duplicate_group",
                    },
                    ],
                    "clause_traces": [
                        {
                            "source_text": "人脸数量在2个及以下",
                            "role": "constraint",
                            "constraint_refs": ["constraint_face_count"],
                        },
                        {
                            "source_text": "去除重复图片",
                            "role": "constraint",
                            "constraint_refs": ["constraint_duplicate"],
                        },
                    ],
                },
        )
    )
    calls = []

    def fake_messages(model, system, content, max_tokens=2048):
        del system, max_tokens
        calls.append(content)
        return ModelResult(text=json.dumps(next(responses)), model=model)

    monkeypatch.setattr(gateway, "_messages", fake_messages)

    payload = gateway.plan_requirement_draft(
        requirement="人脸数量在2个及以下；去除重复图片",
        data_sources=({"type": "local_directory", "uri": "D:/data"},),
    )

    assert len(payload["constraints"]) == 2
    assert "Requirement clause has no ClauseTrace" in calls[1]
    assert "source_text" in calls[1]


def test_requirement_planning_accepts_a_grounded_constraint_definition(
    monkeypatch,
) -> None:
    settings = Settings(
        api_key="test-key",
        base_url="https://example.invalid",
        planning_model="glm-5.2",
        vision_model="qwen3.7-plus",
        home=Path(".dataagent"),
        owner="test-user",
        env_path=None,
    )
    gateway = ModelGateway(settings)
    response = {
        "objective": "保留主要人物穿深色服装的图片",
        "constraints": [
            {
                "id": "constraint_subject_clothing",
                "source_text": "保留主要人物穿深色服装的图片",
                "scope": "asset",
                "field": "image.main_subject.clothing_color",
                "operator": "eq",
                "value": "dark",
                "unit": "category",
                "required_evidence_type": (
                    "subject_clothing_color_classification"
                ),
            }
        ],
        "clause_traces": [
            {
                "source_text": "保留主要人物穿深色服装的图片",
                "role": "constraint",
                "constraint_refs": ["constraint_subject_clothing"],
            },
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

    monkeypatch.setattr(
        gateway,
        "_messages",
        lambda model, system, content, max_tokens=2048: ModelResult(
            text=json.dumps(response),
            model=model,
        ),
    )

    payload = gateway.plan_requirement_draft(
        requirement=(
            "保留主要人物穿深色服装的图片；"
            "主要人物指画面中可见面积最大的人物"
        ),
        data_sources=({"type": "local_directory", "uri": "D:/data"},),
    )

    assert payload["clause_traces"][1]["role"] == "definition"


def test_requirement_planning_assigns_constraint_ids_and_trace_refs(
    monkeypatch,
) -> None:
    gateway = ModelGateway(
        Settings(
            api_key="test-key",
            base_url="https://example.invalid",
            planning_model="glm-5.2",
            vision_model="qwen3.7-plus",
            home=Path(".dataagent"),
            owner="test-user",
            env_path=None,
        )
    )
    response = {
        "objective": "Classify records and preserve their original files.",
        "constraints": [
            {
                "source_text": "Classify every record",
                "source_clause_id": "clause_001",
                "scope": "asset",
                "field": "record.category",
                "operator": "eq",
                "value": "assigned_label",
                "unit": "category",
                "required_evidence_type": "classification",
            },
            {
                "source_text": "preserve the original files",
                "source_clause_id": "clause_002",
                "scope": "dataset",
                "field": "output.content_format",
                "operator": "eq",
                "value": "original_file",
                "unit": "format",
                "required_evidence_type": "output_format",
            },
        ],
        "clause_traces": [
            {
                "source_text": "Classify every record",
                "source_clause_id": "clause_001",
                "role": "constraint",
            },
            {
                "source_text": "preserve the original files",
                "source_clause_id": "clause_002",
                "role": "constraint",
            },
        ],
    }
    monkeypatch.setattr(
        gateway,
        "_messages",
        lambda model, system, content, max_tokens=2048: ModelResult(
            text=json.dumps(response),
            model=model,
        ),
    )

    payload = gateway.plan_requirement_draft(
        requirement="Classify every record; preserve the original files.",
        data_sources=({"type": "local_directory", "uri": "D:/records"},),
    )

    assert [item["id"] for item in payload["constraints"]] == ["C01", "C02"]
    assert payload["clause_traces"][0]["constraint_refs"] == ["C01"]
    assert payload["clause_traces"][1]["constraint_refs"] == ["C02"]


def test_gap_resolution_returns_a_canonical_revised_draft(
    monkeypatch,
) -> None:
    gateway = ModelGateway(
        Settings(
            api_key="test-key",
            base_url="https://example.invalid",
            planning_model="glm-5.2",
            vision_model="qwen3.7-plus",
            home=Path(".dataagent"),
            owner="test-user",
            env_path=None,
        )
    )
    response = {
        "reply": "The labels are now defined.",
        "requirement_relevant": True,
        "resolutions": [
            {
                "gap_id": "gap_labels",
                "resolved": True,
                "normalized_answer": "alpha and beta",
            }
        ],
        "revised_draft": {
            "objective": "Classify records as alpha or beta.",
            "constraints": [
                {
                    "source_text": "Use labels alpha and beta",
                    "source_clause_id": "clause_002",
                    "scope": "asset",
                    "field": "record.category",
                    "operator": "eq",
                    "value": "alpha_or_beta",
                    "unit": "category",
                    "required_evidence_type": "classification",
                },
                {
                    "source_text": "Classify records",
                    "source_clause_id": "clause_001",
                    "scope": "asset",
                    "field": "record.category",
                    "operator": "eq",
                    "value": "assigned_label",
                    "unit": "category",
                    "required_evidence_type": "classification",
                }
            ],
            "clause_traces": [
                {
                    "source_text": "Classify records",
                    "source_clause_id": "clause_001",
                    "role": "context",
                },
                {
                    "source_text": "Use labels alpha and beta",
                    "source_clause_id": "clause_002",
                    "role": "constraint",
                },
            ],
            "gaps": [],
        },
    }
    monkeypatch.setattr(
        gateway,
        "_messages",
        lambda model, system, content, max_tokens=2048, **kwargs: ModelResult(
            text=json.dumps(response),
            model=model,
        ),
    )

    payload = gateway.resolve_requirement_gaps(
        gaps=(
            {
                "id": "gap_labels",
                "question": "Which labels should be used?",
                "answer_schema": {"type": "string"},
            },
        ),
        answer="Use labels alpha and beta.",
        messages=({"role": "user", "content": "Classify records."},),
        draft={
            "objective": "Classify records.",
            "constraints": [
                {
                    "id": "C01",
                    "source_text": "Classify records",
                    "source_clause_id": "clause_001",
                    "scope": "asset",
                    "field": "record.category",
                    "operator": "eq",
                    "value": "assigned_label",
                    "unit": "category",
                    "required_evidence_type": "classification",
                }
            ],
            "clause_traces": [
                {
                    "source_text": "Classify records",
                    "source_clause_id": "clause_001",
                    "role": "constraint",
                    "constraint_refs": ["C01"],
                }
            ],
            "gaps": [],
        },
        requirement="Classify records.\nUse labels alpha and beta.",
        data_sources=({"type": "local_directory", "uri": "D:/records"},),
    )

    assert [item["id"] for item in payload["revised_draft"]["constraints"]] == [
        "C02",
        "C01",
    ]
    direct_refs = {
        item["source_clause_id"]: item["constraint_refs"]
        for item in payload["revised_draft"]["clause_traces"]
        if item["role"] == "constraint"
    }
    assert direct_refs == {
        "clause_001": ["C01"],
        "clause_002": ["C02"],
    }


def test_gap_resolution_repairs_an_invalid_revised_draft_once(
    monkeypatch,
) -> None:
    gateway = ModelGateway(
        Settings(
            api_key="test-key",
            base_url="https://example.invalid",
            planning_model="glm-5.2",
            vision_model="qwen3.7-plus",
            home=Path(".dataagent"),
            owner="test-user",
            env_path=None,
        )
    )
    invalid = {
        "reply": "The strategy is understood.",
        "requirement_relevant": True,
        "resolutions": [
            {
                "gap_id": "gap_strategy",
                "resolved": True,
                "normalized_answer": "best match",
            }
        ],
        "revised_draft": {
            "objective": "Organize records.",
            "constraints": [
                {
                    "source_text": "Use best match",
                    "source_clause_id": "clause_002",
                    "scope": "asset",
                    "field": "record.category",
                    "operator": "best_match_in",
                    "value": "known_labels",
                    "unit": "category",
                    "required_evidence_type": "classification",
                }
            ],
            "data_sources": [],
        },
    }
    valid = {
        **invalid,
        "revised_draft": {
            "objective": "Organize records using best-match assignment.",
            "constraints": [],
            "clause_traces": [
                {
                    "source_text": "Organize records",
                    "source_clause_id": "clause_001",
                    "role": "context",
                },
                {
                    "source_text": "Use best match",
                    "source_clause_id": "clause_002",
                    "role": "preference",
                },
            ],
            "preferences": {"assignment_strategy": "best_match"},
            "gaps": [],
        },
    }
    responses = iter((invalid, valid))
    calls: list[dict] = []

    def fake_messages(model, system, content, max_tokens=2048, **kwargs):
        del system, max_tokens, kwargs
        calls.append(json.loads(content))
        return ModelResult(text=json.dumps(next(responses)), model=model)

    monkeypatch.setattr(gateway, "_messages", fake_messages)

    payload = gateway.resolve_requirement_gaps(
        gaps=(
            {
                "id": "gap_strategy",
                "question": "Which assignment strategy?",
                "answer_schema": {"type": "string"},
            },
        ),
        answer="Use best match.",
        messages=({"role": "user", "content": "Organize records."},),
        draft={"objective": "Organize records.", "gaps": []},
        requirement="Organize records.\nUse best match.",
        data_sources=({"type": "local_directory", "uri": "D:/records"},),
    )

    assert payload["revised_draft"]["preferences"] == {
        "assignment_strategy": "best_match"
    }
    assert len(calls) == 2
    assert "repair_observation" in calls[1]


def test_settings_default_fast_text_model_is_glm_5_2() -> None:
    settings = Settings(
        api_key=None,
        base_url="https://example.invalid",
        planning_model="glm-5.2",
        vision_model="qwen3.7-plus",
        home=Path(".dataagent"),
        owner="test-user",
        env_path=None,
    )

    assert settings.fast_text_model == "glm-5.2"
