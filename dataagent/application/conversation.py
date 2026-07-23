from __future__ import annotations

import json
import hashlib
import logging
from pathlib import Path
from typing import Any

from ..config import Settings
from ..agents.requirement.clarification import recommended_clarification_patch
from ..domain.common import new_id
from ..gateway import ModelGateway, ModelGatewayError
from ..infrastructure import ConversationStore
from .agent_runtime import AgentRuntime
from .conversation_actions import (
    ChatAction,
    ConversationAction,
    ConversationActionError,
    ConversationIntent,
    parse_conversation_action,
)
from .conversation_policy import (
    allowed_conversation_actions,
    validate_conversation_action,
)


logger = logging.getLogger(__name__)


# Maximum ReAct iterations within a single user turn. The model gets up to
# this many chances to correct itself after a control-plane action fails (e.g.
# an invalid data-source path) before the last reply is shown to the user.
_MAX_REACT_ITERATIONS = 4


class ConversationService:
    """Conversational control plane.

    The model is the *primary* decider: every user message is sent to the
    gateway with the current control-plane context, and the model selects an
    intent plus structured arguments (including the data-source ``source``
    path and the ``requirement`` text). A bounded ReAct loop feeds
    control-plane failures back to the model so it can self-correct or ask
    the user a precise follow-up within the same turn.
    """

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
        # ReAct working history: seeded from persisted messages, extended in
        # memory with intermediate assistant + system-feedback turns. Only the
        # original user message and the final assistant reply are persisted.
        conversation_history = [
            {"role": item["role"], "content": item["content"]} for item in history
        ]
        context = self._control_context(thread, owner_id)
        context["execution_request_id"] = (
            f"{thread_id}:{history[-1]['sequence']}"
        )

        current_content = content
        decision: ConversationAction | None = None
        response: dict[str, Any] = {"reply": "我在。", "turn": None, "run": None}
        for iteration in range(_MAX_REACT_ITERATIONS):
            try:
                decision = self._decide(current_content, conversation_history, context)
            except ConversationActionError as exc:
                feedback = json.dumps(
                    {
                        "code": "INVALID_ACTION_CONTRACT",
                        "message": str(exc),
                        "details": exc.details,
                        "allowed_actions": self._allowed_actions(context),
                    },
                    ensure_ascii=False,
                    default=str,
                )
                if iteration == _MAX_REACT_ITERATIONS - 1:
                    decision = ChatAction(
                        reply=(
                            "我没有形成可安全执行的操作，因此没有修改任务。"
                            "请明确说明是确认当前 TaskSpec，还是要修改哪一项。"
                        )
                    ).with_resolution(resolved_by="action-contract-guard")
                    response = {"reply": decision.reply, "turn": None, "run": None}
                    break
                conversation_history.append(
                    {"role": "control", "content": feedback}
                )
                continue
            violation = validate_conversation_action(decision, context)
            if violation is None and decision.intent in {
                ConversationIntent.START_WORK_ORDER,
                ConversationIntent.PROVIDE_SOURCE,
            }:
                source = getattr(decision, "source", None)
                if source and not self._source_is_user_grounded(source, conversation_history):
                    feedback = json.dumps(
                        {
                            "code": "UNGROUNDED_SOURCE_PATH",
                            "message": (
                                "The proposed source path was not supplied by the user. "
                                "Ask the user for the correct directory instead of guessing it."
                            ),
                            "source": source,
                            "allowed_actions": self._allowed_actions(context),
                        },
                        ensure_ascii=False,
                    )
                    if iteration == _MAX_REACT_ITERATIONS - 1:
                        decision = ChatAction(
                            reply=(
                                "我不能猜测本地图片目录，因此没有创建任务。"
                                "请提供一个可访问的图片目录。"
                            )
                        ).with_resolution(resolved_by="source-grounding-guard")
                        response = {"reply": decision.reply, "turn": None, "run": None}
                        break
                    conversation_history.append(
                        {"role": "control", "content": feedback}
                    )
                    continue
            if violation is not None:
                feedback = json.dumps(
                    {
                        "code": violation.code,
                        "message": violation.message,
                        "allowed_actions": violation.allowed_actions,
                    },
                    ensure_ascii=False,
                )
                if iteration == _MAX_REACT_ITERATIONS - 1:
                    decision = ChatAction(
                        reply=(
                            "当前操作与任务状态不一致，因此没有修改任务。"
                            "请根据当前待确认事项重新说明你的选择。"
                        )
                    ).with_resolution(resolved_by="action-policy-guard")
                    response = {"reply": decision.reply, "turn": None, "run": None}
                    break
                conversation_history.append(
                    {"role": "control", "content": feedback}
                )
                continue
            try:
                response = self._apply(
                    thread=thread,
                    owner_id=owner_id,
                    content=current_content,
                    decision=decision,
                    control_context=context,
                )
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "Conversation action %s was rejected during execution: %s",
                    decision.intent,
                    exc,
                )
                response = {
                    "reply": (
                        "控制面拒绝了这次操作，因此没有自动重试，也没有重复提交。"
                        f"当前可执行动作：{', '.join(self._allowed_actions(context))}。"
                        f"拒绝原因：{exc}"
                    ),
                    "turn": None,
                    "run": None,
                }
                break
            feedback = response.pop("_react_feedback", None)
            if not feedback or iteration == _MAX_REACT_ITERATIONS - 1:
                break
            # Feed the control-plane failure back to the model so it can
            # self-correct or ask the user a precise follow-up.
            conversation_history.append(
                {"role": "assistant", "content": decision.reply or response.get("reply", "")}
            )
            feedback_message = json.dumps(
                {"code": "CONTROL_PREFLIGHT_FAILED", "message": feedback},
                ensure_ascii=False,
            )
            conversation_history.append({"role": "control", "content": feedback_message})
            # State may have changed (e.g. context popped); refresh both.
            thread = self.store.get(thread_id, owner_id)
            context = self._control_context(thread, owner_id)

        resolved_by = (decision or ChatAction()).resolved_by
        self.store.add_message(
            thread_id=thread_id,
            owner_id=owner_id,
            role="assistant",
            content=response["reply"],
            intent=(decision.intent if decision else ConversationIntent.CHAT),
            model=resolved_by,
        )
        if decision is not None and decision.fallback_reason:
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

    @staticmethod
    def _allowed_actions(context: dict[str, Any]) -> tuple[str, ...]:
        return allowed_conversation_actions(context)

    @staticmethod
    def _source_is_user_grounded(
        source: str, history: list[dict[str, Any]]
    ) -> bool:
        candidate = source.strip().strip("\"'“”")
        if not candidate:
            return False
        return any(
            candidate in str(item.get("content", ""))
            for item in history
            if item.get("role") == "user"
        )

    def _decide(
        self,
        content: str,
        history: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> ConversationAction:
        """Ask the model to decide intent + structured arguments.

        The model is consulted first; there is no keyword cascade that
        pre-empts it. Paths and requirements are extracted by the model as
        structured ``source`` / ``requirement`` fields, so quoted,
        Chinese-adjacent, or multi-path inputs are handled naturally.
        """
        fallback = self._fallback_decision(content)
        if not self.gateway.configured:
            return fallback.with_resolution(
                resolved_by="local-fallback",
                fallback_reason="model_gateway_not_configured",
            )
        try:
            raw, _ = self.gateway.conversation_turn(
                history=[
                    {"role": item["role"], "content": item["content"]}
                    for item in history
                ],
                context=context,
            )
            decision = parse_conversation_action(raw).with_resolution(
                resolved_by=self.settings.fast_text_model
            )
        except ConversationActionError:
            raise
        except (ModelGatewayError, ValueError, TypeError) as exc:
            reason = f"{type(exc).__name__}: {exc}"[:500]
            logger.warning(
                "Conversation model failed; returning a non-mutating fallback: %s",
                reason,
            )
            return ChatAction(
                reply=(
                    "模型服务本轮未返回有效结果，控制平面没有执行任何操作。"
                    "请重试刚才的问题。"
                )
            ).with_resolution(
                resolved_by="local-fallback",
                fallback_reason=reason,
            )
        ungrounded_ids = self._ungrounded_control_identifiers(decision.reply, context)
        if decision.intent == ConversationIntent.CHAT and ungrounded_ids:
            reason = "ungrounded_control_identifiers:" + ",".join(ungrounded_ids)
            logger.warning("Blocked ungrounded control-plane identifiers: %s", reason)
            return ChatAction(
                reply=(
                    "模型回答包含无法由控制面验证的任务标识，已阻止展示。"
                    "请明确要查看当前 Run、Pipeline、算子、TaskSpec 或工单信息。"
                )
            ).with_resolution(
                resolved_by="control-fact-guard",
                fallback_reason=reason,
            )
        return decision

    def _apply(
        self,
        *,
        thread: dict[str, Any],
        owner_id: str,
        content: str,
        decision: ConversationAction,
        control_context: dict[str, Any] | None = None,
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
            if decision.task_spec_patch:
                context["pending_task_spec_patch"] = decision.task_spec_patch
            return self._maybe_start(thread, owner_id, context, base)
        if decision.intent == ConversationIntent.PROVIDE_SOURCE:
            context["pending_source"] = decision.source or content
            return self._maybe_start(thread, owner_id, context, base)
        if not work_order_id:
            base["reply"] = "当前还没有工单。请先告诉我需要生产什么图片数据。"
            return base
        if decision.intent == ConversationIntent.QUERY_CONTROL_FACTS:
            facts_context = control_context or context
            facets = set(decision.facets)
            facts_reply = ConversationService._control_facts_reply(facts_context, facets)
            if facts_reply:
                base["reply"] = facts_reply
            if "run" in facets:
                try:
                    base["run"] = self._current_run(work_order_id, owner_id, context)
                except (KeyError, ValueError):
                    pass
            return base
        if decision.intent == ConversationIntent.EDIT_TASK_SPEC:
            turn = self.agent_runtime.state(
                work_order_id=work_order_id, owner_id=owner_id
            )
            waiting_for_spec = bool(
                turn["interrupts"]
                and turn["interrupts"][0]["value"].get("kind")
                == "task_spec_confirmation"
            )
            if waiting_for_spec:
                interrupt_value = turn["interrupts"][0]["value"]
                interrupt_spec = (
                    interrupt_value.get("task_spec")
                    or turn["state"].get("task_spec")
                    or {}
                )
                ambiguities = tuple(interrupt_spec.get("ambiguities") or ())
                if decision.action == "accept_defaults":
                    patch = recommended_clarification_patch(ambiguities)
                else:
                    patch = decision.task_spec_patch
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
                patch = decision.task_spec_patch
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
                command["reason"] = decision.reason or content
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
        if decision.intent == ConversationIntent.SELECT_PIPELINE:
            strategy = decision.strategy
            turn = self.agent_runtime.state(
                work_order_id=work_order_id, owner_id=owner_id
            )
            if turn["interrupts"] and turn["interrupts"][0]["value"].get(
                "kind"
            ) == "pipeline_approval":
                value = turn["interrupts"][0]["value"]
                selected = next(
                    (
                        item
                        for item in value.get("pipelines", [])
                        if item.get("strategy") == strategy
                    ),
                    None,
                )
                if selected is None:
                    base["reply"] = "没有找到对应策略，请重新选择 Pipeline。"
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
                turn = self.agent_runtime.resume(
                    work_order_id=work_order_id,
                    owner_id=owner_id,
                    decision={
                        "approved": True,
                        "channel": "conversation",
                        "pipeline_id": selected["id"],
                    },
                )
                base["turn"] = turn
                base["reply"] = self._turn_reply(turn, True)
                return base
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
        if decision.intent == ConversationIntent.RECOMPILE_PIPELINE:
            turn = self.agent_runtime.recompile_pipeline_candidates(
                work_order_id=work_order_id,
                owner_id=owner_id,
            )
            context.pop("active_run_id", None)
            self.store.update(
                thread_id=thread["id"], owner_id=owner_id, context=context
            )
            base["turn"] = turn
            base["reply"] = (
                "已沿用当前确认的 TaskSpec，并使用最新 Catalog 重新检索和编译。"
                + self._turn_reply(turn, True)
            )
            return base
        if decision.intent in {
            ConversationIntent.RETRY_RUN,
            ConversationIntent.RERUN_PIPELINE,
        }:
            previous_run = self._current_run(work_order_id, owner_id, context)
            turn = self.agent_runtime.state(
                work_order_id=work_order_id, owner_id=owner_id
            )
            state = turn["state"]
            if state.get("selected_pipeline_id") != previous_run.get(
                "pipeline_version_id"
            ):
                raise ValueError(
                    "The currently selected Pipeline differs from the latest Run; "
                    "select or recompile a Pipeline before submitting"
                )
            request_id = str(
                (control_context or {}).get("execution_request_id")
                or new_id("execution_request")
            )
            idempotency_key = (
                f"conversation-{decision.intent.value.lower()}-"
                + hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]
            )
            if decision.intent == ConversationIntent.RETRY_RUN:
                run = self.agent_runtime.retry_failed_assets(
                    previous_run_id=previous_run["id"],
                    owner_id=owner_id,
                    idempotency_key=idempotency_key,
                )
            else:
                run = self.agent_runtime.submit_dataset_run(
                    work_order_id=work_order_id,
                    owner_id=owner_id,
                    idempotency_key=idempotency_key,
                )
            context["active_run_id"] = run["id"]
            self.store.update(
                thread_id=thread["id"], owner_id=owner_id, context=context
            )
            base["run"] = run
            base["reply"] = (
                f"已基于 Run {previous_run['id']} 创建新的执行 Run {run['id']}，"
                f"当前状态是 {run['status']}。"
            )
            return base
        if decision.intent == ConversationIntent.SUBMIT_RUN:
            turn = self.agent_runtime.state(
                work_order_id=work_order_id, owner_id=owner_id
            )
            state = turn["state"]
            identity = ":".join(
                (
                    str(
                        (control_context or {}).get("execution_request_id")
                        or thread["id"]
                    ),
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
            if not path.exists():
                # Do not hard-reject: surface the failure back to the model so
                # it can ask the user for the correct path within this turn.
                context.pop("pending_source", None)
                self.store.update(
                    thread_id=thread["id"], owner_id=owner_id, context=context
                )
                response["reply"] = f"找不到这个路径：{path}。请提供一个可访问的图片目录。"
                response["_react_feedback"] = (
                    f"用户提供的 source 路径不存在：{path}"
                    f"（source={source!r}）。请向用户确认正确的图片目录路径，"
                    "不要重复使用这个无效路径。"
                )
                return response
            if not path.is_dir():
                context.pop("pending_source", None)
                self.store.update(
                    thread_id=thread["id"], owner_id=owner_id, context=context
                )
                response["reply"] = f"这个路径不是文件夹：{path}。图片数据需要是一个目录。"
                response["_react_feedback"] = (
                    f"用户提供的 source 不是目录而是文件：{path}"
                    f"（source={source!r}）。请向用户说明需要图片目录，"
                    "并请用户提供目录路径。"
                )
                return response
            turn = self.agent_runtime.start(
                owner_id=owner_id,
                requirement=requirement,
                data_sources=[
                    {"type": "local_directory", "uri": str(path), "mapping": {}}
                ],
            )
            pending_patch = context.pop("pending_task_spec_patch", None)
            if pending_patch:
                turn = self.agent_runtime.resume(
                    work_order_id=turn["work_order_id"],
                    owner_id=owner_id,
                    decision={
                        "action": "edit_spec",
                        "task_spec_patch": pending_patch,
                        "channel": "conversation",
                    },
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
                    node_results = self.agent_runtime.get_run_node_results(
                        run_id=runs[0]["id"], owner_id=owner_id
                    )
                    context["latest_run_audit"] = self._audit_control_summary(
                        node_results
                    )
                except (KeyError, RuntimeError, ValueError) as exc:
                    context["audit_lookup_error"] = str(exc)
                try:
                    context["latest_run_pipeline"] = (
                        self.agent_runtime.pipeline_version(
                            pipeline_version_id=runs[0]["pipeline_version_id"],
                            owner_id=owner_id,
                        )
                    )
                    context["latest_run_pipeline_eligibility"] = (
                        self.agent_runtime.pipeline_version_eligibility(
                            pipeline_version_id=runs[0]["pipeline_version_id"],
                            owner_id=owner_id,
                        )
                    )
                except (KeyError, RuntimeError, ValueError):
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
                qc_report_id = runs[0].get("qc_report_id")
                if qc_report_id:
                    try:
                        context["latest_qc_report"] = self.agent_runtime.get_qc_report(
                            qc_report_id=qc_report_id,
                            owner_id=owner_id,
                        )
                    except (KeyError, RuntimeError, ValueError) as exc:
                        context["qc_report_lookup_error"] = str(exc)
        context["allowed_actions"] = list(self._allowed_actions(context))
        return context

    @staticmethod
    def _audit_control_summary(node_results: list[dict[str, Any]]) -> dict[str, Any]:
        assets: dict[tuple[int, str], list[dict[str, Any]]] = {}
        status_counts: dict[str, int] = {}
        decision_counts: dict[str, int] = {}
        for item in node_results:
            status = str(item.get("status") or "unknown")
            decision = str(item.get("decision") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            decision_counts[decision] = decision_counts.get(decision, 0) + 1
            key = (int(item.get("asset_sequence", 0)), str(item.get("source_uri") or "-"))
            assets.setdefault(key, []).append(
                {
                    "node_id": item.get("node_id"),
                    "operator_version_id": item.get("operator_version_id"),
                    "status": status,
                    "decision": decision,
                    "reason_codes": list(item.get("reason_codes") or []),
                    "error": item.get("error"),
                    "duration_ms": item.get("duration_ms", 0),
                }
            )
        exceptional = []
        for (sequence, source_uri), nodes in assets.items():
            relevant = [
                node
                for node in nodes
                if node["decision"] in {"reject", "failed"} or node["status"] == "failed"
            ]
            if relevant:
                exceptional.append(
                    {"sequence": sequence, "source_uri": source_uri, "nodes": relevant}
                )
        return {
            "asset_count": len(assets),
            "node_result_count": len(node_results),
            "status_counts": status_counts,
            "decision_counts": decision_counts,
            "exceptional_assets": exceptional,
        }

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
            "parent_dataset_version_id": dataset.get("parent_dataset_version_id"),
            "repair_run_ids": list(dataset.get("repair_run_ids") or []),
            "still_failed": list(dataset.get("still_failed") or []),
            "abandoned_assets": list(dataset.get("abandoned_assets") or []),
            "excluded_assets": list(dataset.get("excluded_assets") or []),
            "asset_lineage": [
                {
                    "source_uri": item.get("source_uri"),
                    "decision": item.get("decision"),
                    "asset_origin": item.get("asset_origin"),
                    "origin_dataset_version_id": item.get(
                        "origin_dataset_version_id"
                    ),
                    "origin_run_id": item.get("origin_run_id"),
                    "materialization": item.get("materialization"),
                    "reason_codes": list(item.get("reason_codes") or []),
                    "audit_refs": list(item.get("audit_refs") or []),
                }
                for item in assets
            ],
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
    def _ungrounded_control_identifiers(
        reply: str, context: dict[str, Any]
    ) -> tuple[str, ...]:
        """Block control-plane IDs the model mentions that are not in context.

        Grounded IDs are taken from the actual control-plane context (work
        order, runs, pipelines, task spec) rather than a hardcoded regex
        prefix set, so new ID namespaces are covered automatically.
        """
        import re

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

    @staticmethod
    def _control_facts_reply(
        context: dict[str, Any], facets: frozenset[str] | set[str]
    ) -> str:
        """Render grounded control-plane facts for the requested facets.

        The model selects which facets to show (``action`` =
        comma-separated facets); the reply text is built entirely from real
        control-plane state, so it cannot hallucinate IDs or counts.
        """
        facets = frozenset(facets)
        if not facets:
            return ""
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
        asks_work_order = "work_order" in facets
        asks_run = "run" in facets
        asks_pipeline = "pipeline" in facets
        asks_operators = "operators" in facets
        asks_task_spec = "task_spec" in facets
        asks_dataset = "dataset" in facets
        asks_outcome = "outcome" in facets
        asks_audit = "audit" in facets
        asks_repair = "repair" in facets
        pipeline = (
            run_pipeline
            if asks_dataset
            or asks_outcome
            or asks_repair
            or latest_run.get("status") in active_statuses
            or asks_run
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
                    lines.append(
                        "   parameters=`"
                        + json.dumps(
                            node.get("parameters") or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "`"
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
        if asks_dataset or asks_outcome:
            lines.append(
                ConversationService._dataset_result_reply(
                    context.get("latest_dataset"),
                    pipeline=pipeline,
                    explain=asks_outcome,
                    lookup_error=context.get("dataset_lookup_error"),
                )
            )
        if asks_audit:
            lines.append(
                ConversationService._audit_reply(
                    context.get("latest_run_audit"),
                    lookup_error=context.get("audit_lookup_error"),
                )
            )
        if asks_repair:
            lines.append(
                ConversationService._repair_facts_reply(
                    context.get("latest_dataset"),
                    lookup_error=context.get("dataset_lookup_error"),
                )
            )
        return "\n".join(lines)

    @staticmethod
    def _repair_facts_reply(
        dataset: dict[str, Any] | None,
        *,
        lookup_error: str | None = None,
    ) -> str:
        if dataset is None:
            return (
                f"无法读取 DatasetVersion 修复事实：{lookup_error}。"
                if lookup_error
                else "当前还没有可查询修复状态的 DatasetVersion。"
            )
        still_failed = dataset.get("still_failed") or []
        abandoned = dataset.get("abandoned_assets") or []
        excluded = dataset.get("excluded_assets") or []
        lines = [
            "### DatasetVersion 修复事实",
            f"- 当前版本：`{dataset.get('id', '-')}`",
            f"- 父版本：`{dataset.get('parent_dataset_version_id') or '无'}`",
            "- Repair Runs："
            + (
                ", ".join(
                    f"`{item}`" for item in dataset.get("repair_run_ids") or []
                )
                or "无"
            ),
            (
                f"- still_failed={len(still_failed)}，"
                f"abandoned={len(abandoned)}，excluded={len(excluded)}。"
            ),
        ]
        for label, items in (
            ("still_failed", still_failed),
            ("abandoned", abandoned),
            ("excluded", excluded),
        ):
            for item in items:
                reasons = ", ".join(item.get("reason_codes") or []) or "无原因码"
                lines.append(
                    f"- {label}：`{item.get('source_uri', '-')}`；"
                    f"尝试 {item.get('repair_attempts', 0)}；原因：{reasons}"
                )
        lineage = dataset.get("asset_lineage") or []
        if lineage:
            lines.append("资产血缘：")
            for item in lineage:
                lines.append(
                    f"- `{item.get('source_uri', '-')}`："
                    f"decision={item.get('decision', '-')}，"
                    f"origin={item.get('asset_origin', '-')}，"
                    f"dataset={item.get('origin_dataset_version_id') or '-'}，"
                    f"run={item.get('origin_run_id') or '-'}，"
                    f"materialization={item.get('materialization', '-')}"
                )
        return "\n".join(lines)

    @staticmethod
    def _audit_reply(
        audit: dict[str, Any] | None, *, lookup_error: str | None = None
    ) -> str:
        if audit is None:
            return (
                f"无法读取 Run 审计记录：{lookup_error}。"
                if lookup_error
                else "当前 Run 还没有逐节点审计记录。"
            )
        lines = [
            "### 逐资产节点审计",
            (
                f"共 {audit.get('asset_count', 0)} 张图片、"
                f"{audit.get('node_result_count', 0)} 条节点记录。"
            ),
        ]
        exceptional = audit.get("exceptional_assets") or []
        if not exceptional:
            lines.append("没有节点记录为拒绝或失败。")
            return "\n".join(lines)
        for asset in exceptional:
            lines.append(f"- `{asset.get('source_uri', '-')}`")
            for node in asset.get("nodes", []):
                reasons = ", ".join(node.get("reason_codes") or []) or "无原因码"
                error = f"；错误：{node['error']}" if node.get("error") else ""
                lines.append(
                    f"  - `{node.get('node_id', '-')}` -> "
                    f"`{node.get('operator_version_id', '-')}`："
                    f"{node.get('decision')}；原因：{reasons}{error}"
                )
        return "\n".join(lines)

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

    def _clarification_reply(
        self,
        task_spec: dict[str, Any],
        *,
        include_intro: bool = True,
    ) -> str:
        ambiguities = [str(item) for item in task_spec.get("ambiguities") or []]
        try:
            generated, _ = self.gateway.task_clarifications(task_spec=task_spec)
            questions = generated["questions"]
            if [str(item.get("field")) for item in questions] != ambiguities:
                raise ValueError(
                    "Clarification questions do not cover the current missing fields"
                )
            summary = str(generated.get("summary") or "").strip()
        except (AttributeError, ModelGatewayError, TypeError, ValueError) as exc:
            logger.warning("Task clarification generation failed: %s", exc)
            summary = "模型未能生成自然语言澄清问题；以下字段仍缺少信息。"
            questions = [
                {"field": field, "question": f"请补充字段 `{field}`。"}
                for field in ambiguities
            ]
        lines = ["当前 TaskSpec 仍有待澄清项："] if include_intro else []
        if summary:
            lines.append(summary)
        lines.extend(
            f"{index}. {item['question']}"
            for index, item in enumerate(questions, 1)
        )
        lines.append(
            "请直接回答这些问题；也可以说“按推荐默认值”，"
            "我会写入新版本后再请你确认。"
        )
        return "\n".join(lines)

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

    @staticmethod
    def _normalize_source(content: str) -> str:
        return content.strip().strip("\"'“”").strip()

    def _fallback_decision(self, content: str) -> ConversationAction:
        """Generic non-mutating fallback when the model is unavailable.

        The reply is overridden by the caller when a model failure occurs;
        this just provides a neutral CHAT decision so the turn does not
        mutate control-plane state.
        """
        return ChatAction(
            reply="我可以继续回答，也可以帮你创建图片数据生产任务。"
        )
