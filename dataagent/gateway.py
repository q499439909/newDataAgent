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
from .domain.specs import (
    RequirementDraft,
    build_requirement_source_clauses,
    canonicalize_requirement_draft_payload,
    hydrate_requirement_draft_sources,
    validate_requirement_draft_grounding,
)
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
) -> list[dict[str, str]]:
    observation = validate_requirement_draft_grounding(requirement, draft)
    return [
        {
            "code": item.code,
            "source_text": item.source_text,
            "message": item.message,
        }
        for item in observation.violations
    ]


def _requirement_draft_planning_schema() -> dict[str, Any]:
    schema = RequirementDraft.model_json_schema()
    constraint_schema = schema.get("$defs", {}).get("ConstraintContract", {})
    constraint_schema["required"] = [
        item for item in constraint_schema.get("required", ()) if item != "id"
    ]
    return schema


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
        *,
        timeout: float | None = None,
        attempts: int = 3,
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
        request_timeout = self.timeout if timeout is None else timeout
        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=request_timeout) as client:
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
            if attempt < attempts - 1:
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
        gaps = [
            item for item in task_spec.get("gaps", ()) if isinstance(item, dict)
        ]
        system = (
            "You generate concise clarification questions for an image-data task. "
            "Use the complete TaskSpec and its structured `gaps`. Summarize constraints "
            "that are already captured, then preserve exactly the model-authored question "
            "for every blocking gap. Never invent a gap or default answer. Return JSON only "
            "with this shape: "
            '{"summary":"...","questions":[{"gap_id":"...","question":"..."}]}.'
        )
        result = self._messages(
            route.model_id,
            system,
            json.dumps(
                {
                    "task_spec": task_spec,
                    "gaps": gaps,
                },
                ensure_ascii=False,
            ),
            max_tokens=1000,
            timeout=min(self.timeout, 20.0),
            attempts=1,
        )
        payload = _extract_json(result.text)
        questions = payload.get("questions")
        if not isinstance(payload.get("summary"), str) or not isinstance(
            questions, list
        ):
            raise ValueError("Clarification model returned an invalid payload")
        gap_ids = [
            str(item.get("gap_id"))
            for item in questions
            if isinstance(item, dict)
            and isinstance(item.get("question"), str)
            and item.get("question", "").strip()
        ]
        expected_ids = [str(item.get("id")) for item in gaps]
        if gap_ids != expected_ids:
            raise ValueError(
                "Clarification model questions do not match TaskSpec gaps"
            )
        return payload, result.usage

    def plan_requirement_draft(
        self,
        *,
        requirement: str,
        messages: tuple[dict[str, Any], ...] = (),
        data_sources: tuple[dict, ...],
    ) -> dict[str, Any]:
        """Interpret a request without choosing operators or implementation."""

        route = self.routing.route(ModelTaskKind.REQUIREMENT_PLANNING)
        schema = _requirement_draft_planning_schema()
        source_clauses = build_requirement_source_clauses(requirement)
        system = (
            "You are DataAgent's Requirement Planning Agent. Convert the user's "
            "request into a complete, implementation-neutral RequirementDraft. "
            "Split every independently testable condition into one atomic constraint. "
            "Classify every meaningful source clause with a ClauseTrace. A clause "
            "that defines or qualifies an existing condition is a definition trace "
            "referencing that Constraint; it is not a new Constraint unless it is "
            "independently testable. Preference, output, and context clauses use "
            "their corresponding trace roles. A tie-break, ranking, or retention "
            "choice applied only after eligible items have been grouped is a "
            "preference/output instruction, not an additional eligibility "
            "Constraint. "
            "A description of the source's current contents, annotations, count, "
            "or layout is a context fact unless the user explicitly requires it "
            "as an eligibility, transformation, output, or acceptance condition. "
            "Represent such facts with a context ClauseTrace normalized_effect, "
            "not a Constraint. "
            "Every filtering, transformation, classification, annotation, deduplication, "
            "or output clause must have at least one Constraint; a semantic requirement "
            "does not substitute for its Constraint. "
            "Preserve comparison boundaries exactly (for example < differs from <=), "
            "normalize units. Do not assign final Constraint IDs; the server assigns "
            "stable C-numbers. A temporary Constraint id is optional and is used only "
            "when a definition trace must refer to that Constraint. Every Constraint "
            "and ClauseTrace must reference one "
            "supplied source clause by `source_clause_id`; every RequirementGap must "
            "use `source_clause_ids`. The server owns the exact source text and hydrates "
            "it from those IDs, so never invent source IDs. Put only genuinely "
            "blocking semantic gaps in `gaps`; each gap must explain why it blocks a "
            "TaskSpec and include a concise user question plus an answer JSON Schema. "
            "Never add a gap merely because source files were not inspected. "
            "Constraint `field` is a generic observable target "
            "such as image.face_count or image.vehicle_count. "
            "Do not choose capabilities, operators, libraries, models, parameters, "
            "pipeline nodes, or execution order; RetrievalAgent and ProcessingAgent "
            "own those decisions. Do not infer evidence values from filenames or "
            "paths. Return JSON only and match this JSON Schema exactly: "
            + json.dumps(schema, ensure_ascii=False)
        )
        request_payload: dict[str, Any] = {
            "requirement": requirement,
            "messages": list(messages),
            "data_sources": data_sources,
            "source_clauses": list(source_clauses),
        }
        grounding_errors: list[dict[str, str]] = []
        for attempt in range(2):
            if grounding_errors:
                request_payload["repair_observation"] = {
                    "errors": grounding_errors,
                    "instruction": (
                        "Repair exactly the reported structural grounding "
                        "violations using supplied source clause IDs. Preserve "
                        "valid semantics and do not add defaults or task-specific "
                        "rules."
                    ),
                }
            result = self._messages(
                route.model_id,
                system,
                json.dumps(request_payload, ensure_ascii=False),
                max_tokens=4000,
            )
            canonical_payload = canonicalize_requirement_draft_payload(
                _extract_json(result.text),
                source_clauses,
            )
            draft = hydrate_requirement_draft_sources(
                RequirementDraft.model_validate(canonical_payload),
                source_clauses,
            )
            grounding_errors = _requirement_draft_grounding_errors(
                requirement,
                draft,
            )
            if not grounding_errors:
                return draft.model_dump(mode="json")
            request_payload["previous_invalid_draft"] = draft.model_dump(mode="json")
        raise ModelGatewayError(
            "Requirement planner could not produce a grounded draft: "
            + "; ".join(
                f"{item['code']}[{item['source_text']}]: {item['message']}"
                for item in grounding_errors
            )
        )

    def resolve_requirement_gaps(
        self,
        *,
        gaps: tuple[dict[str, Any], ...],
        answer: str,
        messages: tuple[dict[str, Any], ...],
        draft: dict[str, Any],
        requirement: str,
        data_sources: tuple[dict, ...],
    ) -> dict[str, Any]:
        """Resolve gaps and revise the persisted draft in one bounded model call."""

        route = self.routing.route(ModelTaskKind.REQUIREMENT_PLANNING)
        source_clauses = build_requirement_source_clauses(requirement)
        draft_schema = _requirement_draft_planning_schema()
        system = (
            "You are resolving an active Requirement clarification boundary. "
            "Interpret the user's latest message semantically against only the supplied "
            "gaps. For every gap return its exact gap_id, resolved=true only when the "
            "message provides enough information for its answer_schema, and a concise "
            "normalized_answer. A question, complaint, topic change, or partial answer "
            "must not be invented into a resolution. Return a helpful user-facing reply "
            "that acknowledges what was understood and asks only unresolved questions. "
            "Set requirement_relevant=false only when the message supplies no requirement "
            "information. When every gap is resolved, return `revised_draft`: update "
            "the supplied previous draft using the answer while preserving unaffected "
            "semantics and ordering. Clear resolved gaps. Use supplied source_clause_id "
            "values and do not assign final Constraint IDs or direct constraint Trace "
            "references; the server derives those. Descriptions of the source's current "
            "contents or layout are context facts unless the user explicitly makes them "
            "an eligibility, transformation, output, or acceptance requirement. "
            "When any gap remains unresolved, set revised_draft to null. "
            "Return JSON only with shape "
            '{"reply":"...","requirement_relevant":true,'
            '"resolutions":[{"gap_id":"...","resolved":true,'
            '"normalized_answer":"..."}],"revised_draft":{...}}. '
            "When non-null, revised_draft must match this exact JSON Schema: "
            + json.dumps(draft_schema, ensure_ascii=False)
        )
        expected = [str(item.get("id")) for item in gaps]
        request_payload: dict[str, Any] = {
            "active_gaps": list(gaps),
            "latest_message": answer,
            "recent_messages": list(messages[-6:]),
            "previous_draft": draft,
            "requirement": requirement,
            "data_sources": list(data_sources),
            "source_clauses": list(source_clauses),
        }
        last_error: ValueError | ModelGatewayError | None = None
        for _attempt in range(2):
            result = self._messages(
                route.model_id,
                system,
                json.dumps(request_payload, ensure_ascii=False),
                max_tokens=4000,
                timeout=self.timeout,
                attempts=1,
            )
            try:
                payload = _extract_json(result.text)
                resolutions = payload.get("resolutions")
                if not isinstance(payload.get("reply"), str) or not isinstance(
                    resolutions, list
                ):
                    raise ModelGatewayError(
                        "Requirement gap resolver returned an invalid payload"
                    )
                actual = [
                    str(item.get("gap_id"))
                    for item in resolutions
                    if isinstance(item, dict)
                ]
                if actual != expected:
                    raise ModelGatewayError(
                        "Requirement gap resolver did not preserve active gap IDs"
                    )
                all_resolved = all(
                    isinstance(item, dict) and item.get("resolved") is True
                    for item in resolutions
                )
                if not all_resolved:
                    payload["revised_draft"] = None
                    return payload
                revised_payload = payload.get("revised_draft")
                if not isinstance(revised_payload, dict):
                    raise ModelGatewayError(
                        "Resolved Requirement gaps require a revised draft"
                    )
                canonical_payload = canonicalize_requirement_draft_payload(
                    revised_payload,
                    source_clauses,
                    previous_draft=draft,
                )
                revised_draft = hydrate_requirement_draft_sources(
                    RequirementDraft.model_validate(canonical_payload),
                    source_clauses,
                )
                grounding_errors = _requirement_draft_grounding_errors(
                    requirement,
                    revised_draft,
                )
                if revised_draft.gaps or grounding_errors:
                    raise ModelGatewayError(
                        "Requirement gap resolution produced an invalid revised "
                        "draft: "
                        + "; ".join(
                            f"{item['code']}: {item['message']}"
                            for item in grounding_errors
                        )
                    )
                payload["revised_draft"] = revised_draft.model_dump(mode="json")
                return payload
            except (ValueError, ModelGatewayError) as exc:
                last_error = exc
                request_payload["repair_observation"] = {
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "instruction": (
                        "Return the same resolution decision with a revised_draft "
                        "that matches the supplied Schema exactly. Do not invent "
                        "fields or operators."
                    ),
                }
        raise ModelGatewayError(
            "Requirement gap resolver could not produce a valid revised draft: "
            + str(last_error)
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
            "messages": [
                item.model_dump(mode="json") for item in request.messages
            ],
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
