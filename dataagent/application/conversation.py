from __future__ import annotations

import json
import logging
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..config import Settings
from ..agents.requirement.clarification import recommended_clarification_patch
from ..domain.common import new_id
from ..gateway import ModelGateway, ModelGatewayError
from ..infrastructure import ConversationStore
from .agent_runtime import AgentRuntime


logger = logging.getLogger(__name__)


class ConversationIntent(StrEnum):
    CHAT = "CHAT"
    START_WORK_ORDER = "START_WORK_ORDER"
    PROVIDE_SOURCE = "PROVIDE_SOURCE"
    EDIT_TASK_SPEC = "EDIT_TASK_SPEC"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    SUBMIT_RUN = "SUBMIT_RUN"
    RUN_STATUS = "RUN_STATUS"
    CONTROL_RUN = "CONTROL_RUN"
    RESOLVE_GAP = "RESOLVE_GAP"


class ConversationDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: ConversationIntent = ConversationIntent.CHAT
    reply: str = ""
    requirement: str | None = None
    source: str | None = None
    strategy: str | None = None
    action: str | None = None
    runtime_backend: str | None = None
    task_spec_patch: dict[str, Any] | None = None
    resolved_by: str = "deterministic"
    fallback_reason: str | None = None


class ConversationService:
    def __init__(
        self,
        *,
        store: ConversationStore,
        agent_runtime: AgentRuntime,
        settings: Settings,
        gateway: ModelGateway | None = None,
    ) -> None:
        self.store = store
        self.agent_runtime = agent_runtime
        self.settings = settings
        self.gateway = gateway or ModelGateway(settings)

    def create(self, owner_id: str) -> dict[str, Any]:
        thread = self.store.create(thread_id=new_id("conversation"), owner_id=owner_id)
        return self._public_thread(thread)

    def get(self, thread_id: str, owner_id: str) -> dict[str, Any]:
        return self._public_thread(self.store.get(thread_id, owner_id))

    def bind_work_order(
        self, *, thread_id: str, owner_id: str, work_order_id: str
    ) -> dict[str, Any]:
        self.agent_runtime.state(work_order_id=work_order_id, owner_id=owner_id)
        thread = self.store.get(thread_id, owner_id)
        updated = self.store.update(
            thread_id=thread_id,
            owner_id=owner_id,
            context=thread["context"],
            work_order_id=work_order_id,
        )
        return self._public_thread(updated)

    def send(self, *, thread_id: str, owner_id: str, content: str) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("Message must not be empty")
        thread = self.store.get(thread_id, owner_id)
        self.store.add_message(
            thread_id=thread_id, owner_id=owner_id, role="user", content=content
        )
        history = self.store.messages(thread_id, owner_id)
        control_context = self._control_context(thread, owner_id)
        decision = self._decide(content, history, control_context)
        response = self._apply(
            thread=thread,
            owner_id=owner_id,
            content=content,
            decision=decision,
        )
        self.store.add_message(
            thread_id=thread_id,
            owner_id=owner_id,
            role="assistant",
            content=response["reply"],
            intent=decision.intent,
            model=decision.resolved_by,
        )
        if decision.fallback_reason:
            latest = self.store.get(thread_id, owner_id)
            latest_context = dict(latest["context"])
            diagnostics = dict(latest_context.get("conversation_runtime", {}))
            diagnostics["fallback_count"] = int(diagnostics.get("fallback_count", 0)) + 1
            diagnostics["last_fallback_reason"] = decision.fallback_reason
            diagnostics["last_fallback_model"] = self.settings.fast_text_model
            latest_context["conversation_runtime"] = diagnostics
            self.store.update(
                thread_id=thread_id,
                owner_id=owner_id,
                context=latest_context,
            )
            response["diagnostics"] = {
                "fallback_used": True,
                "model": self.settings.fast_text_model,
                "reason": decision.fallback_reason,
            }
        updated_thread = self.store.get(thread_id, owner_id)
        response["conversation_id"] = thread_id
        response["work_order_id"] = updated_thread.get("work_order_id")
        response["messages"] = self.store.messages(thread_id, owner_id)
        return response

    def _decide(
        self,
        content: str,
        history: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> ConversationDecision:
        source_decision = self._source_or_pending_task_decision(content, context)
        if source_decision is not None:
            return source_decision
        resolution_decision = self._pending_resolution_decision(content, context)
        if resolution_decision is not None:
            return resolution_decision
        task_spec_decision = self._task_spec_details_decision(content, context)
        if task_spec_decision is not None:
            return task_spec_decision
        clarification_decision = self._pending_clarification_decision(content, context)
        if clarification_decision is not None:
            return clarification_decision
        pipeline_decision = self._pipeline_details_decision(content, context)
        if pipeline_decision is not None:
            return pipeline_decision
        fast = self._fast_decision(content)
        if fast is not None:
            return fast
        fallback = self._fallback_decision(content, context)
        if not self.gateway.configured:
            return fallback.model_copy(
                update={
                    "resolved_by": "local-fallback",
                    "fallback_reason": "model_gateway_not_configured",
                }
            )
        try:
            raw, _ = self.gateway.conversation_turn(
                history=[
                    {"role": item["role"], "content": item["content"]}
                    for item in history
                ],
                context=context,
            )
            decision = ConversationDecision.model_validate(raw).model_copy(
                update={"resolved_by": self.settings.fast_text_model}
            )
        except (ModelGatewayError, ValueError, TypeError) as exc:
            reason = f"{type(exc).__name__}: {exc}"[:500]
            logger.warning(
                "Conversation model failed; returning a non-mutating fallback: %s",
                reason,
            )
            return fallback.model_copy(
                update={
                    "reply": (
                        "模型服务本轮未返回有效结果，控制平面没有执行任何操作。"
                        "请重试刚才的问题。"
                    ),
                    "resolved_by": "local-fallback",
                    "fallback_reason": reason,
                }
            )
        if decision.intent == ConversationIntent.START_WORK_ORDER:
            requirement = decision.requirement or content
            if not self._looks_like_data_requirement(requirement):
                return decision.model_copy(update={"intent": ConversationIntent.CHAT})
        if decision.intent == ConversationIntent.PROVIDE_SOURCE and not context.get(
            "pending_requirement"
        ):
            return decision.model_copy(update={"intent": ConversationIntent.CHAT})
        if (
            context.get("agent_state", {}).get("waiting")
            == "task_spec_confirmation"
            and decision.intent == ConversationIntent.APPROVE
            and not self._is_explicit_approval(content)
        ):
            return decision.model_copy(
                update={
                    "intent": ConversationIntent.EDIT_TASK_SPEC,
                    "reply": "我会先把这段补充写入 TaskSpec，再请你确认。",
                    "task_spec_patch": decision.task_spec_patch
                    or {"semantic_requirements": [content]},
                }
            )
        return decision

    def _fast_decision(self, content: str) -> ConversationDecision | None:
        normalized = content.strip().lower().strip("!！。,.，~～ ")
        if normalized in {"你好", "您好", "嗨", "hi", "hello", "hey"}:
            return ConversationDecision(
                reply="你好，我是 DataAgent。你可以直接问我问题，也可以描述图片数据任务。"
            )
        if normalized in {"你是谁", "你叫什么", "who are you"}:
            return ConversationDecision(
                reply="我是 DataAgent，负责把图片数据需求规划、执行并评测成可追溯的数据版本。"
            )
        if any(
            token in normalized
            for token in (
                "怎么使用",
                "如何使用",
                "怎么用",
                "能做什么",
                "帮助",
                "how to use",
                "how do i use",
                "what can you do",
                "help",
            )
        ):
            return ConversationDecision(
                reply=(
                    "直接描述你的图片数据目标即可，例如“筛选清晰人像并去重”。"
                    "我会在信息不足时追问目录和约束，方案确认后再运行，不需要记命令。"
                )
            )
        if any(
            token in normalized
            for token in ("什么模型", "哪个模型", "what model", "which model")
        ):
            return ConversationDecision(
                reply=(
                    f"当前普通对话由 {self.settings.fast_text_model} 处理，"
                    f"复杂规划由 {self.settings.planning_model} 处理。"
                )
            )
        if self._is_explicit_approval(content):
            return ConversationDecision(
                intent=ConversationIntent.APPROVE,
                reply="正在确认。",
            )
        if normalized in {"谢谢", "感谢", "thanks", "thank you"}:
            return ConversationDecision(reply="不客气。继续说你的需求就好。")
        if normalized in {"再见", "拜拜", "bye", "goodbye"}:
            return ConversationDecision(reply="再见。下次可以用 conversation ID 接着这段任务继续。")
        return None

    def _apply(
        self,
        *,
        thread: dict[str, Any],
        owner_id: str,
        content: str,
        decision: ConversationDecision,
    ) -> dict[str, Any]:
        context = dict(thread["context"])
        work_order_id = thread.get("work_order_id")
        base = {"reply": decision.reply or "我在。", "turn": None, "run": None}
        if decision.intent == ConversationIntent.CHAT:
            return base
        if decision.intent == ConversationIntent.START_WORK_ORDER:
            context["pending_requirement"] = decision.requirement or content
            if decision.source:
                context["pending_source"] = decision.source
            return self._maybe_start(thread, owner_id, context, base)
        if decision.intent == ConversationIntent.PROVIDE_SOURCE:
            context["pending_source"] = decision.source or content
            return self._maybe_start(thread, owner_id, context, base)
        if not work_order_id:
            base["reply"] = "当前还没有工单。请先告诉我需要生产什么图片数据。"
            return base
        if decision.intent == ConversationIntent.EDIT_TASK_SPEC:
            turn = self.agent_runtime.state(
                work_order_id=work_order_id, owner_id=owner_id
            )
            if not turn["interrupts"] or turn["interrupts"][0]["value"].get(
                "kind"
            ) != "task_spec_confirmation":
                base["reply"] = "当前没有等待修改的 TaskSpec 草案。"
                base["turn"] = turn
                return base
            patch = decision.task_spec_patch or {
                "semantic_requirements": [content]
            }
            turn = self.agent_runtime.resume(
                work_order_id=work_order_id,
                owner_id=owner_id,
                decision={
                    "action": "edit_spec",
                    "task_spec_patch": patch,
                    "channel": "conversation",
                },
            )
            base["turn"] = turn
            revised_spec = turn["state"]["task_spec"]
            prefix = (
                "TaskSpec 已生成修订版本，但还有信息需要确认。\n\n"
                if revised_spec.get("ambiguities")
                else "TaskSpec 已生成修订版本，请检查后确认。\n\n"
            )
            base["reply"] = prefix + self._task_spec_details_reply(revised_spec)
            return base
        if decision.intent in {ConversationIntent.APPROVE, ConversationIntent.REJECT}:
            turn = self.agent_runtime.state(
                work_order_id=work_order_id, owner_id=owner_id
            )
            if not turn["interrupts"]:
                base["reply"] = "当前没有等待确认的事项。"
                base["turn"] = turn
                return base
            value = turn["interrupts"][0]["value"]
            if (
                value.get("kind") == "capability_resolution"
                and decision.intent == ConversationIntent.APPROVE
            ):
                base["turn"] = turn
                base["reply"] = self._capability_resolution_reply(value)
                return base
            if (
                value.get("kind") == "task_spec_confirmation"
                and decision.intent == ConversationIntent.APPROVE
                and value.get("task_spec", {}).get("ambiguities")
            ):
                base["turn"] = turn
                base["reply"] = self._clarification_reply(value["task_spec"])
                return base
            approved = decision.intent == ConversationIntent.APPROVE
            command: dict[str, Any] = {
                "approved": approved,
                "channel": "conversation",
            }
            if not approved:
                command["reason"] = content
            elif value.get("kind") == "pipeline_approval":
                strategy = self._normalize_strategy(decision.strategy or "balanced")
                selected = next(
                    (
                        item
                        for item in value.get("pipelines", [])
                        if item.get("strategy") == strategy
                    ),
                    None,
                )
                if selected is None:
                    base["reply"] = "没有找到对应策略，请选择保留优先、均衡或质量优先。"
                    base["turn"] = turn
                    return base
                eligibility = selected.get("execution_eligibility") or {}
                if eligibility.get("eligible") is False:
                    violations = eligibility.get("violations") or ["未知执行门禁错误"]
                    base["reply"] = (
                        "这条 Pipeline 当前不能批准运行："
                        + "；".join(str(item) for item in violations)
                    )
                    base["turn"] = turn
                    return base
                command["pipeline_id"] = selected["id"]
            turn = self.agent_runtime.resume(
                work_order_id=work_order_id,
                owner_id=owner_id,
                decision=command,
            )
            base["turn"] = turn
            base["reply"] = self._turn_reply(turn, approved)
            return base
        if decision.intent == ConversationIntent.RESOLVE_GAP:
            turn = self.agent_runtime.state(
                work_order_id=work_order_id, owner_id=owner_id
            )
            if not turn["interrupts"] or turn["interrupts"][0]["value"].get(
                "kind"
            ) != "capability_resolution":
                base["reply"] = "当前没有等待处理的能力缺口。"
                base["turn"] = turn
                return base
            command: dict[str, Any] = {"action": decision.action or "retry"}
            if decision.runtime_backend:
                command["enable_runtime_backends"] = [decision.runtime_backend]
            turn = self.agent_runtime.resume(
                work_order_id=work_order_id,
                owner_id=owner_id,
                decision=command,
            )
            base["turn"] = turn
            base["reply"] = self._turn_reply(turn, True)
            return base
        if decision.intent == ConversationIntent.SUBMIT_RUN:
            run = self.agent_runtime.submit_dataset_run(
                work_order_id=work_order_id,
                owner_id=owner_id,
                idempotency_key=f"conversation-{thread['id']}-run",
            )
            context["active_run_id"] = run["id"]
            self.store.update(
                thread_id=thread["id"], owner_id=owner_id, context=context
            )
            base["run"] = run
            base["reply"] = f"已提交 Run {run['id']}，当前状态是 {run['status']}。"
            return base
        if decision.intent == ConversationIntent.RUN_STATUS:
            run = self._current_run(work_order_id, owner_id, context)
            base["run"] = run
            base["reply"] = self._run_reply(run)
            return base
        if decision.intent == ConversationIntent.CONTROL_RUN:
            run = self._current_run(work_order_id, owner_id, context)
            action = decision.action
            if action not in {"pause", "resume", "cancel"}:
                base["reply"] = "请明确要暂停、恢复还是取消当前 Run。"
                base["run"] = run
                return base
            run = self.agent_runtime.control_run(
                run_id=run["id"], owner_id=owner_id, action=action
            )
            base["run"] = run
            base["reply"] = self._run_reply(run)
            return base
        return base

    def _maybe_start(
        self,
        thread: dict[str, Any],
        owner_id: str,
        context: dict[str, Any],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        requirement = str(context.get("pending_requirement", "")).strip()
        source = self._normalize_source(str(context.get("pending_source", "")))
        if not requirement:
            response["reply"] = "请先描述你希望生产什么样的图片数据。"
        elif not source:
            response["reply"] = "需求我记下了。图片目前放在哪个本地目录？"
        else:
            path = Path(source).expanduser().resolve()
            if not path.is_dir():
                context.pop("pending_source", None)
                response["reply"] = f"目录不存在或不是文件夹：{path}。请提供一个可访问的图片目录。"
            else:
                turn = self.agent_runtime.start(
                    owner_id=owner_id,
                    requirement=requirement,
                    data_sources=[
                        {"type": "local_directory", "uri": str(path), "mapping": {}}
                    ],
                )
                context.pop("pending_requirement", None)
                context.pop("pending_source", None)
                self.store.update(
                    thread_id=thread["id"],
                    owner_id=owner_id,
                    context=context,
                    work_order_id=turn["work_order_id"],
                )
                response["turn"] = turn
                task_spec = turn["state"].get("task_spec", {})
                if task_spec.get("ambiguities"):
                    response["reply"] = (
                        f"已创建工单 {turn['work_order_id']}。"
                        "在确认 TaskSpec 前，还需要你补充以下信息：\n\n"
                        + self._clarification_reply(task_spec, include_intro=False)
                    )
                else:
                    response["reply"] = (
                        f"已创建工单 {turn['work_order_id']}。我生成了 TaskSpec 草案，"
                        "现在等你确认；你可以先问我草案内容，也可以直接说“确认”。"
                    )
                return response
        self.store.update(
            thread_id=thread["id"], owner_id=owner_id, context=context
        )
        return response

    def _control_context(self, thread: dict[str, Any], owner_id: str) -> dict[str, Any]:
        context = dict(thread["context"])
        context["work_order_id"] = thread.get("work_order_id")
        if thread.get("work_order_id"):
            turn = self.agent_runtime.state(
                work_order_id=thread["work_order_id"], owner_id=owner_id
            )
            context["agent_state"] = {
                "current_agent": turn["state"].get("current_agent"),
                "next_action": turn["state"].get("next_action"),
                "waiting": (
                    turn["interrupts"][0]["value"].get("kind")
                    if turn["interrupts"]
                    else None
                ),
            }
            if task_spec := turn["state"].get("task_spec"):
                context["task_spec"] = task_spec
            approval_pipelines = []
            if (
                turn["interrupts"]
                and turn["interrupts"][0]["value"].get("kind")
                == "pipeline_approval"
            ):
                approval_pipelines = turn["interrupts"][0]["value"].get(
                    "pipelines", []
                )
            if pipelines := (
                approval_pipelines
                or turn["state"].get("representative_pipelines")
            ):
                context["pipeline_choices"] = [
                    {
                        "id": item.get("id"),
                        "strategy": item.get("strategy"),
                        "version": item.get("version"),
                        "node_count": len(item.get("nodes", [])),
                        "nodes": [
                            {
                                "id": node.get("id"),
                                "operator_version_id": node.get("operator_version_id"),
                                "runtime_backend": node.get("runtime_backend"),
                                "operator_status": node.get("operator_status"),
                                "parameters": node.get("parameters", {}),
                            }
                            for node in item.get("nodes", [])
                        ],
                        "execution_eligibility": item.get("execution_eligibility"),
                    }
                    for item in pipelines
                ]
            if sampling_plan := turn["state"].get("sampling_plan"):
                context["sampling_plan"] = sampling_plan
            runs = self.agent_runtime.list_runs(
                work_order_id=thread["work_order_id"], owner_id=owner_id
            )
            if runs:
                context["latest_run"] = runs[0]
        return context

    def _current_run(
        self, work_order_id: str, owner_id: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        if run_id := context.get("active_run_id"):
            return self.agent_runtime.get_run(run_id=run_id, owner_id=owner_id)
        runs = self.agent_runtime.list_runs(
            work_order_id=work_order_id, owner_id=owner_id
        )
        if not runs:
            raise ValueError("当前工单还没有 Run")
        return runs[0]

    def _public_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": thread["id"],
            "work_order_id": thread["work_order_id"],
            "messages": self.store.messages(thread["id"], thread["owner_id"]),
        }

    @staticmethod
    def _task_spec_details_decision(
        content: str, context: dict[str, Any]
    ) -> ConversationDecision | None:
        normalized = content.strip().lower()
        asks_for_spec = normalized in {"草案内容", "查看草案", "任务草案"} or (
            "taskspec" in normalized
            and any(token in normalized for token in ("内容", "详情", "查看", "show"))
        )
        task_spec = context.get("task_spec")
        if not asks_for_spec or not task_spec:
            return None
        return ConversationDecision(
            intent=ConversationIntent.CHAT,
            reply=ConversationService._task_spec_details_reply(task_spec),
        )

    @staticmethod
    def _task_spec_details_reply(task_spec: dict[str, Any]) -> str:
        sources = ", ".join(
            str(item.get("uri", "-")) for item in task_spec.get("data_sources", [])
        ) or "-"
        capabilities = [
            str(item.get("capability", item.get("id", "-")))
            for item in task_spec.get("capability_requirements", [])
        ]
        lines = [
            "### 当前 TaskSpec",
            f"- 版本：`{task_spec.get('version', '-')}`",
            f"- 目标：{task_spec.get('objective', '-')}",
            f"- 数据源：`{sources}`",
            "- 输出动作："
            + (", ".join(task_spec.get("output_actions", [])) or "无"),
            "- 能力需求：" + (", ".join(capabilities) or "无"),
            "- 硬约束：`"
            + json.dumps(
                task_spec.get("hard_constraints", {}),
                ensure_ascii=False,
                sort_keys=True,
            )
            + "`",
            "- 语义需求："
            + ("；".join(task_spec.get("semantic_requirements", [])) or "无"),
            "- 排除需求："
            + ("；".join(task_spec.get("exclusion_requirements", [])) or "无"),
            "- 待澄清："
            + ("；".join(task_spec.get("ambiguities", [])) or "无"),
            f"- 已确认：{'是' if task_spec.get('confirmed') else '否'}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _clarification_reply(
        task_spec: dict[str, Any], *, include_intro: bool = True
    ) -> str:
        ambiguities = task_spec.get("ambiguities") or []
        lines = ["当前 TaskSpec 仍有待澄清项："] if include_intro else []
        lines.extend(
            f"{index}. {question}" for index, question in enumerate(ambiguities, 1)
        )
        lines.append(
            "请直接回答这些问题；也可以说“按推荐默认值”，"
            "我会写入新版本后再请你确认。"
        )
        return "\n".join(lines)

    @staticmethod
    def _pending_clarification_decision(
        content: str, context: dict[str, Any]
    ) -> ConversationDecision | None:
        if context.get("agent_state", {}).get("waiting") != "task_spec_confirmation":
            return None
        task_spec = context.get("task_spec") or {}
        ambiguities = tuple(task_spec.get("ambiguities") or ())
        if not ambiguities:
            return None
        normalized = content.strip().lower().strip("!！。,.，~～ ")
        if normalized not in {
            "按推荐默认值",
            "按默认值",
            "使用默认值",
            "采用默认值",
            "按建议",
            "用推荐值",
        }:
            return None
        return ConversationDecision(
            intent=ConversationIntent.EDIT_TASK_SPEC,
            reply="正在将推荐默认值写入 TaskSpec。",
            task_spec_patch=recommended_clarification_patch(ambiguities),
        )

    @staticmethod
    def _is_explicit_approval(content: str) -> bool:
        normalized = content.strip().lower().strip("!！。.?？")
        return normalized in {
            "确认",
            "通过",
            "批准",
            "同意",
            "没问题",
            "就这样",
            "按这个执行",
            "approve",
            "approved",
            "yes",
        }

    @staticmethod
    def _pipeline_details_decision(
        content: str, context: dict[str, Any]
    ) -> ConversationDecision | None:
        normalized = content.strip().lower()
        asks_for_pipeline = "pipeline" in normalized or "流水线" in normalized
        asks_for_operator = any(
            token in normalized
            for token in ("什么算子", "哪些算子", "用了什么", "算子顺序", "什么顺序")
        )
        if not asks_for_operator and not (
            asks_for_pipeline and any(token in normalized for token in ("具体", "详情", "节点"))
        ):
            return None
        pipelines = context.get("pipeline_choices") or []
        if not pipelines:
            return None
        return ConversationDecision(
            intent=ConversationIntent.CHAT,
            reply=ConversationService._pipeline_details_reply(pipelines),
        )

    @staticmethod
    def _pipeline_details_reply(pipelines: list[dict[str, Any]]) -> str:
        lines = ["以下是控制平面实际编译的算子流水线："]
        for pipeline in pipelines:
            eligibility = pipeline.get("execution_eligibility") or {}
            eligibility_text = (
                "可运行"
                if eligibility.get("eligible") is True
                else "不可运行"
                if eligibility.get("eligible") is False
                else "未评估"
            )
            lines.append(
                f"\n### {pipeline.get('strategy', 'unknown')}（{eligibility_text}）"
            )
            for index, node in enumerate(pipeline.get("nodes", []), start=1):
                parameters = node.get("parameters") or {}
                parameter_text = (
                    f"；参数 `{json.dumps(parameters, ensure_ascii=False, sort_keys=True)}`"
                    if parameters
                    else ""
                )
                lines.append(
                    f"{index}. `{node.get('operator_version_id', '-')}` "
                    f"[{node.get('runtime_backend', '-')} / "
                    f"{node.get('operator_status', 'unknown')}]"
                    f"{parameter_text}"
                )

        differences: dict[str, dict[str, str]] = {}
        for pipeline in pipelines:
            strategy = str(pipeline.get("strategy", "unknown"))
            for node in pipeline.get("nodes", []):
                node_id = str(node.get("id", "-"))
                differences.setdefault(node_id, {})[strategy] = json.dumps(
                    node.get("parameters") or {},
                    ensure_ascii=False,
                    sort_keys=True,
                )
        varying = {
            node_id: values
            for node_id, values in differences.items()
            if len(set(values.values())) > 1
        }
        if varying:
            strategies = [str(item.get("strategy", "unknown")) for item in pipelines]
            lines.extend(
                [
                    "\n### 策略差异",
                    "| 节点 | " + " | ".join(strategies) + " |",
                    "|---|" + "---|" * len(strategies),
                ]
            )
            for node_id, values in varying.items():
                lines.append(
                    f"| `{node_id}` | "
                    + " | ".join(f"`{values.get(strategy, '{}')}`" for strategy in strategies)
                    + " |"
                )
        return "\n".join(lines)

    def _fallback_decision(
        self, content: str, context: dict[str, Any]
    ) -> ConversationDecision:
        normalized = content.strip().lower().strip("!！。,.，~～ ")
        if normalized in {"你好", "您好", "嗨", "hi", "hello", "hey"}:
            return ConversationDecision(reply="你好，我是 DataAgent。你可以直接问我问题或描述数据任务。")
        if any(token in normalized for token in ("怎么使用", "如何使用", "能做什么", "帮助")):
            return ConversationDecision(
                reply="你可以自然描述图片数据目标；信息不足时我会继续追问，确认后再创建和执行工单。"
            )
        if "什么模型" in normalized or "哪个模型" in normalized:
            return ConversationDecision(
                reply=(
                    f"当前普通对话由 {self.settings.fast_text_model} 处理，"
                    f"复杂规划由 {self.settings.planning_model} 处理。"
                )
            )
        if context.get("pending_requirement") and self._looks_like_path(content):
            return ConversationDecision(
                intent=ConversationIntent.PROVIDE_SOURCE,
                source=content,
                reply="我来检查这个目录并创建工单。",
            )
        if normalized in {"确认", "同意", "批准", "approve", "yes"}:
            return ConversationDecision(intent=ConversationIntent.APPROVE, reply="正在确认。")
        if normalized in {"拒绝", "不同意", "reject", "no"}:
            return ConversationDecision(intent=ConversationIntent.REJECT, reply="已记录拒绝。")
        if any(token in normalized for token in ("开始运行", "提交运行", "执行任务")):
            return ConversationDecision(intent=ConversationIntent.SUBMIT_RUN, reply="正在提交。")
        if any(token in normalized for token in ("进度", "运行状态", "怎么样了")):
            return ConversationDecision(intent=ConversationIntent.RUN_STATUS, reply="正在查询。")
        for token, action in (("暂停", "pause"), ("恢复", "resume"), ("取消", "cancel")):
            if token in normalized:
                return ConversationDecision(
                    intent=ConversationIntent.CONTROL_RUN, action=action, reply="正在处理。"
                )
        if self._looks_like_data_requirement(content):
            return ConversationDecision(
                intent=ConversationIntent.START_WORK_ORDER,
                requirement=content,
                reply="我先记录需求，再确认数据位置。",
            )
        return ConversationDecision(reply="我可以继续回答，也可以帮你创建图片数据生产任务。")

    @classmethod
    def _source_or_pending_task_decision(
        cls,
        content: str,
        context: dict[str, Any],
    ) -> ConversationDecision | None:
        source, remainder = cls._extract_source(content)
        if source:
            if remainder and cls._looks_like_data_requirement(remainder):
                return ConversationDecision(
                    intent=ConversationIntent.START_WORK_ORDER,
                    requirement=remainder,
                    source=source,
                    reply="我来检查目录并创建图片数据任务。",
                )
            return ConversationDecision(
                intent=ConversationIntent.PROVIDE_SOURCE,
                source=source,
                reply="我来检查这个目录。",
            )
        normalized = content.strip().lower()
        if context.get("pending_requirement") and any(
            token in normalized
            for token in ("创建数据处理任务", "创建任务", "开始创建", "create task")
        ):
            return ConversationDecision(
                intent=ConversationIntent.START_WORK_ORDER,
                requirement=str(context["pending_requirement"]),
                source=context.get("pending_source"),
                reply="我继续创建刚才的数据任务。",
            )
        return None

    @staticmethod
    def _pending_resolution_decision(
        content: str,
        context: dict[str, Any],
    ) -> ConversationDecision | None:
        if context.get("agent_state", {}).get("waiting") != "capability_resolution":
            return None
        normalized = content.strip().lower()
        if normalized in {"1", "重试", "重新检索", "retry"}:
            return ConversationDecision(
                intent=ConversationIntent.RESOLVE_GAP,
                action="retry",
                reply="重新检索能力候选。",
            )
        if normalized in {"2", "启用远程", "使用远程", "remote"}:
            return ConversationDecision(
                intent=ConversationIntent.RESOLVE_GAP,
                action="retry",
                runtime_backend="remote",
                reply="启用远程 Runtime 后重新检索。",
            )
        if normalized in {"3", "修改需求", "修改 taskspec", "revise"}:
            return ConversationDecision(
                intent=ConversationIntent.RESOLVE_GAP,
                action="revise_task",
                reply="返回 TaskSpec 修改阶段。",
            )
        if normalized in {"4", "终止工单", "终止任务", "terminate"}:
            return ConversationDecision(
                intent=ConversationIntent.RESOLVE_GAP,
                action="terminate",
                reply="终止当前工单。",
            )
        return None

    @classmethod
    def _extract_source(cls, content: str) -> tuple[str | None, str]:
        quoted = re.search(
            r"[\"'“”](?P<path>(?:[a-zA-Z]:[\\/]|\\\\|/)[^\"'“”\r\n]+)[\"'“”]",
            content,
        )
        if quoted:
            source = cls._normalize_source(quoted.group("path"))
            remainder = (content[: quoted.start()] + content[quoted.end() :]).strip()
            return source, remainder.lstrip("，,。.:：;；| ")

        stripped = cls._normalize_source(content)
        if cls._looks_like_path(stripped) and Path(stripped).is_dir():
            return stripped, ""

        unquoted = re.match(
            r"^(?P<path>(?:[a-zA-Z]:[\\/]|\\\\|/)\S+)(?:\s+|[，,;；|])(?P<rest>.+)$",
            content.strip(),
        )
        if unquoted:
            return (
                cls._normalize_source(unquoted.group("path")),
                unquoted.group("rest").strip(),
            )
        return None, content.strip()

    @staticmethod
    def _looks_like_data_requirement(content: str) -> bool:
        return any(
            token in content.lower()
            for token in (
                "图片",
                "照片",
                "图像",
                "数据集",
                "训练数据",
                "筛选",
                "过滤",
                "去重",
                "清洗",
                "采样",
                "标注",
                "模糊",
                "保留",
            )
        )

    @staticmethod
    def _looks_like_path(content: str) -> bool:
        normalized = ConversationService._normalize_source(content)
        return bool(re.match(r"^(?:[a-zA-Z]:[\\/]|/|\\\\)", normalized))

    @staticmethod
    def _normalize_source(content: str) -> str:
        return content.strip().strip("\"'“”").strip()

    @staticmethod
    def _normalize_strategy(strategy: str) -> str:
        return {
            "retain": "retention_first",
            "retention": "retention_first",
            "保留优先": "retention_first",
            "quality": "quality_first",
            "质量优先": "quality_first",
            "均衡": "balanced",
        }.get(strategy, strategy)

    @staticmethod
    def _turn_reply(turn: dict[str, Any], approved: bool) -> str:
        if not approved:
            return "已拒绝当前方案，工单已停止。"
        if turn["interrupts"]:
            kind = turn["interrupts"][0]["value"].get("kind")
            if kind == "pipeline_approval":
                return "TaskSpec 已确认。现在有保留优先、均衡和质量优先三条 Pipeline 等你选择。"
            if kind == "capability_resolution":
                return ConversationService._capability_resolution_reply(
                    turn["interrupts"][0]["value"]
                )
        if turn["state"].get("next_action") == "submit_dataset_run":
            return "Pipeline 已批准，SamplingPlan 已生成。你可以说“开始运行”。"
        if turn["state"].get("next_action") == "expand_retrieval":
            blocked = [
                item
                for item in turn["state"].get("operator_candidates", [])
                if not item.get("executable")
            ]
            if blocked:
                details = "；".join(
                    f"{item['provider_operator_ref']}：{item.get('blocked_reason') or '不可执行'}"
                    for item in blocked
                )
                return f"需求已确认，但匹配算子当前不可执行：{details}。"
            return "需求已确认，但当前算子目录不足以生成可执行 Pipeline。"
        if turn["state"].get("next_action") == "edit_task_spec":
            return "能力缺口尚未解决，工单已返回 TaskSpec 修改阶段。"
        return "已确认，流程继续。"

    @staticmethod
    def _capability_resolution_reply(value: dict[str, Any]) -> str:
        gaps = value.get("gaps", [])
        details = "、".join(
            f"{item.get('capability')}（{item.get('status')}）" for item in gaps
        )
        return (
            f"当前仍有能力缺口：{details}。请选择："
            "1 重新检索；2 启用远程模型后重试；3 修改 TaskSpec；4 终止工单。"
        )

    @staticmethod
    def _run_reply(run: dict[str, Any]) -> str:
        return (
            f"Run {run['id']} 当前为 {run['status']}，进度 {run['progress']}/{run['total']}，"
            f"保留 {run['kept']}，拒绝 {run['rejected']}，失败 {run['failed']}。"
        )
