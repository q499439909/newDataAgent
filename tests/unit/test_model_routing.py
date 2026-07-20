from __future__ import annotations

from pathlib import Path

from dataagent.config import Settings
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


def test_gateway_uses_fast_conversation_and_reasoning_healthcheck(monkeypatch) -> None:
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
    gateway.conversation_turn(history=[], context={})

    assert called_models == ["glm-5.2", "glm-5.2"]


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
