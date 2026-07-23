from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from ..domain.common import new_id
from .observations import ToolResult
from .registry import ToolRegistry
from .spec import ToolContext


_DISPLAY_NAMES = {
    "propose_control_action": "Control Action Guard",
    "query_control_facts": "Control Fact Reader",
    "retrieve_operators": "Operator Retriever",
    "compile_pipeline_artifact": "Pipeline Compiler",
    "validate_pipeline_artifact": "Pipeline Validator",
}

_STAGE_LABELS = {
    "analyze_requirement": "正在理解需求",
    "validate_control_action": "正在验证操作",
    "inspect_control_facts": "正在读取控制面事实",
    "retrieve_operator_candidates": "正在检索算子",
    "compile_pipeline_artifact": "正在生成 Pipeline",
    "validate_pipeline_artifact": "正在校验 Pipeline",
}


def _safe_value(value: Any, key: str = "") -> Any:
    normalized_key = key.lower()
    if any(
        token in normalized_key
        for token in ("api_key", "token", "password", "secret", "credential")
    ):
        return "***"
    if normalized_key == "pipeline" and isinstance(value, dict):
        return {
            "id": value.get("id"),
            "version": value.get("version"),
            "strategy": value.get("strategy"),
            "node_count": len(value.get("nodes") or ()),
        }
    if normalized_key == "action" and isinstance(value, dict):
        summary = {
            item_key: _safe_value(value[item_key], item_key)
            for item_key in (
                "intent",
                "source",
                "requirement",
                "action",
                "strategy",
                "facets",
                "run_id",
            )
            if value.get(item_key) is not None
        }
        patch = value.get("task_spec_patch")
        if isinstance(patch, dict):
            classification = patch.get("classification")
            patch_summary: dict[str, Any] = {
                "fields": sorted(patch),
                "semantic_requirement_count": len(
                    patch.get("semantic_requirements") or ()
                ),
                "exclusion_requirement_count": len(
                    patch.get("exclusion_requirements") or ()
                ),
            }
            if isinstance(classification, dict):
                patch_summary["classification"] = {
                    "label_ids": [
                        item.get("id")
                        for item in classification.get("labels") or ()
                        if isinstance(item, dict)
                    ],
                    "mixed_label": classification.get("mixed_label"),
                    "unknown_label": classification.get("unknown_label"),
                }
            hard_constraints = patch.get("hard_constraints")
            if isinstance(hard_constraints, dict):
                patch_summary["hard_constraint_fields"] = sorted(
                    hard_constraints
                )
            preferences = patch.get("preferences")
            if isinstance(preferences, dict):
                patch_summary["preference_fields"] = sorted(preferences)
            summary["task_spec_patch"] = patch_summary
        return summary
    if isinstance(value, dict):
        return {
            str(item_key): _safe_value(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_value(item) for item in value]
    if isinstance(value, str) and len(value) > 500:
        encoded = value.encode("utf-8")
        return {
            "content_sha256": hashlib.sha256(encoded).hexdigest(),
            "characters": len(value),
        }
    return value


class GovernedToolLoop:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(
        self,
        *,
        name: str,
        stage: str,
        context: ToolContext,
        raw_input: dict[str, Any],
    ) -> tuple[ToolResult, dict[str, Any]]:
        started = time.perf_counter()
        result = self.registry.execute(name, context, raw_input)
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        evidence_ids = tuple(item.id for item in result.evidence)
        trace = {
            "id": new_id("tool_trace"),
            "stage": stage,
            "stage_label": _STAGE_LABELS.get(stage, stage),
            "kind": "tool",
            "tool": name,
            "display_name": _DISPLAY_NAMES.get(name, name),
            "status": result.status,
            "parameters": _safe_value(raw_input),
            "duration_ms": duration_ms,
            "summary": result.summary,
            "evidence_ids": list(evidence_ids),
            "error_type": result.error_type,
        }
        json.dumps(trace, ensure_ascii=False, default=str)
        return result, trace


__all__ = ["GovernedToolLoop"]
