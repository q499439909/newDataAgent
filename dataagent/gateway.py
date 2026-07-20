from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .model_routing import ModelRoutingPolicy, ModelTaskKind
from .models import ModelResult, ModelUsage, TaskSpec


class ModelGatewayError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ModelGatewayError("Model response did not contain a JSON object")
        return json.loads(match.group(0))


class ModelGateway:
    def __init__(self, settings: Settings, timeout: float = 90.0):
        self.settings = settings
        self.timeout = timeout
        self.routing = ModelRoutingPolicy.from_settings(settings)

    @property
    def configured(self) -> bool:
        return bool(self.settings.api_key and self.settings.base_url)

    def _messages(
        self,
        model: str,
        system: str,
        content: str | list[dict[str, Any]],
        max_tokens: int = 2048,
    ) -> ModelResult:
        if not self.settings.api_key:
            raise ModelGatewayError("BAILIAN_API_KEY is not configured")
        url = f"{self.settings.base_url}/v1/messages"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "x-api-key": self.settings.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        response = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code != 429 and exc.response.status_code < 500:
                    detail = exc.response.text[:500]
                    raise ModelGatewayError(
                        f"Model API returned HTTP {exc.response.status_code}: {detail}"
                    ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (2**attempt))
        if response is None or response.is_error:
            if isinstance(last_error, httpx.HTTPStatusError):
                detail = last_error.response.text[:500]
                raise ModelGatewayError(
                    f"Model API returned HTTP {last_error.response.status_code} after retries: {detail}"
                ) from last_error
            raise ModelGatewayError(f"Model API request failed after retries: {last_error}")

        data = response.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        return ModelResult(
            text=text,
            usage=ModelUsage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
            ),
            model=data.get("model", model),
            request_id=data.get("id") or response.headers.get("request-id"),
            raw=data,
        )

    def healthcheck(self) -> ModelResult:
        route = self.routing.route(ModelTaskKind.REQUIREMENT_PLANNING)
        return self._messages(
            route.model_id,
            "You are a health check. Answer with the exact word OK.",
            "OK",
            max_tokens=16,
        )

    def conversation_turn(
        self,
        *,
        history: list[dict[str, str]],
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], ModelUsage]:
        route = self.routing.route(ModelTaskKind.CONVERSATION)
        system = (
            "You are DataAgent's conversational control assistant. Reply naturally in Chinese "
            "unless the user uses another language. You can explain the product and discuss the "
            "current task. Classify the user's intent, but never claim an action succeeded unless "
            "the control plane executes it. Ask one concise follow-up when information is missing. "
            "START_WORK_ORDER requires a concrete data-production requirement; greetings, product "
            "questions, capability questions, and usage questions are CHAT. A filesystem path by "
            "itself is PROVIDE_SOURCE only when pending_requirement is present. APPROVE, REJECT, "
            "SUBMIT_RUN, RUN_STATUS, and CONTROL_RUN require explicit user intent. When a TaskSpec "
            "is waiting for confirmation, new constraints or answers to follow-up questions are "
            "EDIT_TASK_SPEC, not APPROVE. Put only user-provided changes in task_spec_patch using "
            "hard_constraints, semantic_requirements, exclusion_requirements, preferences, or "
            "objective. For authenticity clarification, use hard_constraints.authenticity_scope. "
            "For mixed or unknown classes, use preferences.mixed_policy and "
            "preferences.unknown_policy with keep, review, or reject. For output safety, use "
            "hard_constraints.preserve_source and preferences.output_layout. Return JSON only "
            "with keys: intent, reply, requirement, source, strategy, "
            "action, task_spec_patch. Allowed intents are CHAT, START_WORK_ORDER, PROVIDE_SOURCE, "
            "EDIT_TASK_SPEC, APPROVE, REJECT, RESELECT_PIPELINE, SUBMIT_RUN, RUN_STATUS, "
            "CONTROL_RUN. RESELECT_PIPELINE is used only when the user explicitly chooses a "
            "different strategy for a completed or failed Run. strategy may be "
            "retention_first, balanced, quality_first, or null. "
            "action may be pause, resume, cancel, or null."
        )
        payload = {
            "configured_model": route.model_id,
            "conversation": history[-20:],
            "control_context": context,
        }
        result = self._messages(
            route.model_id,
            system,
            json.dumps(payload, ensure_ascii=False),
            max_tokens=700,
        )
        return _extract_json(result.text), result.usage

    def plan_task(self, requirement: str, source_path: str) -> tuple[TaskSpec, ModelUsage]:
        route = self.routing.route(ModelTaskKind.REQUIREMENT_PLANNING)
        schema = TaskSpec.model_json_schema()
        system = (
            "You are the planning model for DataAgent, an image data production system. "
            "Convert the user requirement into a conservative, executable TaskSpec. "
            "Do not invent measurable constraints that the user did not request. "
            "Separate objective hard constraints from semantic requirements. "
            "Allowed output_actions are filter, tag, deduplicate, resize, crop, enhance, "
            "convert, repair, generate, and manifest. Return JSON only, matching this schema: "
            + json.dumps(schema, ensure_ascii=False)
        )
        user = json.dumps(
            {"requirement": requirement, "source_type": "local", "source_path": source_path},
            ensure_ascii=False,
        )
        result = self._messages(route.model_id, system, user, max_tokens=3000)
        data = _extract_json(result.text)
        data["source_type"] = "local"
        data["source_path"] = source_path
        return TaskSpec.model_validate(data), result.usage

    def evaluate_image(self, image_path: Path, spec: TaskSpec) -> tuple[dict[str, Any], ModelUsage]:
        route = self.routing.route(ModelTaskKind.VISION_EVALUATION)
        media_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        if media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            media_type = "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        system = (
            "You are an independent image quality evaluator for DataAgent. Evaluate only "
            "the semantic and exclusion requirements, because dimensions and file quality "
            "are checked separately. Return JSON only with keys meets_requirement (boolean), "
            "confidence (number 0..1), reason (short Chinese string), and tags (string array). "
            "When the requirement is ambiguous, lower confidence instead of guessing."
        )
        criteria = {
            "objective": spec.objective,
            "semantic_requirements": spec.semantic_requirements,
            "exclusion_requirements": spec.exclusion_requirements,
        }
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": encoded},
            },
            {"type": "text", "text": json.dumps(criteria, ensure_ascii=False)},
        ]
        result = self._messages(route.model_id, system, content, max_tokens=500)
        data = _extract_json(result.text)
        return {
            "meets_requirement": bool(data.get("meets_requirement", False)),
            "confidence": max(0.0, min(1.0, float(data.get("confidence", 0.0)))),
            "reason": str(data.get("reason", "")),
            "tags": [str(tag) for tag in data.get("tags", [])],
        }, result.usage


