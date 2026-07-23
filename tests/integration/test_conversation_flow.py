from __future__ import annotations

import json
import pytest

from dataagent.application.agent_runtime import AgentRuntime
from dataagent.application.conversation import ConversationService
from dataagent.application.conversation_actions import parse_conversation_action
from dataagent.config import Settings
from dataagent.gateway import ModelGatewayError


class FakeConversationGateway:
    """Replay a scripted sequence of model decisions, one per call.

    In the model-first design every user turn calls the gateway (possibly
    more than once when the ReAct loop feeds a failure back), so tests must
    supply a decision for each expected call.
    """

    configured = True

    def __init__(self, decisions, clarification_responses=None):
        self.decisions = list(decisions)
        self.clarification_responses = list(clarification_responses or [])
        self.calls = 0
        self.clarification_calls = 0

    def conversation_turn(self, *, history, context):
        self.calls += 1
        assert history[-1]["role"] in {"user", "control"}
        if not self.decisions:
            raise AssertionError("FakeConversationGateway ran out of scripted decisions")
        return self.decisions.pop(0), None

    def task_clarifications(self, *, task_spec):
        self.clarification_calls += 1
        if self.clarification_responses:
            return self.clarification_responses.pop(0), None
        return {
            "summary": "已记录当前需求。",
            "questions": [
                {
                    "field": field,
                    "question": f"请补充 {field}。",
                }
                for field in task_spec.get("ambiguities", [])
            ],
        }, None


class FailingConversationGateway:
    configured = True

    def __init__(self) -> None:
        self.calls = 0

    def conversation_turn(self, *, history, context):
        self.calls += 1
        raise ModelGatewayError("structured response unavailable")


def _decision(**payload):
    return parse_conversation_action(payload)


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


class EligiblePipelineRuntime:
    """A work order paused at pipeline_approval with an eligible pipeline."""

    def __init__(self, pipeline):
        self._pipeline = pipeline
        self.resumed = None

    def state(self, *, work_order_id, owner_id):
        return {
            "work_order_id": work_order_id,
            "thread_id": "thread_1",
            "state": {"next_action": "approve_pipeline"},
            "interrupts": [
                {
                    "value": {
                        "kind": "pipeline_approval",
                        "pipelines": [self._pipeline],
                    }
                }
            ],
        }

    def resume(self, *, work_order_id, owner_id, decision):
        self.resumed = decision
        return {
            "work_order_id": work_order_id,
            "thread_id": "thread_1",
            "state": {"next_action": "submit_dataset_run", "approved_pipeline": self._pipeline},
            "interrupts": [],
        }


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


def _start_work_order(source, requirement, reply="我来检查目录并创建图片数据任务。"):
    return {
        "intent": "START_WORK_ORDER",
        "source": str(source),
        "requirement": requirement,
        "reply": reply,
    }


def test_common_system_questions_get_model_replies(tmp_path) -> None:
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            {"intent": "CHAT", "reply": "直接描述你的图片数据目标即可，不需要记命令。"},
            {"intent": "CHAT", "reply": "Just describe your image data goal."},
            {"intent": "CHAT", "reply": "普通对话由 glm-5.2 处理。"},
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
    for content in ("教我怎么使用", "how to use", "什么模型"):
        response = service.send(
            thread_id=conversation["id"], owner_id="user_1", content=content
        )
        responses.append(response)
    response = responses[0]

    assert response["work_order_id"] is None
    assert response["turn"] is None
    assert "不需要记命令" in response["reply"]
    assert gateway.calls == 3
    assert len(responses[-1]["messages"]) == 6
    with pytest.raises(PermissionError):
        service.get(conversation["id"], "user_2")


