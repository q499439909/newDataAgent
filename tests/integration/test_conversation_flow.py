from __future__ import annotations

import pytest
from time import perf_counter

from dataagent.application.agent_runtime import AgentRuntime
from dataagent.application.conversation import ConversationDecision, ConversationService
from dataagent.config import Settings
from dataagent.gateway import ModelGatewayError


class FakeConversationGateway:
    configured = True

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = 0

    def conversation_turn(self, *, history, context):
        self.calls += 1
        assert history[-1]["role"] == "user"
        return self.decisions.pop(0), None


class FailingConversationGateway:
    configured = True

    def __init__(self) -> None:
        self.calls = 0

    def conversation_turn(self, *, history, context):
        self.calls += 1
        raise ModelGatewayError("structured response unavailable")


class IneligiblePipelineRuntime:
    def state(self, *, work_order_id, owner_id):
        return {
            "work_order_id": work_order_id,
            "thread_id": "thread_1",
            "state": {"next_action": "approve_pipeline"},
            "interrupts": [
                {
                    "value": {
                        "kind": "pipeline_approval",
                        "pipelines": [
                            {
                                "id": "pipeline_quality",
                                "strategy": "quality_first",
                                "execution_eligibility": {
                                    "eligible": False,
                                    "violations": ["provider parameter schema is invalid"],
                                },
                            }
                        ],
                    }
                }
            ],
        }

    def resume(self, **kwargs):
        raise AssertionError("ineligible pipeline must not be resumed")


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
            {
                "intent": "APPROVE",
                "reply": "选择质量优先。",
                "strategy": "quality_first",
            },
            {"intent": "SUBMIT_RUN", "reply": "开始运行。"},
            {"intent": "SUBMIT_RUN", "reply": "重新开始运行。"},
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
    assert gateway.calls == 1

    assert runtime.run_store is not None
    runtime.run_store.mark_failed(submitted["run"]["id"], "test failure")
    switch_help = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="换个 Pipeline"
    )
    assert "不会直接复用旧 Run" in switch_help["reply"]

    switched = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="保留优先"
    )
    switched_pipeline = switched["turn"]["state"]["approved_pipeline"]
    assert switched_pipeline["strategy"] == "retention_first"
    assert switched_pipeline["id"] != submitted["run"]["pipeline_version_id"]
    assert switched["turn"]["state"]["next_action"] == "submit_dataset_run"

    retried = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="开始运行"
    )
    assert retried["run"]["id"] != submitted["run"]["id"]
    assert retried["run"]["pipeline_version_id"] == switched_pipeline["id"]

    runtime.run_store.mark_failed(retried["run"]["id"], "second test failure")
    revised = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="不筛选清晰度了",
    )
    revised_spec = revised["turn"]["state"]["task_spec"]
    assert revised["turn"]["interrupts"][0]["value"]["kind"] == (
        "task_spec_confirmation"
    )
    assert revised_spec["confirmed"] is False
    assert revised_spec["hard_constraints"]["disabled_capabilities"] == [
        "image_quality"
    ]
    assert "image_quality" not in {
        item["capability"] for item in revised_spec["capability_requirements"]
    }
    assert revised["turn"]["state"]["selected_pipeline_id"] == ""

    replanned = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="确认"
    )
    assert replanned["turn"]["interrupts"][0]["value"]["kind"] == (
        "pipeline_approval"
    )
    for pipeline in replanned["turn"]["state"]["representative_pipelines"]:
        assert "builtin.quality_filter:1" not in {
            node["operator_version_id"] for node in pipeline["nodes"]
        }


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
    assert "还需要你补充" in response["reply"]
    assert len(response["turn"]["state"]["task_spec"]["ambiguities"]) == 3


def test_unquoted_path_glued_to_requirement_uses_existing_directory_prefix(
    tmp_path,
) -> None:
    source = tmp_path / "mix"
    source.mkdir()
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
        content=f"{source}去掉不真实的图片，把猫和狗分开",
    )

    spec = response["turn"]["state"]["task_spec"]
    assert spec["data_sources"][0]["uri"] == str(source.resolve())
    assert spec["objective"] == "去掉不真实的图片，把猫和狗分开"
    assert gateway.calls == 0


