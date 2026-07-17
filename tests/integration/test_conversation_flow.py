from __future__ import annotations

import pytest

from dataagent.application.agent_runtime import AgentRuntime
from dataagent.application.conversation import ConversationService
from dataagent.config import Settings


class FakeConversationGateway:
    configured = True

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def conversation_turn(self, *, history, context):
        assert history[-1]["role"] == "user"
        return self.decisions.pop(0), None


def _settings(tmp_path) -> Settings:
    return Settings(
        api_key="test-key",
        base_url="https://example.invalid",
        planning_model="conversation-test-model",
        vision_model="vision-test-model",
        home=tmp_path,
        owner="user_1",
        env_path=None,
    )


def test_conversation_safety_gate_keeps_usage_question_out_of_work_orders(tmp_path) -> None:
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway(
            [
                {
                    "intent": "START_WORK_ORDER",
                    "reply": "我来说明使用方法。",
                    "requirement": "教我怎么使用",
                }
            ]
        ),
    )
    conversation = service.create("user_1")

    response = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="教我怎么使用"
    )

    assert response["work_order_id"] is None
    assert response["turn"] is None
    assert response["reply"] == "我来说明使用方法。"
    assert [item["role"] for item in response["messages"]] == ["user", "assistant"]
    with pytest.raises(PermissionError):
        service.get(conversation["id"], "user_2")


def test_natural_conversation_creates_approves_and_submits_work_order(tmp_path) -> None:
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            {
                "intent": "START_WORK_ORDER",
                "reply": "我先记录需求。",
                "requirement": "筛选清晰的人像图片并去重",
            },
            {
                "intent": "PROVIDE_SOURCE",
                "reply": "我来检查目录。",
                "source": str(source),
            },
            {"intent": "APPROVE", "reply": "确认 TaskSpec。"},
            {
                "intent": "APPROVE",
                "reply": "选择质量优先。",
                "strategy": "quality_first",
            },
            {"intent": "SUBMIT_RUN", "reply": "开始运行。"},
        ]
    )
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=gateway,
    )
    conversation = service.create("user_1")

    first = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="我要筛选清晰的人像图片并去重",
    )
    assert first["work_order_id"] is None
    assert "哪个本地目录" in first["reply"]

    created = service.send(
        thread_id=conversation["id"], owner_id="user_1", content=str(source)
    )
    assert created["work_order_id"]
    assert created["turn"]["interrupts"][0]["value"]["kind"] == "task_spec_confirmation"

    candidates = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="确认"
    )
    assert candidates["turn"]["interrupts"][0]["value"]["kind"] == "pipeline_approval"

    ready = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="质量优先"
    )
    assert ready["turn"]["interrupts"] == []
    assert ready["turn"]["state"]["approved_pipeline"]["strategy"] == "quality_first"

    submitted = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="开始运行"
    )
    assert submitted["run"]["status"] == "QUEUED"
    assert submitted["run"]["work_order_id"] == created["work_order_id"]