def fallback_task_spec(requirement: str, source_path: str) -> TaskSpec:
    lower = requirement.lower()
    constraints: dict[str, Any] = {}
    patterns = {
        "min_width": [r"宽(?:度)?(?:至少|不小于|>=?)\s*(\d+)", r"min(?:imum)? width\s*(\d+)"],
        "min_height": [r"高(?:度)?(?:至少|不小于|>=?)\s*(\d+)", r"min(?:imum)? height\s*(\d+)"],
        "min_short_edge": [r"短边(?:至少|不小于|>=?)\s*(\d+)", r"short edge\s*(\d+)"],
    }
    for field, candidates in patterns.items():
        for pattern in candidates:
            match = re.search(pattern, lower, flags=re.IGNORECASE)
            if match:
                constraints[field] = int(match.group(1))
                break
    formats = re.findall(r"\b(jpe?g|png|webp|gif|bmp|tiff?)\b", lower)
    if formats and any(word in lower for word in ["只要", "仅", "格式", "format"]):
        constraints["allowed_formats"] = ["jpeg" if item in {"jpg", "jpeg"} else item for item in formats]

    actions = ["filter", "manifest"]
    action_words = {
        "deduplicate": ["去重", "dedup"],
        "resize": ["缩放", "resize"],
        "crop": ["裁剪", "crop"],
        "enhance": ["增强", "提亮", "autocontrast"],
        "convert": ["转换格式", "convert"],
        "tag": ["打标", "标签", "tag"],
    }
    for action, words in action_words.items():
        if any(word in lower for word in words):
            actions.append(action)

    semantic_probe = lower
    semantic_probe = re.sub(r"\d+(?:\.\d+)?", "", semantic_probe)
    for token in [
        "筛选", "图片", "宽度", "高度", "短边", "长边", "至少", "不小于", "大于",
        "小于", "去重", "并", "输出", "清单", "格式", "filter", "image", "width",
        "height", "minimum", "dedup", "manifest", "resize", "crop", "convert",
        "create", "and", "the", "files", "file",
    ]:
        semantic_probe = semantic_probe.replace(token, "")
    semantic_probe = re.sub(r"[^\w\u4e00-\u9fff]+", "", semantic_probe)
    semantic_requirements = [requirement] if len(semantic_probe) >= 2 else []

    return TaskSpec(
        objective=requirement,
        source_type="local",
        source_path=source_path,
        output_actions=actions,
        hard_constraints=constraints,
        semantic_requirements=semantic_requirements,
        ambiguities=["当前使用本地规则生成的 TaskSpec，请在试跑前确认语义口径。"],
        acceptance_notes=["首次任务以代理评估和边界样本人工判断为准。"],
    )