def test_natural_conversation_creates_approves_and_submits_work_order(tmp_path) -> None:
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            {"intent": "START_WORK_ORDER", "requirement": "筛选清晰的人像图片并去重", "reply": "我先记录需求。"},
            {"intent": "PROVIDE_SOURCE", "source": str(source), "reply": "我来检查这个目录。"},
            {"intent": "EDIT_TASK_SPEC", "action": "accept_defaults", "confirm_after_edit": True, "reply": "按推荐默认值确认。"},
            {"intent": "SELECT_PIPELINE", "strategy": "quality_first", "reply": "选择质量优先。"},
            {"intent": "SUBMIT_RUN", "reply": "开始运行。"},
            {"intent": "QUERY_CONTROL_FACTS", "facets": ["pipeline"], "reply": ""},
            {"intent": "QUERY_CONTROL_FACTS", "facets": ["pipeline"], "reply": ""},
            {"intent": "QUERY_CONTROL_FACTS", "facets": ["run"], "reply": ""},
            {"intent": "RERUN_PIPELINE", "reply": "按原 Pipeline 重新运行。"},
            {"intent": "CHAT", "reply": "可以重新选择 Pipeline，不会直接复用旧 Run。"},
            {"intent": "SELECT_PIPELINE", "strategy": "retention_first", "reply": "正在切换。"},
            {"intent": "SUBMIT_RUN", "reply": "重新开始运行。"},
            {
                "intent": "EDIT_TASK_SPEC",
                "task_spec_patch": {"hard_constraints": {"disabled_capabilities": ["image_quality"]}},
                "reply": "去掉清晰度筛选。",
            },
            {"intent": "EDIT_TASK_SPEC", "action": "accept_defaults", "confirm_after_edit": True, "reply": "确认。"},
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
    assert gateway.calls == 5

    pipeline_question = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="你跑的是哪个pipeline",
    )
    repeated_question = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="你跑的是哪个pipeline",
    )
    assert pipeline_question["reply"] == repeated_question["reply"]
    assert submitted["run"]["pipeline_version_id"] in pipeline_question["reply"]
    assert "quality_first" in pipeline_question["reply"]
    status_question = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="现在运行进度怎么样",
    )
    assert status_question["run"]["id"] == submitted["run"]["id"]
    assert status_question["run"]["status"] == "QUEUED"
    assert gateway.calls == 8

    assert runtime.run_store is not None
    runtime.run_store.mark_failed(submitted["run"]["id"], "test failure")
    rerun = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="重新跑"
    )
    assert rerun["run"]["id"] != submitted["run"]["id"]
    assert rerun["run"]["pipeline_version_id"] == submitted["run"][
        "pipeline_version_id"
    ]
    runtime.run_store.mark_failed(rerun["run"]["id"], "rerun test failure")

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
    assert gateway.calls == 14


def test_confirmed_task_spec_can_recompile_with_current_catalog(tmp_path) -> None:
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            _start_work_order(source, "筛选清晰图片并去重"),
            {
                "intent": "EDIT_TASK_SPEC",
                "action": "accept_defaults",
                "confirm_after_edit": True,
            },
            {"intent": "SELECT_PIPELINE", "strategy": "balanced"},
        ]
    )
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=gateway,
    )
    conversation = service.create("user_1")
    created = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"筛选清晰图片并去重',
    )
    candidates = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="按推荐值确认"
    )
    old_ids = {
        item["id"] for item in candidates["turn"]["state"]["representative_pipelines"]
    }
    ready = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="均衡"
    )

    recompiled = runtime.recompile_pipeline_candidates(
        work_order_id=ready["work_order_id"], owner_id="user_1"
    )

    assert recompiled["interrupts"][0]["value"]["kind"] == "pipeline_approval"
    new_ids = {
        item["id"]
        for item in recompiled["turn"]["state"]["representative_pipelines"]
    } if "turn" in recompiled else {
        item["id"] for item in recompiled["state"]["representative_pipelines"]
    }
    assert new_ids.isdisjoint(old_ids)
    assert gateway.calls == 3


def test_quoted_path_and_requirement_in_one_message_create_work_order(tmp_path) -> None:
    source = tmp_path / "cats_dogs_mixed" / "images"
    source.mkdir(parents=True)
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    requirement = "去掉里面不真实、不清晰的图片，把猫和狗的图片分开"
    gateway = FakeConversationGateway([_start_work_order(source, requirement)])
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
        content=f'"{source}"{requirement}',
    )

    assert response["work_order_id"] is not None
    assert response["turn"]["interrupts"][0]["value"]["kind"] == (
        "task_spec_confirmation"
    )
    assert response["turn"]["state"]["task_spec"]["objective"] == requirement
    assert gateway.calls == 1
    assert response["turn"]["state"]["task_spec"]["data_sources"][0]["uri"] == str(
        source.resolve()
    )
    assert gateway.calls == 1
    assert "还需要你补充" in response["reply"]
    assert response["turn"]["state"]["task_spec"]["ambiguities"] == [
        "hard_constraints.authenticity_scope",
        "preferences.mixed_policy",
        "preferences.unknown_policy",
        "hard_constraints.preserve_source",
        "preferences.output_layout",
    ]


