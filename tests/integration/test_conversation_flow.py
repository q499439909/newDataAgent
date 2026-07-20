from __future__ import annotations

import pytest
from time import perf_counter

from dataagent.application.agent_runtime import AgentRuntime
from dataagent.application.conversation import ConversationService
from dataagent.config import Settings


class FakeConversationGateway:
    configured = True

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = 0

    def conversation_turn(self, *, history, context):
        self.calls += 1
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


def test_common_system_questions_use_fast_path_without_model_call(tmp_path) -> None:
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            {
                "intent": "START_WORK_ORDER",
                "reply": "incorrect model response",
                "requirement": "教我怎么使用",
            }
        ]
    )
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=gateway,
    )
    conversation = service.create("user_1")

    responses = []
    for content in ("教我怎么使用", "how to use", "what model"):
        started = perf_counter()
        response = service.send(
            thread_id=conversation["id"], owner_id="user_1", content=content
        )
        responses.append((response, perf_counter() - started))
    response = responses[0][0]

    assert response["work_order_id"] is None
    assert response["turn"] is None
    assert "不需要记命令" in response["reply"]
    assert gateway.calls == 0
    assert all(item_elapsed < 1.0 for _, item_elapsed in responses)
    assert len(responses[-1][0]["messages"]) == 6
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


def test_quoted_path_and_requirement_in_one_message_create_work_order(tmp_path) -> None:
    source = tmp_path / "cats_dogs_mixed" / "images"
    source.mkdir(parents=True)
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway([])
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=gateway,
    )
    conversation = service.create("user_1")

    response = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=(
            f'"{source}"去掉里面不真实、不清晰的图片，把猫和狗的图片分开'
        ),
    )

    assert response["work_order_id"] is not None
    assert response["turn"]["interrupts"][0]["value"]["kind"] == (
        "task_spec_confirmation"
    )
    assert response["turn"]["state"]["task_spec"]["objective"] == (
        "去掉里面不真实、不清晰的图片，把猫和狗的图片分开"
    )
    assert response["turn"]["state"]["task_spec"]["data_sources"][0]["uri"] == str(
        source.resolve()
    )
    assert gateway.calls == 0


def test_quoted_standalone_path_continues_pending_requirement_without_model(tmp_path) -> None:
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            {
                "intent": "START_WORK_ORDER",
                "reply": "请提供目录。",
                "requirement": "筛选清晰图片并去重",
            }
        ]
    )
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=gateway,
    )
    conversation = service.create("user_1")
    service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="帮我筛选清晰图片并去重",
    )

    response = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"',
    )

    assert response["work_order_id"] is not None
    assert response["turn"]["interrupts"][0]["value"]["kind"] == (
        "task_spec_confirmation"
    )
    assert gateway.calls == 1


def test_blocked_operator_candidates_are_explained_after_confirmation() -> None:
    reply = ConversationService._turn_reply(
        {
            "interrupts": [],
            "state": {
                "next_action": "expand_retrieval",
                "operator_candidates": [
                    {
                        "provider_operator_ref": "image_tagging_mapper",
                        "executable": False,
                        "blocked_reason": "Runtime backend cuda is not available",
                    }
                ],
            },
        },
        approved=True,
    )

    assert "image_tagging_mapper" in reply
    assert "cuda" in reply


def test_numeric_resolution_choices_bind_to_pending_interrupt() -> None:
    context = {"agent_state": {"waiting": "capability_resolution"}}

    retry = ConversationService._pending_resolution_decision("1", context)
    remote = ConversationService._pending_resolution_decision("2", context)
    revise = ConversationService._pending_resolution_decision("3", context)
    terminate = ConversationService._pending_resolution_decision("4", context)

    assert retry is not None and retry.action == "retry"
    assert remote is not None and remote.runtime_backend == "remote"
    assert revise is not None and revise.action == "revise_task"
    assert terminate is not None and terminate.action == "terminate"


def test_pipeline_operator_question_uses_grounded_pipeline_context() -> None:
    pipelines = [
        {
            "id": "pipeline_balanced",
            "strategy": "balanced",
            "version": 1,
            "nodes": [
                {
                    "id": "quality_filter",
                    "operator_version_id": "builtin.quality_filter:1",
                    "runtime_backend": "cpu",
                    "parameters": {"confidence_threshold": 0.55},
                },
                {
                    "id": "image_classification",
                    "operator_version_id": (
                        "datajuicer.image_tagging_vlm_mapper.remote_api:1"
                    ),
                    "runtime_backend": "remote",
                    "parameters": {"tag_field_name": "image_tags"},
                },
            ],
        }
    ]

    decision = ConversationService._pipeline_details_decision(
        "用了什么算子", {"pipeline_choices": pipelines}
    )

    assert decision is not None
    assert "builtin.quality_filter:1" in decision.reply
    assert "datajuicer.image_tagging_vlm_mapper.remote_api:1" in decision.reply
    assert "data_loader" not in decision.reply