def test_complex_task_requires_clarification_before_confirmation(tmp_path) -> None:
    source = tmp_path / "cats_dogs"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway([]),
    )
    conversation = service.create("user_1")
    created = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"去掉不真实、不是实拍直出的图片，把猫和狗分开',
    )

    assert created["turn"]["interrupts"][0]["value"]["kind"] == (
        "task_spec_confirmation"
    )
    assert "还需要你补充" in created["reply"]
    assert created["turn"]["state"]["task_spec"]["confirmed"] is False

    revised = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="按推荐默认值",
    )
    spec = revised["turn"]["state"]["task_spec"]
    assert spec["version"] == created["turn"]["state"]["task_spec"]["version"] + 1
    assert spec["ambiguities"] == []
    assert spec["hard_constraints"]["preserve_source"] is True
    assert spec["preferences"]["mixed_policy"] == "review"
    assert "重度滤镜" in spec["exclusion_requirements"][0]

    approved = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="确认"
    )
    assert approved["turn"]["state"]["task_spec"]["confirmed"] is True


def test_contextual_continue_accepts_remaining_defaults_and_confirms(tmp_path) -> None:
    source = tmp_path / "mix"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway([]),
    )
    conversation = service.create("user_1")
    created = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"去掉不真实、非实拍直出的、不清晰的图片，把猫和狗分开',
    )
    thread = runtime.conversation_store.get(conversation["id"], "user_1")
    partial = service._apply(
        thread=thread,
        owner_id="user_1",
        content="补充真实性和类别要求",
        decision=ConversationDecision(
            intent="EDIT_TASK_SPEC",
            task_spec_patch={
                "hard_constraints": {
                    "authenticity_scope": {"exclude_composite": True},
                    "preserve_source": None,
                },
                "preferences": {
                    "mixed_policy": "review",
                    "unknown_policy": "review",
                },
                "semantic_requirements": ["好", "继续"],
            },
        ),
    )
    assert partial["turn"]["state"]["task_spec"]["ambiguities"] == [
        "是否按默认安全方式复制到新的版本化分类目录，并保持源目录只读？"
    ]

    continued = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="继续"
    )
    spec = continued["turn"]["state"]["task_spec"]
    assert spec["version"] > created["turn"]["state"]["task_spec"]["version"]
    assert spec["confirmed"] is True
    assert spec["ambiguities"] == []
    assert spec["hard_constraints"]["preserve_source"] is True
    assert spec["preferences"]["output_layout"] == "versioned_class_directories"
    assert "好" not in spec["semantic_requirements"]
    assert "继续" not in spec["semantic_requirements"]
    assert continued["turn"]["state"]["next_action"] != "confirm_task_spec"
    assert "已采纳剩余推荐值" in continued["reply"]


def test_clarification_answer_is_parsed_without_model_and_normalized(tmp_path) -> None:
    source = tmp_path / "mix"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FailingConversationGateway()
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
        content=f'"{source}"去掉不真实、非实拍直出的图片，把猫和狗分开',
    )

    revised = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="排除插画、截图、明显合成。输出到猫狗都有的文件夹。好",
    )

    spec = revised["turn"]["state"]["task_spec"]
    assert gateway.calls == 0
    assert spec["hard_constraints"]["preserve_source"] is True
    assert spec["preferences"]["mixed_policy"] == "keep"
    assert spec["preferences"]["unknown_policy"] == "review"
    assert spec["preferences"]["output_layout"] == "versioned_class_directories"
    assert "排除插画" in spec["exclusion_requirements"]
    assert all(not item.startswith("{") for item in spec["exclusion_requirements"])
    assert spec["ambiguities"] == []
    assert spec["confirmed"] is False


def test_structured_requirement_patch_uses_description_instead_of_dict_repr(
    tmp_path,
) -> None:
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway([]),
    )
    conversation = service.create("user_1")
    created = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"去掉不真实图片',
    )
    thread = runtime.conversation_store.get(conversation["id"], "user_1")

    revised = service._apply(
        thread=thread,
        owner_id="user_1",
        content="排除插画和截图",
        decision=ConversationDecision(
            intent="EDIT_TASK_SPEC",
            task_spec_patch={
                "exclusion_requirements": [
                    {
                        "id": "exclude_non_authentic",
                        "description": "排除插画和截图",
                        "rules": ["排除插画", "排除截图"],
                    }
                ]
            },
        ),
    )

    requirements = revised["turn"]["state"]["task_spec"]["exclusion_requirements"]
    assert requirements == ["排除插画和截图"]
    assert created["work_order_id"] == revised["turn"]["state"]["work_order_id"]


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