def test_path_adjacent_to_chinese_is_extracted_by_model(tmp_path) -> None:
    """The path no longer needs to be at position 0 or quote-delimited."""
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    requirement = "筛选清晰图片并去重"
    gateway = FakeConversationGateway([_start_work_order(source, requirement)])
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
        content=f"帮我处理这个目录{source}里的图片",
    )

    assert response["work_order_id"] is not None
    assert response["turn"]["state"]["task_spec"]["data_sources"][0]["uri"] == str(
        source.resolve()
    )
    assert response["turn"]["state"]["task_spec"]["objective"] == requirement


def test_confirmed_task_runs_governed_planning_tools_and_persists_trace(
    tmp_path,
) -> None:
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    requirement = "筛选清晰图片并去重"
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway(
            [
                _start_work_order(source, requirement),
                {"intent": "APPROVE", "reply": "确认。"},
            ]
        ),
    )
    conversation = service.create("user_1")
    created = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"{requirement}',
    )
    assert created["turn"]["state"]["task_spec"]["ambiguities"] == []

    streamed_actions = []
    approved = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="确认",
        action_sink=streamed_actions.append,
    )

    tools = [item["tool"] for item in approved["action_trace"]]
    assert tools == [
        "conversation_turn",
        "propose_control_action",
        "retrieve_operators",
        "compile_pipeline_artifact",
        "validate_pipeline_artifact",
    ]
    assert approved["action_trace"][2]["evidence_ids"]
    assert all("thought" not in item for item in approved["action_trace"])
    assert streamed_actions == approved["action_trace"]
    stored = service.get(conversation["id"], "user_1")
    assert stored["action_trace_history"][-1]["actions"] == approved["action_trace"]


def test_start_work_order_normalizes_model_task_patch_before_control_execution(
    tmp_path,
) -> None:
    source = tmp_path / "mix"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            {
                "intent": "START_WORK_ORDER",
                "source": str(source),
                "requirement": "去掉不真实、不清晰、非实拍直出、非猫狗图片并分类",
                "task_spec_patch": {
                    "classification": {
                        "mode": "closed_set",
                        "labels": [
                            {
                                "id": "cat",
                                "display_name": "猫",
                                "aliases": ["猫咪", "小猫"],
                            },
                            {
                                "id": "dog",
                                "display_name": "狗",
                                "aliases": ["犬", "小狗"],
                            },
                        ],
                        "mixed_label": {
                            "id": "mixed",
                            "display_name": "猫狗同框",
                            "aliases": ["混合"],
                        },
                        "unknown_label": {
                            "id": "unknown",
                            "display_name": "非猫狗",
                            "aliases": ["其他动物"],
                        },
                    },
                    "hard_constraints": {
                        "preserve_source": True,
                        "authenticity_scope": {
                            "authentic": "真实实拍直出照片",
                            "inauthentic": "AI生成、截图、合成或编辑图片",
                        },
                    },
                    "preferences": {
                        "mixed_policy": "review",
                        "unknown_policy": "reject",
                        "output_layout": "按类别分目录输出",
                    },
                    "semantic_requirements": {
                        "authenticity": "排除非实拍直出的图片",
                        "clarity": "排除模糊、不清晰的图片",
                        "relevance": "排除既不是猫也不是狗的图片",
                    },
                    "exclusion_requirements": [
                        "不真实的图片",
                        "不清晰的图片",
                        "非猫狗图片",
                    ],
                },
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

    response = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f"{source} 去掉不真实、不清晰、非实拍直出、非猫狗图片并分类",
    )

    assert response["work_order_id"] is not None
    assert "控制面拒绝" not in response["reply"]
    spec = response["turn"]["state"]["task_spec"]
    assert spec["classification"]["mixed_label"] == "mixed"
    assert spec["classification"]["unknown_label"] == "unknown"
    assert spec["semantic_requirements"] == [
        "排除非实拍直出的图片",
        "排除模糊、不清晰的图片",
        "排除既不是猫也不是狗的图片",
    ]


def test_missing_path_triggers_react_self_correction(tmp_path) -> None:
    """An invalid source is fed back so the model asks the user, in one turn."""
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    bad_source = tmp_path / "does_not_exist"
    gateway = FakeConversationGateway(
        [
            _start_work_order(bad_source, "筛选清晰图片并去重"),
            {"intent": "CHAT", "reply": "找不到这个目录，请提供正确的图片目录路径。"},
        ]
    )
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
        content=f'"{bad_source}"筛选清晰图片并去重',
    )

    assert response["work_order_id"] is None
    assert response["turn"] is None
    assert "目录" in response["reply"]
    assert gateway.calls == 2


