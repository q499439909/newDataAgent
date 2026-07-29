from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx
from json_repair import repair_json

from .config import Settings
from .model_routing import ModelRoutingPolicy, ModelTaskKind
from .models import ModelResult, ModelUsage, TaskSpec
from .domain.specs import RequirementDraft, validate_requirement_draft_grounding
from .agents.runner import AgentDecision, AgentPlanningRequest


class ModelGatewayError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as strict_error:
        try:
            payload = repair_json(
                cleaned,
                return_objects=True,
                ensure_ascii=False,
            )
        except (TypeError, ValueError) as repair_error:
            raise ModelGatewayError(
                f"Model response JSON could not be repaired: {repair_error}"
            ) from strict_error
    if not isinstance(payload, dict) or not payload:
        raise ModelGatewayError("Model response did not contain a JSON object")
    return payload


def _requirement_draft_grounding_errors(
    requirement: str,
    draft: RequirementDraft,
) -> list[str]:
    observation = validate_requirement_draft_grounding(requirement, draft)
    return [item.message for item in observation.violations]


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

    def task_clarifications(
        self,
        *,
        task_spec: dict[str, Any],
    ) -> tuple[dict[str, Any], ModelUsage]:
        route = self.routing.route(ModelTaskKind.CONVERSATION)
        missing_fields = [
            str(item) for item in task_spec.get("ambiguities", ()) if str(item)
        ]
        system = (
            "You generate concise clarification questions for an image-data task. "
            "Use the complete TaskSpec and its `ambiguities` field paths. Summarize "
            "constraints that are already captured, then ask exactly one natural-language "
            "question for each missing field. Never ask for a field that already has a "
            "value, never add requirements, and do not use a fixed domain-specific question "
            "template. Return JSON only with this shape: "
            '{"summary":"...","questions":[{"field":"...","question":"..."}]}. '
            "Every question field must be one of the supplied ambiguity paths and every "
            "ambiguity path must appear exactly once."
        )
        result = self._messages(
            route.model_id,
            system,
            json.dumps(
                {
                    "task_spec": task_spec,
                    "missing_fields": missing_fields,
                },
                ensure_ascii=False,
            ),
            max_tokens=1000,
        )
        payload = _extract_json(result.text)
        questions = payload.get("questions")
        if not isinstance(payload.get("summary"), str) or not isinstance(
            questions, list
        ):
            raise ValueError("Clarification model returned an invalid payload")
        fields = [
            str(item.get("field"))
            for item in questions
            if isinstance(item, dict)
            and isinstance(item.get("question"), str)
            and item.get("question", "").strip()
        ]
        if fields != missing_fields:
            raise ValueError(
                "Clarification model questions do not match TaskSpec ambiguities"
            )
        return payload, result.usage

    def plan_requirement_draft(
        self,
        *,
        requirement: str,
        data_sources: tuple[dict, ...],
    ) -> dict[str, Any]:
        """Interpret a request without choosing operators or implementation."""

        route = self.routing.route(ModelTaskKind.REQUIREMENT_PLANNING)
        schema = RequirementDraft.model_json_schema()
        system = (
            "You are DataAgent's Requirement Planning Agent. Convert the user's "
            "request into a complete, implementation-neutral RequirementDraft. "
            "Split every independently testable condition into one atomic constraint. "
            "Classify every meaningful source clause with a ClauseTrace. A clause "
            "that defines or qualifies an existing condition is a definition trace "
            "referencing that Constraint; it is not a new Constraint unless it is "
            "independently testable. Preference, output, and context clauses use "
            "their corresponding trace roles. "
            "Every filtering, transformation, classification, annotation, deduplication, "
            "or output clause must have at least one Constraint; a semantic requirement "
            "does not substitute for its Constraint. "
            "Preserve comparison boundaries exactly (for example < differs from <=), "
            "normalize units, retain the original source span, and list genuinely "
            "blocking ambiguities. Constraint `field` is a generic observable target "
            "such as image.face_count or image.vehicle_count. "
            "Do not choose capabilities, operators, libraries, models, parameters, "
            "pipeline nodes, or execution order; RetrievalAgent and ProcessingAgent "
            "own those decisions. Do not infer evidence values from filenames or "
            "paths. Return JSON only and match this JSON Schema exactly: "
            + json.dumps(schema, ensure_ascii=False)
        )
        request_payload: dict[str, Any] = {
            "requirement": requirement,
            "data_sources": data_sources,
        }
        grounding_errors: list[str] = []
        for attempt in range(3):
            if grounding_errors:
                request_payload["repair_observation"] = {
                    "errors": grounding_errors,
                    "instruction": (
                        "Repair Constraint and ClauseTrace grounding using only "
                        "exact spans from the original requirement."
                    ),
                }
            result = self._messages(
                route.model_id,
                system,
                json.dumps(request_payload, ensure_ascii=False),
                max_tokens=3000,
            )
            draft = RequirementDraft.model_validate(_extract_json(result.text))
            grounding_errors = _requirement_draft_grounding_errors(
                requirement,
                draft,
            )
            if not grounding_errors:
                return draft.model_dump(mode="json")
            request_payload["previous_invalid_draft"] = draft.model_dump(mode="json")
        raise ModelGatewayError(
            "Requirement planner could not produce a grounded draft: "
            + "; ".join(grounding_errors)
        )

    def plan_agent_decision(
        self,
        request: AgentPlanningRequest,
    ) -> dict[str, Any]:
        """Choose one governed Agent action from tools and observations."""

        task_kind = {
            "retrieval": ModelTaskKind.RETRIEVAL_PLANNING,
            "processing": ModelTaskKind.PROCESSING_PLANNING,
            "strategy": ModelTaskKind.STRATEGY_PLANNING,
        }.get(request.agent_name, ModelTaskKind.REQUIREMENT_PLANNING)
        route = self.routing.route(task_kind)
        system = (
            f"You are DataAgent's {request.agent_name} Agent. Work toward the "
            "given goal using only the supplied governed tools and factual "
            "observations. Choose one next action per response. For action=tool, "
            "set tool_name to an available tool and provide its arguments in "
            "tool_input. After a tool result, inspect the observation before "
            "deciding again. Use finish only when the goal is satisfied and put "
            "the typed result in output. Use report_gap when available evidence "
            "cannot cover the goal, and ask_user only when an explicit user "
            "choice is required. Do not infer operator support from requirement "
            "keywords and do not invent operator IDs, parameters, evidence, or "
            "tool results. Return JSON only matching this schema: "
            + json.dumps(AgentDecision.model_json_schema(), ensure_ascii=False)
        )
        payload = {
            "agent": request.agent_name,
            "goal": request.goal,
            "iteration": request.iteration,
            "context": request.context,
            "tools": request.tools,
            "observations": [
                item.model_dump(mode="json") for item in request.observations
            ],
        }
        last_error: Exception | None = None
        max_tokens = {
            "main": 800,
            "retrieval": 2000,
            "processing": 5000,
            "strategy": 2000,
        }.get(request.agent_name, 1500)
        for _attempt in range(3):
            result = self._messages(
                route.model_id,
                system,
                json.dumps(payload, ensure_ascii=False, default=str),
                max_tokens=max_tokens,
            )
            try:
                return AgentDecision.model_validate(
                    _extract_json(result.text)
                ).model_dump(mode="json")
            except (ModelGatewayError, ValueError) as exc:
                last_error = exc
                payload["repair_observation"] = {
                    "error": str(exc),
                    "previous_response": result.text[:2000],
                    "instruction": (
                        "Return one non-empty JSON object matching AgentDecision."
                    ),
                }
        raise ModelGatewayError(
            f"Agent planner did not return a valid decision: {last_error}"
        )

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

    def call_vision_model_json(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        model: str | None = None,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Call an OpenAI-compatible VLM (DashScope qwen) with one image.

        Distinct from evaluate_image: this speaks the OpenAI chat-completions
        protocol against settings.vision_api_base_url (not the Anthropic base_url),
        so native VLM operators can call the vision model directly without going
        through the Data-Juicer dj-process subprocess.
        """
        if not self.settings.api_key:
            raise ModelGatewayError("BAILIAN_API_KEY is not configured")
        route = self.routing.route(ModelTaskKind.VISION_EVALUATION)
        model_id = model or route.model_id
        encoded = base64.b64encode(image_bytes).decode("ascii")
        url = (
            f"{self.settings.vision_api_base_url.rstrip('/')}/chat/completions"
        )
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_id,
            "messages": [
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{encoded}"
                            },
                        },
                    ],
                }
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
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
                        f"VLM API returned HTTP {exc.response.status_code}: {detail}"
                    ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (2**attempt))
        if response is None or response.is_error:
            if isinstance(last_error, httpx.HTTPStatusError):
                detail = last_error.response.text[:500]
                raise ModelGatewayError(
                    f"VLM API returned HTTP {last_error.response.status_code} after retries: {detail}"
                ) from last_error
            raise ModelGatewayError(f"VLM API request failed after retries: {last_error}")
        data = response.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        parsed = _extract_json(text)
        parsed["_dataagent_gateway"] = {
            "model": str(data.get("model") or model_id),
            "request_id": (
                data.get("id")
                or response.headers.get("request-id")
                or response.headers.get("x-request-id")
            ),
            "usage": data.get("usage", {}),
        }
        return parsed


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