def test_pipeline_approval_and_run_submission_are_bound_to_workflow_state() -> None:
    approval_context = {
        "agent_state": {
            "waiting": "pipeline_approval",
            "next_action": "approve_pipeline",
        }
    }

    selection = ConversationService._pending_pipeline_approval_decision(
        "运行保留优先的 pipeline", approval_context
    )
    premature_run = ConversationService._pending_pipeline_approval_decision(
        "运行啊", approval_context
    )
    submission = ConversationService._pending_run_submission_decision(
        "运行啊",
        {
            "agent_state": {
                "waiting": None,
                "next_action": "submit_dataset_run",
            }
        },
    )

    assert selection is not None
    assert selection.intent == "APPROVE"
    assert selection.strategy == "retention_first"
    assert premature_run is not None
    assert premature_run.intent == "CHAT"
    assert "先选择" in premature_run.reply
    assert submission is not None
    assert submission.intent == "SUBMIT_RUN"


def test_task_spec_supplement_is_revised_before_explicit_approval(tmp_path) -> None:
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            {
                "intent": "START_WORK_ORDER",
                "reply": "Please provide the image directory.",
                "requirement": "筛选清晰的猫狗图片",
            },
            {
                # Guard against the observed model mistake: supplemental text is not approval.
                "intent": "APPROVE",
                "reply": "TaskSpec approved.",
                "task_spec_patch": {
                    "exclusion_requirements": [
                        "Exclude AI-generated or obviously composited images"
                    ],
                    "preferences": {"output_layout": "separate_classes"},
                },
            },
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
        content="筛选清晰的猫狗图片",
    )
    created = service.send(
        thread_id=conversation["id"], owner_id="user_1", content=str(source)
    )
    original_version = created["turn"]["state"]["task_spec"]["version"]

    details = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="TaskSpec show details"
    )
    assert "筛选清晰的猫狗图片" in details["reply"]
    assert gateway.calls == 1

    revised = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="Exclude generated images and write separate class folders",
    )
    revised_spec = revised["turn"]["state"]["task_spec"]
    assert revised["turn"]["interrupts"][0]["value"]["kind"] == (
        "task_spec_confirmation"
    )
    assert revised_spec["version"] == original_version + 1
    assert revised_spec["confirmed"] is False
    assert revised_spec["exclusion_requirements"] == [
        "Exclude AI-generated or obviously composited images"
    ]
    assert revised_spec["preferences"]["output_layout"] == "separate_classes"

    approved = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="确认"
    )
    assert approved["turn"]["state"]["task_spec"]["confirmed"] is True
    assert approved["turn"]["interrupts"][0]["value"]["kind"] == (
        "capability_resolution"
    )
    assert gateway.calls == 2


def test_model_failure_is_visible_non_mutating_and_auditable(tmp_path) -> None:
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FailingConversationGateway()
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
        content="Explain the current operator selection",
    )

    assert gateway.calls == 1
    assert "模型服务本轮未返回有效结果" in response["reply"]
    assert "没有执行任何操作" in response["reply"]
    assert response["work_order_id"] is None
    assert response["diagnostics"]["fallback_used"] is True
    assert "ModelGatewayError" in response["diagnostics"]["reason"]
    assert response["messages"][-1]["model"] == "local-fallback"
    stored = runtime.conversation_store.get(conversation["id"], "user_1")
    assert stored["context"]["conversation_runtime"]["fallback_count"] == 1
    assert "ModelGatewayError" in stored["context"]["conversation_runtime"][
        "last_fallback_reason"
    ]


def test_ineligible_pipeline_selection_does_not_redraw_approval_tables(tmp_path) -> None:
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=IneligiblePipelineRuntime(),
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway([]),
    )

    response = service._apply(
        thread={
            "id": "conversation_1",
            "owner_id": "user_1",
            "work_order_id": "work_order_1",
            "context": {},
        },
        owner_id="user_1",
        content="质量优先",
        decision=ConversationDecision(
            intent="APPROVE", strategy="quality_first", reply="选择质量优先"
        ),
    )

    assert response["turn"] is None
    assert "仍停留在方案选择阶段" in response["reply"]
    assert "provider parameter schema is invalid" in response["reply"]