def test_file_path_rejected_with_react_feedback(tmp_path) -> None:
    """A file (not a directory) is fed back instead of hard-rejected."""
    source_dir = tmp_path / "images"
    source_dir.mkdir()
    file_path = source_dir / "data.csv"
    file_path.write_text("a,b\n1,2\n", encoding="utf-8")
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            _start_work_order(file_path, "筛选清晰图片"),
            {"intent": "CHAT", "reply": "这个路径是文件不是目录，请提供图片目录。"},
        ]
    )
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
        content=f'"{file_path}"筛选清晰图片',
    )

    assert response["work_order_id"] is None
    assert "目录" in response["reply"]
    assert gateway.calls == 2


def test_react_cannot_replace_a_bad_source_with_an_ungrounded_path(tmp_path) -> None:
    """The model must ask instead of inventing a different local directory."""
    source = tmp_path / "images"
    source.mkdir()
    bad_source = tmp_path / "wrong_dir"
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            _start_work_order(bad_source, "筛选清晰图片并去重"),
            _start_work_order(source, "筛选清晰图片并去重"),
            {"intent": "CHAT", "reply": "请提供正确的图片目录。"},
        ]
    )
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
        content=f'"{bad_source}"筛选清晰图片并去重',
    )

    assert response["work_order_id"] is None
    assert response["turn"] is None
    assert "目录" in response["reply"]
    assert gateway.calls == 3


def test_synonym_and_english_requirements_start_work_order(tmp_path) -> None:
    """Synonyms and English that the old keyword whitelist rejected now work."""
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway(
            [_start_work_order(source, "清理并剔除重复照片")]
        ),
    )
    conversation = service.create("user_1")

    response = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"清理并剔除重复照片',
    )
    assert response["work_order_id"] is not None

    service2 = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway(
            [_start_work_order(source, "filter blurry photos and deduplicate")]
        ),
    )
    conversation2 = service2.create("user_1")
    response2 = service2.send(
        thread_id=conversation2["id"],
        owner_id="user_1",
        content=f'"{source}" filter blurry photos and deduplicate',
    )
    assert response2["work_order_id"] is not None


def test_complex_task_requires_clarification_before_confirmation(tmp_path) -> None:
    source = tmp_path / "cats_dogs"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    requirement = "去掉不真实、不是实拍直出的图片，把猫和狗分开"
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway(
            [
                _start_work_order(source, requirement),
                {"intent": "EDIT_TASK_SPEC", "action": "accept_defaults", "reply": "按推荐默认值。"},
                {"intent": "APPROVE", "reply": "确认。"},
            ]
        ),
    )
    conversation = service.create("user_1")
    created = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"{requirement}',
    )

    assert created["turn"]["interrupts"][0]["value"]["kind"] == (
        "task_spec_confirmation"
    )
    assert "还需要你补充" in created["reply"]
    assert created["turn"]["state"]["task_spec"]["confirmed"] is False

    revised = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="按推荐默认值"
    )
    spec = revised["turn"]["state"]["task_spec"]
    assert spec["version"] == created["turn"]["state"]["task_spec"]["version"] + 1
    assert spec["ambiguities"] == []
    assert spec["hard_constraints"]["preserve_source"] is True
    assert spec["preferences"]["mixed_policy"] == "review"
    assert spec["hard_constraints"]["authenticity_scope"] == {
        "uncertain_policy": "review"
    }

    approved = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="确认"
    )
    assert approved["turn"]["state"]["task_spec"]["confirmed"] is True


def test_clarification_questions_are_generated_by_the_model(tmp_path) -> None:
    source = tmp_path / "categories"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            _decision(
                intent="START_WORK_ORDER",
                source=str(source),
                requirement="把猫和狗图片分开，排除其他图片",
                task_spec_patch={
                    "classification": {
                        "mode": "closed_set",
                        "labels": [
                            {
                                "id": "cat",
                                "display_name": "猫",
                                "aliases": ["猫"],
                            },
                            {
                                "id": "dog",
                                "display_name": "狗",
                                "aliases": ["狗"],
                            },
                        ],
                        "mixed_label": "mixed",
                        "unknown_label": "unknown",
                    },
                    "preferences": {"unknown_policy": "reject"},
                    "exclusion_requirements": ["排除目标类别集合之外的图片"],
                },
                reply="创建分类任务。",
            )
        ],
        clarification_responses=[
            {
                "summary": "已记录：类别集合之外的图片直接排除。",
                "questions": [
                    {
                        "field": "preferences.mixed_policy",
                        "question": "目标类别同时出现时，你希望保留、复核还是排除？",
                    },
                    {
                        "field": "hard_constraints.preserve_source",
                        "question": "是否保持源目录只读？",
                    },
                    {
                        "field": "preferences.output_layout",
                        "question": "结果希望采用哪种目录布局？",
                    },
                ],
            }
        ],
    )
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
        content=f'"{source}"筛选目标类别并分类',
    )

    assert "已记录：类别集合之外的图片直接排除。" in response["reply"]
    assert "目标类别同时出现时" in response["reply"]
    assert gateway.clarification_calls == 1


