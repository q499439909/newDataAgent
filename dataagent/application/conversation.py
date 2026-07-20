from __future__ import annotations

import json
import hashlib
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
    QUERY_CONTROL_FACTS = "QUERY_CONTROL_FACTS"
    RESOLVE_GAP = "RESOLVE_GAP"
    RESELECT_PIPELINE = "RESELECT_PIPELINE"


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
    confirm_after_edit: bool = False
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
        revision_decision = self._confirmed_task_revision_decision(content, context)
        if revision_decision is not None:
            return revision_decision
        clarification_decision = self._pending_clarification_decision(content, context)
        if clarification_decision is not None:
            return clarification_decision
        pipeline_decision = self._pipeline_details_decision(content, context)
        if pipeline_decision is not None:
            return pipeline_decision
        fact_decision = self._control_fact_query_decision(content, context)
        if fact_decision is not None:
            return fact_decision
        approval_decision = self._pending_pipeline_approval_decision(content, context)
        if approval_decision is not None:
            return approval_decision
        reselection_decision = self._pipeline_reselection_decision(content, context)
        if reselection_decision is not None:
            return reselection_decision
        submission_decision = self._pending_run_submission_decision(content, context)
        if submission_decision is not None:
            return submission_decision
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
        ungrounded_ids = self._ungrounded_control_identifiers(decision.reply, context)
        if decision.intent == ConversationIntent.CHAT and ungrounded_ids:
            reason = "ungrounded_control_identifiers:" + ",".join(ungrounded_ids)
            logger.warning("Blocked ungrounded control-plane identifiers: %s", reason)
            return ConversationDecision(
                intent=ConversationIntent.CHAT,
                reply=(
                    "模型回答包含无法由控制面验证的任务标识，已阻止展示。"
                    "请明确要查看当前 Run、Pipeline、算子、TaskSpec 或工单信息。"
                ),
                resolved_by="control-fact-guard",
                fallback_reason=reason,
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

    @staticmethod
    def _ungrounded_control_identifiers(
        reply: str, context: dict[str, Any]
    ) -> tuple[str, ...]:
        identifiers = set(
            re.findall(
                r"\b(?:run|pipeline_version|work_order|spec)_[A-Za-z0-9]+\b",
                reply,
                flags=re.IGNORECASE,
            )
        )
        if not identifiers:
            return ()
        grounded_context = json.dumps(context, ensure_ascii=False, sort_keys=True)
        return tuple(sorted(item for item in identifiers if item not in grounded_context))

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
        if normalized in {"好", "好的", "知道了", "明白了", "收到"}:
            return ConversationDecision(reply="好的。")
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
        if decision.intent == ConversationIntent.QUERY_CONTROL_FACTS:
            if "run" in (decision.action or "").split(","):
                base["run"] = self._current_run(work_order_id, owner_id, context)
            return base
        if decision.intent == ConversationIntent.EDIT_TASK_SPEC:
            turn = self.agent_runtime.state(
                work_order_id=work_order_id, owner_id=owner_id
            )
            patch = decision.task_spec_patch or {
                "semantic_requirements": [content]
            }
            waiting_for_spec = bool(
                turn["interrupts"]
                and turn["interrupts"][0]["value"].get("kind")
                == "task_spec_confirmation"
            )
            if waiting_for_spec:
                turn = self.agent_runtime.resume(
                    work_order_id=work_order_id,
                    owner_id=owner_id,
                    decision={
                        "action": "edit_spec",
                        "task_spec_patch": patch,
                        "channel": "conversation",
                    },
                )
            elif turn["state"].get("task_spec", {}).get("confirmed"):
                turn = self.agent_runtime.revise_task_spec(
                    work_order_id=work_order_id,
                    owner_id=owner_id,
                    patch=patch,
                )
                context.pop("active_run_id", None)
                self.store.update(
                    thread_id=thread["id"], owner_id=owner_id, context=context
                )
            else:
                base["reply"] = "当前没有可以修改的 TaskSpec。"
                base["turn"] = turn
                return base
            revised_spec = turn["state"]["task_spec"]
            if decision.confirm_after_edit and not revised_spec.get("ambiguities"):
                turn = self.agent_runtime.resume(
                    work_order_id=work_order_id,
                    owner_id=owner_id,
                    decision={"approved": True, "channel": "conversation"},
                )
                base["turn"] = turn
                base["reply"] = (
                    "已采纳剩余推荐值并确认 TaskSpec。"
                    + self._turn_reply(turn, True)
                )
                return base
            base["turn"] = turn
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
                        f"{strategy} Pipeline 未获批准，工单仍停留在方案选择阶段。"
                        "阻塞原因："
                        + "；".join(str(item) for item in violations)
                    )
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
        if decision.intent == ConversationIntent.RESELECT_PIPELINE:
            strategy = self._normalize_strategy(decision.strategy or "")
            turn = self.agent_runtime.reselect_pipeline(
                work_order_id=work_order_id,
                owner_id=owner_id,
                strategy=strategy,
            )
            context.pop("active_run_id", None)
            self.store.update(
                thread_id=thread["id"], owner_id=owner_id, context=context
            )
            base["turn"] = turn
            base["reply"] = (
                f"已切换并批准 {strategy} Pipeline。旧 Run 保留为历史记录；"
                "你可以说“开始运行”提交一个新 Run。"
            )
            return base
        if decision.intent == ConversationIntent.SUBMIT_RUN:
            turn = self.agent_runtime.state(
                work_order_id=work_order_id, owner_id=owner_id
            )
            state = turn["state"]
            identity = ":".join(
                (
                    thread["id"],
                    str(state.get("selected_pipeline_id", "")),
                    str(state.get("task_spec", {}).get("id", "")),
                )
            )
            run = self.agent_runtime.submit_dataset_run(
                work_order_id=work_order_id,
                owner_id=owner_id,
                idempotency_key=(
                    "conversation-run-"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
                ),
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
            if approved_pipeline := turn["state"].get("approved_pipeline"):
                context["approved_pipeline"] = approved_pipeline
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
                try:
                    context["latest_run_pipeline"] = (
                        self.agent_runtime.pipeline_version(
                            pipeline_version_id=runs[0]["pipeline_version_id"],
                            owner_id=owner_id,
                        )
                    )
                except (KeyError, RuntimeError):
                    logger.warning(
                        "Pipeline version %s for Run %s could not be loaded",
                        runs[0].get("pipeline_version_id"),
                        runs[0].get("id"),
                    )
                dataset_version_id = runs[0].get("dataset_version_id")
                if dataset_version_id:
                    try:
                        dataset = self.agent_runtime.get_dataset(
                            dataset_version_id=dataset_version_id,
                            owner_id=owner_id,
                        )
                        context["latest_dataset"] = self._dataset_control_summary(
                            dataset
                        )
                    except (KeyError, RuntimeError, ValueError) as exc:
                        context["dataset_lookup_error"] = str(exc)
        return context

    @staticmethod
    def _dataset_control_summary(dataset: dict[str, Any]) -> dict[str, Any]:
        manifest = Path(str(dataset.get("manifest_uri", "")))
        assets = list(dataset.get("assets") or [])
        kept_assets = [item for item in assets if item.get("decision") == "keep"]
        class_counts: dict[str, int] = {}
        authenticity_counts: dict[str, int] = {}
        output_directories: dict[str, int] = {}
        missing_outputs: list[str] = []
        empty_semantic_outputs = 0
        for asset in kept_assets:
            labels = asset.get("labels") or {}
            resolved_class = str(labels.get("resolved_class") or "unclassified")
            class_counts[resolved_class] = class_counts.get(resolved_class, 0) + 1
            authenticity = str(labels.get("authenticity") or "unclassified")
            authenticity_counts[authenticity] = (
                authenticity_counts.get(authenticity, 0) + 1
            )
            provider_output = labels.get("datajuicer_output")
            if not provider_output:
                empty_semantic_outputs += 1
            output_uri = asset.get("output_uri")
            if output_uri:
                output = Path(str(output_uri))
                if output.is_file():
                    directory = str(output.parent.resolve())
                    output_directories[directory] = output_directories.get(directory, 0) + 1
                else:
                    missing_outputs.append(str(output))
            else:
                missing_outputs.append(str(asset.get("source_uri") or "unknown"))
        return {
            "id": dataset.get("id"),
            "manifest_uri": str(manifest),
            "dataset_root": str(manifest.parent.resolve()) if str(manifest) else None,
            "manifest_exists": manifest.is_file(),
            "source_count": dataset.get("source_count", len(assets)),
            "kept_count": dataset.get("kept_count", len(kept_assets)),
            "rejected_count": dataset.get("rejected_count", 0),
            "failed_count": dataset.get("failed_count", 0),
            "original_files_unchanged": dataset.get("original_files_unchanged"),
            "class_counts": class_counts,
            "authenticity_counts": authenticity_counts,
            "output_directories": output_directories,
            "missing_output_count": len(missing_outputs),
            "missing_output_examples": missing_outputs[:5],
            "empty_semantic_output_count": empty_semantic_outputs,
            "materialized": manifest.is_file() and not missing_outputs,
        }

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
    def _control_fact_query_decision(
        content: str, context: dict[str, Any]
    ) -> ConversationDecision | None:
        normalized = content.strip().lower().replace(" ", "")
        query_markers = (
            "什么",
            "哪个",
            "哪些",
            "哪条",
            "哪一个",
            "在哪",
            "哪里",
            "多少",
            "用的",
            "用了",
            "当前",
            "现在",
            "查看",
            "展示",
            "告诉我",
            "状态",
            "进度",
            "情况",
            "详情",
            "怎么样",
            "为什么",
            "为何",
            "到哪",
        )
        contradicts_output = "没有" in normalized and any(
            token in normalized for token in ("文件夹", "目录", "路径", "文件")
        )
        is_question = (
            any(marker in normalized for marker in query_markers)
            or normalized in {"完整路径", "输出路径", "结果路径"}
            or contradicts_output
            or (
            normalized.endswith(("?", "？", "吗", "呢"))
            )
        )
        if not is_question:
            return None

        asks_pipeline = any(
            token in normalized for token in ("pipeline", "流水线", "方案")
        )
        asks_operators = any(
            token in normalized for token in ("算子", "节点", "算子顺序")
        )
        asks_task_spec = any(
            token in normalized
            for token in (
                "taskspec",
                "任务规格",
                "需求草案",
                "数据源",
                "约束",
                "需求是什么",
                "任务是什么",
                "任务目标",
            )
        )
        asks_work_order = "工单" in normalized and any(
            token in normalized for token in ("id", "编号", "哪个", "当前")
        )
        asks_run = any(token in normalized for token in ("进度", "状态")) or any(
            token in normalized
            for token in (
                "运行情况",
                "run情况",
                "运行怎么样",
                "run怎么样",
                "跑到哪",
                "运行到哪",
                "run到哪",
                "在运行吗",
                "在跑吗",
                "完成了吗",
                "跑了多少",
                "处理了多少",
            )
        )
        asks_dataset = any(
            token in normalized
            for token in (
                "输出",
                "结果",
                "数据集",
                "dataset",
                "manifest",
                "清单",
                "完整路径",
                "文件夹",
                "发布到",
                "存到",
                "分类后",
            )
        ) or contradicts_output
        asks_outcome_explanation = any(
            token in normalized for token in ("为什么", "为何", "怎么会")
        ) and any(
            token in normalized
            for token in ("保留", "拒绝", "分类", "unknown", "未知")
        )
        if not any(
            (
                asks_pipeline,
                asks_operators,
                asks_task_spec,
                asks_work_order,
                asks_run,
                asks_dataset,
                asks_outcome_explanation,
            )
        ):
            return None

        lines: list[str] = []
        latest_run = context.get("latest_run") or {}
        approved_pipeline = context.get("approved_pipeline") or {}
        run_pipeline = context.get("latest_run_pipeline") or {}
        active_statuses = {
            "QUEUED",
            "RUNNING",
            "PAUSING",
            "PAUSED",
            "CANCELLING",
            "EVALUATING",
        }
        pipeline = (
            run_pipeline
            if asks_dataset
            or asks_outcome_explanation
            or latest_run.get("status") in active_statuses
            or any(token in normalized for token in ("run", "运行", "跑"))
            else approved_pipeline or run_pipeline
        )

        if asks_work_order:
            lines.append(f"当前工单：`{context.get('work_order_id', '-')}`。")
        if asks_run:
            if latest_run:
                lines.append(ConversationService._run_reply(latest_run))
            else:
                lines.append("当前工单还没有 Run。")
        if asks_pipeline:
            if pipeline:
                run_prefix = (
                    f"Run `{latest_run.get('id')}` 使用"
                    if pipeline is run_pipeline and latest_run
                    else "当前已批准"
                )
                lines.append(
                    f"{run_prefix} `{pipeline.get('strategy', 'unknown')}` Pipeline："
                    f"`{pipeline.get('id', '-')}`。"
                )
            else:
                lines.append("当前还没有已编译或已批准的 Pipeline。")
        if asks_operators:
            if pipeline:
                lines.append("实际算子顺序：")
                for index, node in enumerate(pipeline.get("nodes", []), start=1):
                    lines.append(
                        f"{index}. `{node.get('id', '-')}` → "
                        f"`{node.get('operator_version_id', '-')}` "
                        f"[{node.get('runtime_backend', '-')} / "
                        f"{node.get('operator_status', 'unknown')}]"
                    )
            else:
                lines.append("当前没有可展示的实际算子流水线。")
        if asks_task_spec:
            task_spec = context.get("task_spec")
            lines.append(
                ConversationService._task_spec_details_reply(task_spec)
                if task_spec
                else "当前还没有 TaskSpec。"
            )
        if asks_dataset or asks_outcome_explanation:
            lines.append(
                ConversationService._dataset_result_reply(
                    context.get("latest_dataset"),
                    pipeline=pipeline,
                    explain=asks_outcome_explanation,
                    lookup_error=context.get("dataset_lookup_error"),
                )
            )
        facets = [
            name
            for name, requested in (
                ("work_order", asks_work_order),
                ("run", asks_run),
                ("pipeline", asks_pipeline),
                ("operators", asks_operators),
                ("task_spec", asks_task_spec),
                ("dataset", asks_dataset or asks_outcome_explanation),
            )
            if requested
        ]
        return ConversationDecision(
            intent=ConversationIntent.QUERY_CONTROL_FACTS,
            reply="\n".join(lines),
            action=",".join(facets),
        )

    @staticmethod
    def _dataset_result_reply(
        dataset: dict[str, Any] | None,
        *,
        pipeline: dict[str, Any],
        explain: bool,
        lookup_error: str | None = None,
    ) -> str:
        if not dataset:
            if lookup_error:
                return f"Run 引用了数据集，但读取 DatasetVersion 失败：{lookup_error}。"
            return "当前 Run 还没有已发布的 DatasetVersion。"
        lines = [
            f"数据集：`{dataset.get('id', '-')}`",
            f"实际发布根目录：`{dataset.get('dataset_root', '-')}`",
            f"Manifest：`{dataset.get('manifest_uri', '-')}`",
            (
                "物理发布校验：通过。"
                if dataset.get("materialized")
                else "物理发布校验：未通过，Manifest 或部分输出文件缺失。"
            ),
            (
                f"计数：源文件 {dataset.get('source_count', 0)}，"
                f"保留 {dataset.get('kept_count', 0)}，"
                f"拒绝 {dataset.get('rejected_count', 0)}，"
                f"失败 {dataset.get('failed_count', 0)}。"
            ),
        ]
        class_counts = dataset.get("class_counts") or {}
        lines.append(
            "实际分类统计："
            + (
                "，".join(f"{name}={count}" for name, count in sorted(class_counts.items()))
                if class_counts
                else "无"
            )
            + "。"
        )
        directories = dataset.get("output_directories") or {}
        if directories:
            lines.append("实际存在的输出目录：")
            lines.extend(
                f"- `{directory}`：{count} 个文件"
                for directory, count in sorted(directories.items())
            )
        if dataset.get("missing_output_count"):
            lines.append(
                f"缺失输出：{dataset['missing_output_count']} 个；示例："
                + "，".join(dataset.get("missing_output_examples") or [])
            )
        empty_semantic = int(dataset.get("empty_semantic_output_count") or 0)
        if empty_semantic:
            lines.append(
                f"语义结果警告：{empty_semantic} 个保留资产没有 VLM 标签输出；"
                "该数据集已物理发布，但不能视为完成了真实性判断和猫狗分类。"
            )
        if explain:
            policies = {
                node.get("id"): node.get("parameters") or {}
                for node in pipeline.get("nodes", [])
            }
            lines.append(
                "保留原因：VLM 标签为空后，真实性被解析为 uncertain、类别被解析为 unknown；"
                f"当前策略参数为 uncertain={policies.get('authenticity_decision', {}).get('uncertain_policy', '-')}, "
                f"unknown={policies.get('class_resolution', {}).get('unknown_policy', '-')}。"
            )
        return "\n".join(lines)

    @staticmethod
    def _confirmed_task_revision_decision(
        content: str, context: dict[str, Any]
    ) -> ConversationDecision | None:
        task_spec = context.get("task_spec") or {}
        if not task_spec.get("confirmed"):
            return None
        normalized = content.strip().lower().replace(" ", "")
        remove_quality = any(
            token in normalized
            for token in (
                "不筛选清晰度",
                "不要筛选清晰度",
                "取消清晰度筛选",
                "去掉清晰度筛选",
                "不检查清晰度",
                "不要清晰度",
            )
        )
        if not remove_quality:
            return None
        return ConversationDecision(
            intent=ConversationIntent.EDIT_TASK_SPEC,
            reply="正在生成不包含清晰度过滤的新 TaskSpec 版本。",
            task_spec_patch={
                "hard_constraints": {
                    "disabled_capabilities": ["image_quality"]
                }
            },
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
        review_defaults = {
            "按推荐默认值",
            "按默认值",
            "使用默认值",
            "采用默认值",
            "按建议",
            "用推荐值",
        }
        continue_with_defaults = {
            "好",
            "好的",
            "可以",
            "继续",
            "确认",
            "同意",
            "是",
            "yes",
            "ok",
        }
        if normalized not in review_defaults | continue_with_defaults:
            return ConversationService._clarification_answer_decision(
                content, ambiguities
            )
        return ConversationDecision(
            intent=ConversationIntent.EDIT_TASK_SPEC,
            reply="正在将推荐默认值写入 TaskSpec。",
            task_spec_patch=recommended_clarification_patch(ambiguities),
            confirm_after_edit=normalized in continue_with_defaults,
        )

    @staticmethod
    def _clarification_answer_decision(
        content: str, ambiguities: tuple[str, ...]
    ) -> ConversationDecision | None:
        normalized = content.strip().lower()
        if normalized.endswith(("?", "？")) or normalized.startswith(
            ("为什么", "怎么", "如何", "什么", "能不能", "是否可以")
        ):
            return None

        accepts_remaining_defaults = bool(
            re.search(
                r"(?:^|[。.!！,，;；\s])(?:好|好的|可以|同意)[。.!！,，;；\s]*$",
                normalized,
            )
        )
        patch: dict[str, Any] = {
            "hard_constraints": {},
            "preferences": {},
            "semantic_requirements": [],
            "exclusion_requirements": [],
        }
        exclusion_labels = {
            "ai生成": "排除 AI 生成图片",
            "插画": "排除插画",
            "截图": "排除截图",
            "明显合成": "排除明显合成图片",
            "美颜": "排除重度美颜图片",
            "滤镜": "排除重度滤镜图片",
            "后期调色": "排除明显后期调色图片",
        }
        exclusions = [
            label for token, label in exclusion_labels.items() if token in normalized
        ]
        if exclusions:
            patch["hard_constraints"]["authenticity_scope"] = "；".join(exclusions)
            patch["exclusion_requirements"].extend(exclusions)

        preferences = patch["preferences"]
        if "复核" in normalized:
            preferences.update({"mixed_policy": "review", "unknown_policy": "review"})
        elif "猫狗都有" in normalized:
            preferences["mixed_policy"] = "keep"
        if any(token in normalized for token in ("输出到", "分别输出", "文件夹", "目录")):
            preferences["output_layout"] = "versioned_class_directories"
        if any(
            token in normalized
            for token in ("源目录只读", "保持源目录", "不修改源", "复制到")
        ):
            patch["hard_constraints"]["preserve_source"] = True

        has_explicit_answer = any(
            bool(patch[key])
            for key in (
                "hard_constraints",
                "preferences",
                "semantic_requirements",
                "exclusion_requirements",
            )
        )
        if not has_explicit_answer:
            return None

        if accepts_remaining_defaults:
            defaults = recommended_clarification_patch(ambiguities)
            for key in ("hard_constraints", "preferences"):
                merged = dict(defaults.get(key) or {})
                merged.update(patch[key])
                patch[key] = merged
            for key in ("semantic_requirements", "exclusion_requirements"):
                patch[key] = [*(defaults.get(key) or []), *patch[key]]

        return ConversationDecision(
            intent=ConversationIntent.EDIT_TASK_SPEC,
            reply="正在把你的澄清写入 TaskSpec。",
            task_spec_patch=patch,
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
        compares_choices = (
            context.get("agent_state", {}).get("waiting") == "pipeline_approval"
            or any(
                token in normalized
                for token in ("三个", "各个", "分别", "候选", "可选", "每条")
            )
            or not (
                context.get("approved_pipeline") or context.get("latest_run_pipeline")
            )
        )
        if not compares_choices:
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

    @staticmethod
    def _pipeline_reselection_decision(
        content: str, context: dict[str, Any]
    ) -> ConversationDecision | None:
        latest_run = context.get("latest_run") or {}
        if latest_run.get("status") not in {"FAILED", "SUCCEEDED", "CANCELLED"}:
            return None
        normalized = content.strip().lower().strip("!！。,.，~～ ")
        strategies = {
            "保留优先": "retention_first",
            "均衡": "balanced",
            "质量优先": "quality_first",
            "retention_first": "retention_first",
            "balanced": "balanced",
            "quality_first": "quality_first",
        }
        if normalized in strategies:
            return ConversationDecision(
                intent=ConversationIntent.RESELECT_PIPELINE,
                strategy=strategies[normalized],
                reply="正在生成新的已批准 Pipeline 版本。",
            )
        asks_to_switch = any(token in normalized for token in ("换", "重选", "重新选")) and any(
            token in normalized for token in ("pipeline", "方案", "流水线")
        )
        if not asks_to_switch:
            return None
        available = [
            str(item.get("strategy"))
            for item in context.get("pipeline_choices", [])
            if (item.get("execution_eligibility") or {}).get("eligible") is not False
        ]
        return ConversationDecision(
            intent=ConversationIntent.CHAT,
            reply=(
                "可以重新选择 Pipeline。请选择保留优先、均衡或质量优先；"
                f"当前可选策略：{', '.join(available) or '无'}。"
                "选择后我会先生成新的批准版本，不会直接复用旧 Run。"
            ),
        )

    @staticmethod
    def _pending_pipeline_approval_decision(
        content: str, context: dict[str, Any]
    ) -> ConversationDecision | None:
        if context.get("agent_state", {}).get("waiting") != "pipeline_approval":
            return None
        normalized = content.strip().lower().replace(" ", "")
        strategies = {
            "保留优先": "retention_first",
            "均衡": "balanced",
            "质量优先": "quality_first",
            "retention_first": "retention_first",
            "balanced": "balanced",
            "quality_first": "quality_first",
        }
        matches = [
            strategy for label, strategy in strategies.items() if label in normalized
        ]
        if matches:
            return ConversationDecision(
                intent=ConversationIntent.APPROVE,
                strategy=matches[0],
                reply=f"正在批准 {matches[0]} Pipeline。",
            )
        if any(token in normalized for token in ("运行", "执行", "开始", "提交")):
            return ConversationDecision(
                intent=ConversationIntent.CHAT,
                reply=(
                    "当前还在 Pipeline 选择阶段。请先选择保留优先、均衡或质量优先；"
                    "批准后再说“开始运行”。"
                ),
            )
        return None

    @staticmethod
    def _pending_run_submission_decision(
        content: str, context: dict[str, Any]
    ) -> ConversationDecision | None:
        agent_state = context.get("agent_state", {})
        if agent_state.get("waiting") is not None or agent_state.get(
            "next_action"
        ) != "submit_dataset_run":
            return None
        normalized = content.strip().lower().replace(" ", "")
        if not any(
            token in normalized
            for token in ("开始运行", "提交运行", "运行", "执行任务", "开始执行")
        ):
            return None
        return ConversationDecision(
            intent=ConversationIntent.SUBMIT_RUN,
            reply="正在提交新的 Run。",
        )

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

        compact = content.strip()
        if cls._looks_like_path(compact):
            for end in range(len(compact) - 1, 2, -1):
                source = cls._normalize_source(compact[:end])
                remainder = compact[end:].lstrip("，,。.:：;；| ")
                if (
                    remainder
                    and cls._looks_like_data_requirement(remainder)
                    and Path(source).is_dir()
                ):
                    return source, remainder

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
