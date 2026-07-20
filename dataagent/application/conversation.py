from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..config import Settings
from ..domain.common import new_id
from ..gateway import ModelGateway, ModelGatewayError
from ..infrastructure import ConversationStore
from .agent_runtime import AgentRuntime


class ConversationIntent(StrEnum):
    CHAT = "CHAT"
    START_WORK_ORDER = "START_WORK_ORDER"
    PROVIDE_SOURCE = "PROVIDE_SOURCE"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    SUBMIT_RUN = "SUBMIT_RUN"
    RUN_STATUS = "RUN_STATUS"
    CONTROL_RUN = "CONTROL_RUN"


class ConversationDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: ConversationIntent = ConversationIntent.CHAT
    reply: str = ""
    requirement: str | None = None
    source: str | None = None
    strategy: str | None = None
    action: str | None = None


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
            model=self.settings.fast_text_model if self.gateway.configured else "local-fallback",
        )
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
        fast = self._fast_decision(content)
        if fast is not None:
            return fast
        fallback = self._fallback_decision(content, context)
        if not self.gateway.configured:
            return fallback
        try:
            raw, _ = self.gateway.conversation_turn(
                history=[
                    {"role": item["role"], "content": item["content"]}
                    for item in history
                ],
                context=context,
            )
            decision = ConversationDecision.model_validate(raw)
        except (ModelGatewayError, ValueError, TypeError):
            return fallback
        if decision.intent == ConversationIntent.START_WORK_ORDER:
            requirement = decision.requirement or content
            if not self._looks_like_data_requirement(requirement):
                return decision.model_copy(update={"intent": ConversationIntent.CHAT})
        if decision.intent == ConversationIntent.PROVIDE_SOURCE and not context.get(
            "pending_requirement"
        ):
            return decision.model_copy(update={"intent": ConversationIntent.CHAT})
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
        if decision.intent in {ConversationIntent.APPROVE, ConversationIntent.REJECT}:
            turn = self.agent_runtime.state(
                work_order_id=work_order_id, owner_id=owner_id
            )
            if not turn["interrupts"]:
                base["reply"] = "当前没有等待确认的事项。"
                base["turn"] = turn
                return base
            value = turn["interrupts"][0]["value"]
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
                command["pipeline_id"] = selected["id"]
            turn = self.agent_runtime.resume(
                work_order_id=work_order_id,
                owner_id=owner_id,
                decision=command,
            )
            base["turn"] = turn
            base["reply"] = self._turn_reply(turn, approved)
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
                context["task_spec"] = {
                    "objective": task_spec.get("objective"),
                    "hard_constraints": task_spec.get("hard_constraints", {}),
                    "semantic_requirements": task_spec.get("semantic_requirements", []),
                    "exclusion_requirements": task_spec.get("exclusion_requirements", []),
                    "confirmed": task_spec.get("confirmed", False),
                }
            if pipelines := turn["state"].get("representative_pipelines"):
                context["pipeline_choices"] = [
                    {
                        "id": item.get("id"),
                        "strategy": item.get("strategy"),
                        "version": item.get("version"),
                        "node_count": len(item.get("nodes", [])),
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
        return "已确认，流程继续。"

    @staticmethod
    def _run_reply(run: dict[str, Any]) -> str:
        return (
            f"Run {run['id']} 当前为 {run['status']}，进度 {run['progress']}/{run['total']}，"
            f"保留 {run['kept']}，拒绝 {run['rejected']}，失败 {run['failed']}。"
        )