def test_contextual_continue_accepts_remaining_defaults_and_confirms(tmp_path) -> None:
    source = tmp_path / "mix"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    requirement = "去掉不真实、非实拍直出的、不清晰的图片，把猫和狗分开"
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway(
            [
                _start_work_order(source, requirement),
                {"intent": "EDIT_TASK_SPEC", "action": "accept_defaults", "confirm_after_edit": True, "reply": "继续。"},
            ]
        ),
    )
    conversation = service.create("user_1")
    created = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"{requirement}',
    )
    thread = runtime.conversation_store.get(conversation["id"], "user_1")
    partial = service._apply(
        thread=thread,
        owner_id="user_1",
        content="补充真实性和类别要求",
        decision=_decision(
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
            },
        ),
    )
    assert partial["turn"]["state"]["task_spec"]["ambiguities"] == [
        "hard_constraints.preserve_source",
        "preferences.output_layout",
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
    assert continued["turn"]["state"]["next_action"] != "confirm_task_spec"
    assert "已采纳剩余推荐值" in continued["reply"]


def test_affirmative_confirmation_cannot_be_appended_to_task_spec(tmp_path) -> None:
    source = tmp_path / "mix"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    requirement = "去掉不真实、非实拍直出的图片，把猫和狗分开"
    gateway = FakeConversationGateway(
        [
            _start_work_order(source, requirement),
            {
                "intent": "EDIT_TASK_SPEC",
                "action": "accept_defaults",
                "reply": "采用推荐默认值。",
            },
            {
                "intent": "EDIT_TASK_SPEC",
                "reply": "写入确认。",
            },
            {"intent": "APPROVE", "reply": "确认当前 TaskSpec。"},
        ]
    )
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=gateway,
    )
    conversation = service.create("user_1")

    created = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"{requirement}',
    )
    assert created["turn"]["state"]["task_spec"]["ambiguities"]

    defaulted = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="可以了"
    )
    defaulted_spec = defaulted["turn"]["state"]["task_spec"]
    assert defaulted_spec["ambiguities"] == []
    assert defaulted_spec["confirmed"] is False
    version_before_confirmation = defaulted_spec["version"]

    confirmed = service.send(
        thread_id=conversation["id"], owner_id="user_1", content="是"
    )
    confirmed_spec = confirmed["turn"]["state"]["task_spec"]
    assert confirmed_spec["confirmed"] is True
    assert confirmed_spec["version"] == version_before_confirmation + 1
    assert "是" not in confirmed_spec["semantic_requirements"]
    assert "可以了" not in confirmed_spec["semantic_requirements"]
    assert gateway.calls == 4


def test_model_driven_clarification_patch_is_applied(tmp_path) -> None:
    """The model supplies a structured patch; the system applies + normalizes."""
    source = tmp_path / "mix"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    requirement = "去掉不真实、非实拍直出的图片，把猫和狗分开"
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway(
            [
                _start_work_order(source, requirement),
                {
                    "intent": "EDIT_TASK_SPEC",
                    "task_spec_patch": {
                        "hard_constraints": {
                            "preserve_source": True,
                            "authenticity_scope": "排除插画、截图、明显合成",
                        },
                        "preferences": {
                            "mixed_policy": "keep",
                            "unknown_policy": "review",
                            "output_layout": "versioned_class_directories",
                        },
                        "exclusion_requirements": ["排除插画", "排除截图", "排除明显合成"],
                        "semantic_requirements": [],
                    },
                    "reply": "已写入澄清。",
                },
            ]
        ),
    )
    conversation = service.create("user_1")
    service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content=f'"{source}"{requirement}',
    )

    revised = service.send(
        thread_id=conversation["id"],
        owner_id="user_1",
        content="排除插画、截图、明显合成。输出到猫狗都有的文件夹",
    )

    spec = revised["turn"]["state"]["task_spec"]
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
        gateway=FakeConversationGateway([_start_work_order(source, "去掉不真实图片")]),
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
        decision=_decision(
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


def test_quoted_standalone_path_continues_pending_requirement(tmp_path) -> None:
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            {"intent": "START_WORK_ORDER", "requirement": "筛选清晰图片并去重", "reply": "请提供目录。"},
            {"intent": "PROVIDE_SOURCE", "source": str(source), "reply": "我来检查这个目录。"},
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
        thread_id=conversation["id"], owner_id="user_1", content=f'"{source}"'
    )

    assert response["work_order_id"] is not None
    assert response["turn"]["interrupts"][0]["value"]["kind"] == (
        "task_spec_confirmation"
    )
    assert gateway.calls == 2


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


def test_pipeline_approval_uses_grounded_pipeline_context(tmp_path) -> None:
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
                        "datajuicer.image_tagging_vlm_mapper.remote_api:2"
                    ),
                    "runtime_backend": "remote",
                    "parameters": {"tag_field_name": "image_tags"},
                },
            ],
        }
    ]
    details = ConversationService._pipeline_details_reply(pipelines)
    assert "builtin.quality_filter:1" in details
    assert "datajuicer.image_tagging_vlm_mapper.remote_api:2" in details
    assert "data_loader" not in details

    current_context = {
        "approved_pipeline": pipelines[0],
        "latest_run_pipeline": pipelines[0],
        "pipeline_choices": pipelines,
    }
    current = ConversationService._control_facts_reply(
        current_context, frozenset({"operators", "pipeline"})
    )
    assert "pipeline_balanced" in current
    assert "builtin.quality_filter:1" in current


def test_pipeline_approval_resumes_eligible_pipeline() -> None:
    pipeline = {
        "id": "pipeline_balanced",
        "strategy": "balanced",
        "execution_eligibility": {"eligible": True, "violations": []},
    }
    runtime = EligiblePipelineRuntime(pipeline)
    service = ConversationService(
        store=runtime,
        agent_runtime=runtime,
        settings=None,  # type: ignore[arg-type]
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
        content="均衡",
        decision=_decision(
            intent="SELECT_PIPELINE", strategy="balanced", reply="选择均衡"
        ),
    )
    assert response["turn"]["state"]["next_action"] == "submit_dataset_run"
    assert response["turn"]["state"]["approved_pipeline"]["id"] == "pipeline_balanced"
    assert runtime.resumed["approved"] is True
    assert runtime.resumed["pipeline_id"] == "pipeline_balanced"


def test_submit_run_without_work_order_is_blocked(tmp_path) -> None:
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway([]),
    )
    response = service._apply(
        thread={
            "id": "conversation_1",
            "owner_id": "user_1",
            "work_order_id": None,
            "context": {},
        },
        owner_id="user_1",
        content="开始运行",
        decision=_decision(intent="SUBMIT_RUN", reply="开始运行。"),
    )
    assert response["turn"] is None
    assert "还没有工单" in response["reply"]


def test_control_fact_queries_are_grounded_composable_and_repeatable() -> None:
    pipeline = {
        "id": "pipeline_version_real",
        "strategy": "retention_first",
        "nodes": [
            {
                "id": "decode",
                "operator_version_id": "builtin.decode_check:1",
                "runtime_backend": "cpu",
                "operator_status": "PERSONAL_RELEASE",
            },
            {
                "id": "classification",
                "operator_version_id": (
                    "datajuicer.image_tagging_vlm_mapper.remote_api:2"
                ),
                "runtime_backend": "remote",
                "operator_status": "PERSONAL_RELEASE",
            },
        ],
    }
    context = {
        "work_order_id": "work_order_real",
        "latest_run": {
            "id": "run_real",
            "status": "RUNNING",
            "progress": 3,
            "total": 37,
            "kept": 2,
            "rejected": 1,
            "failed": 0,
        },
        "latest_run_pipeline": pipeline,
        "approved_pipeline": pipeline,
    }

    first = ConversationService._control_facts_reply(context, frozenset({"pipeline"}))
    repeated = ConversationService._control_facts_reply(context, frozenset({"pipeline"}))
    combined = ConversationService._control_facts_reply(
        context, frozenset({"run", "pipeline", "operators"})
    )

    assert first == repeated
    assert "pipeline_version_real" in first
    assert "retention_first" in first
    assert "3/37" in combined
    assert "builtin.decode_check:1" in combined
    assert "datajuicer.image_tagging_vlm_mapper.remote_api:2" in combined
    assert "parameters=" in combined


def test_audit_fact_query_shows_grounded_rejection_and_failure_reasons() -> None:
    audit = ConversationService._audit_control_summary(
        [
            {
                "asset_sequence": 0,
                "source_uri": "D:/images/rejected.png",
                "node_id": "authenticity",
                "operator_version_id": "builtin.authenticity_decision:1",
                "status": "completed",
                "decision": "reject",
                "reason_codes": ["SYNTHETIC_IMAGE"],
                "duration_ms": 4,
            },
            {
                "asset_sequence": 1,
                "source_uri": "D:/images/failed.png",
                "node_id": "tagging",
                "operator_version_id": "datajuicer.image_tagging_vlm_mapper.remote_api:2",
                "status": "failed",
                "decision": "failed",
                "reason_codes": ["OPERATOR_ERROR:TimeoutError"],
                "error": "request timed out",
                "duration_ms": 300000,
            },
        ]
    )

    reply = ConversationService._control_facts_reply(
        {"latest_run_audit": audit}, frozenset({"audit"})
    )

    assert "rejected.png" in reply and "SYNTHETIC_IMAGE" in reply
    assert "failed.png" in reply and "request timed out" in reply


def test_dataset_result_queries_use_materialized_paths_and_actual_classes(
    tmp_path,
) -> None:
    dataset_root = tmp_path / "datasets" / "dataset_real"
    unknown = dataset_root / "files" / "classes" / "unknown"
    unknown.mkdir(parents=True)
    output = unknown / "sample.jpg"
    output.write_bytes(b"image")
    manifest = dataset_root / "manifest.json"
    manifest.write_text(json.dumps({"id": "dataset_real"}), encoding="utf-8")
    summary = ConversationService._dataset_control_summary(
        {
            "id": "dataset_real",
            "manifest_uri": str(manifest),
            "source_count": 1,
            "kept_count": 1,
            "rejected_count": 0,
            "failed_count": 0,
            "original_files_unchanged": True,
            "assets": [
                {
                    "source_uri": str(tmp_path / "source.jpg"),
                    "output_uri": str(output),
                    "decision": "keep",
                    "labels": {
                        "resolved_class": "unknown",
                        "authenticity": "uncertain",
                        "datajuicer_output": {},
                    },
                }
            ],
        }
    )
    pipeline = {
        "id": "pipeline_real",
        "nodes": [
            {"id": "authenticity_decision", "parameters": {"uncertain_policy": "keep"}},
            {"id": "class_resolution", "parameters": {"unknown_policy": "keep"}},
        ],
    }
    context = {
        "latest_run": {"id": "run_real", "status": "SUCCEEDED"},
        "latest_run_pipeline": pipeline,
        "latest_dataset": summary,
    }

    questions = ("那你分类后输出到哪了", "完整路径", "并没有这些文件夹")
    replies = [
        ConversationService._control_facts_reply(context, frozenset({"dataset"}))
        for _ in questions
    ]
    explanation = ConversationService._control_facts_reply(
        context, frozenset({"outcome"})
    )

    assert all(str(dataset_root.resolve()) in reply for reply in replies)
    assert all(str(unknown.resolve()) in reply for reply in replies)
    assert all("classes\\cat" not in reply for reply in replies)
    assert all("classes\\dog" not in reply for reply in replies)
    assert "VLM 标签为空" in explanation
    assert "uncertain=keep" in explanation
    assert "unknown=keep" in explanation


def test_repair_fact_queries_are_rendered_from_dataset_manifest() -> None:
    summary = ConversationService._dataset_control_summary(
        {
            "id": "dataset_repaired_3",
            "manifest_uri": "D:/datasets/dataset_repaired_3/manifest.json",
            "source_count": 2,
            "kept_count": 1,
            "rejected_count": 0,
            "failed_count": 1,
            "original_files_unchanged": True,
            "parent_dataset_version_id": "dataset_parent",
            "repair_run_ids": ["run_repair_1", "run_repair_2", "run_repair_3"],
            "still_failed": [],
            "abandoned_assets": [
                {
                    "source_uri": "D:/images/failed.png",
                    "source_sha256": "abc",
                    "reason_codes": ["OPERATOR_ERROR:TimeoutError"],
                    "audit_refs": ["run:run_repair_3:asset:0"],
                    "repair_attempts": 3,
                }
            ],
            "excluded_assets": [],
            "assets": [
                {
                    "source_uri": "D:/images/kept.png",
                    "output_uri": "D:/datasets/parent/files/kept.png",
                    "decision": "keep",
                    "asset_origin": "parent_dataset_version",
                    "origin_dataset_version_id": "dataset_parent",
                    "origin_run_id": "run_parent",
                    "materialization": "referenced_file",
                    "labels": {},
                },
                {
                    "source_uri": "D:/images/failed.png",
                    "decision": "failed",
                    "asset_origin": "repair_run",
                    "origin_dataset_version_id": "dataset_repair_output",
                    "origin_run_id": "run_repair_3",
                    "materialization": "not_materialized",
                    "reason_codes": ["OPERATOR_ERROR:TimeoutError"],
                    "labels": {},
                },
            ],
        }
    )

    reply = ConversationService._control_facts_reply(
        {"latest_dataset": summary},
        frozenset({"repair"}),
    )

    assert "dataset_parent" in reply
    assert "run_repair_3" in reply
    assert "abandoned=1" in reply
    assert "D:/images/failed.png" in reply
    assert "OPERATOR_ERROR:TimeoutError" in reply
    assert "parent_dataset_version" in reply
    assert "repair_run" in reply


def test_ungrounded_control_plane_identifiers_are_detected() -> None:
    context = {
        "work_order_id": "work_order_real",
        "latest_run": {
            "id": "run_real",
            "pipeline_version_id": "pipeline_version_real",
        },
    }

    grounded = ConversationService._ungrounded_control_identifiers(
        "Run run_real uses pipeline_version_real", context
    )
    hallucinated = ConversationService._ungrounded_control_identifiers(
        "Run run_fake uses pipeline_version_invented", context
    )

    assert grounded == ()
    assert hallucinated == ("pipeline_version_invented", "run_fake")


def test_model_reply_with_invented_control_identifier_is_blocked(tmp_path) -> None:
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    service = ConversationService(
        store=runtime.conversation_store,
        agent_runtime=runtime,
        settings=_settings(tmp_path),
        gateway=FakeConversationGateway(
            [
                {
                    "intent": "CHAT",
                    "reply": "选中了 pipeline_version_invented。",
                }
            ]
        ),
    )
    context = {"approved_pipeline": {"id": "pipeline_version_real"}}

    decision = service._decide(
        "它到底选中了啥",
        [{"role": "user", "content": "它到底选中了啥"}],
        context,
    )

    assert decision.resolved_by == "control-fact-guard"
    assert decision.fallback_reason == (
        "ungrounded_control_identifiers:pipeline_version_invented"
    )
    assert "pipeline_version_invented" not in decision.reply


@pytest.mark.parametrize(
    "question",
    (
        "你跑的是哪个pipeline",
        "当前用的哪条流水线",
        "这个 Run 用的什么方案",
    ),
)
def test_pipeline_identity_reply_is_grounded_and_stable(question) -> None:
    pipeline = {
        "id": "pipeline_version_persisted",
        "strategy": "balanced",
        "nodes": [],
    }
    context = {
        "latest_run": {
            "id": "run_persisted",
            "status": "SUCCEEDED",
            "progress": 10,
            "total": 10,
            "kept": 9,
            "rejected": 1,
            "failed": 0,
        },
        "latest_run_pipeline": pipeline,
    }

    reply = ConversationService._control_facts_reply(context, frozenset({"pipeline"}))

    assert "pipeline_version_persisted" in reply
    assert "balanced" in reply
    del question  # facet selection is the model's job now; reply is facet-driven


def test_task_spec_supplement_is_revised_before_explicit_approval(tmp_path) -> None:
    source = tmp_path / "images"
    source.mkdir()
    runtime = AgentRuntime(tmp_path / "runtime")
    assert runtime.conversation_store is not None
    gateway = FakeConversationGateway(
        [
            {"intent": "START_WORK_ORDER", "requirement": "筛选清晰的猫狗图片", "reply": "请提供目录。"},
            {"intent": "PROVIDE_SOURCE", "source": str(source), "reply": "我来检查目录。"},
            {"intent": "QUERY_CONTROL_FACTS", "facets": ["task_spec"], "reply": ""},
            {
                "intent": "EDIT_TASK_SPEC",
                "task_spec_patch": {
                    "exclusion_requirements": [
                        "Exclude AI-generated or obviously composited images"
                    ],
                    "preferences": {"output_layout": "separate_classes"},
                },
                "reply": "写入排除项。",
            },
            {"intent": "EDIT_TASK_SPEC", "action": "accept_defaults", "confirm_after_edit": True, "reply": "确认。"},
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
    assert gateway.calls == 3

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
    assert gateway.calls == 5


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
        decision=_decision(
            intent="SELECT_PIPELINE", strategy="quality_first", reply="选择质量优先"
        ),
    )

    assert response["turn"] is None
    assert "仍停留在方案选择阶段" in response["reply"]
    assert "provider parameter schema is invalid" in response["reply"]
