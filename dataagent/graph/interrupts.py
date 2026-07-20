from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from ..agents.shared import WorkOrderGraphState, append_trace
from ..domain.common import new_id
from ..domain.pipelines import PipelineStrategy, PipelineVersion
from ..domain.specs import TaskSpecVersion
from ..domain.plans import CapabilityCoverage, CapabilityCoverageStatus
from ..operators.planning import decompose_task_capabilities, infer_output_actions


def _merge_unique(current: tuple[str, ...], additions: Any) -> tuple[str, ...]:
    if not isinstance(additions, (list, tuple)):
        return current
    return tuple(dict.fromkeys([*current, *(str(item) for item in additions if str(item).strip())]))


def _revise_task_spec(
    spec: TaskSpecVersion,
    *,
    patch: dict[str, Any],
    actor: str,
) -> TaskSpecVersion:
    hard_constraints = dict(spec.hard_constraints)
    if isinstance(patch.get("hard_constraints"), dict):
        hard_constraints.update(patch["hard_constraints"])
    preferences = dict(spec.preferences)
    if isinstance(patch.get("preferences"), dict):
        preferences.update(patch["preferences"])
    objective = str(patch.get("objective") or spec.objective).strip()
    semantic_requirements = _merge_unique(
        spec.semantic_requirements, patch.get("semantic_requirements")
    )
    exclusion_requirements = _merge_unique(
        spec.exclusion_requirements, patch.get("exclusion_requirements")
    )
    planning_text = " ".join(
        [objective, *semantic_requirements, *exclusion_requirements]
    )
    capabilities = decompose_task_capabilities(planning_text)
    return spec.model_copy(
        update={
            "id": new_id("spec"),
            "version": spec.version + 1,
            "parent_version_id": spec.id,
            "created_by": actor,
            "change_reason": "TaskSpec revised from conversation",
            "objective": objective,
            "hard_constraints": hard_constraints,
            "semantic_requirements": semantic_requirements,
            "exclusion_requirements": exclusion_requirements,
            "preferences": preferences,
            "capability_requirements": capabilities,
            "output_actions": infer_output_actions(capabilities),
            "confirmed": False,
        }
    )


def confirm_task_spec(state: WorkOrderGraphState) -> dict[str, Any]:
    if state.get("task_spec_confirmed"):
        return {"trace": append_trace(state, "hitl:task_spec_already_confirmed")}
    decision = interrupt(
        {
            "kind": "task_spec_confirmation",
            "work_order_id": state["work_order_id"],
            "task_spec": state["task_spec"],
            "allowed_actions": ["approve", "reject", "edit_spec", "terminate"],
        }
    )
    if isinstance(decision, dict) and decision.get("action") == "edit_spec":
        spec = TaskSpecVersion.model_validate(state["task_spec"])
        revised = _revise_task_spec(
            spec,
            patch=decision.get("task_spec_patch") or {},
            actor=state["owner_id"],
        )
        return {
            "task_spec": revised.model_dump(mode="json"),
            "task_spec_confirmed": False,
            "task_spec_approval": decision,
            "next_action": "confirm_task_spec",
            "trace": append_trace(state, "hitl:task_spec_revised"),
        }
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    if not approved:
        return {
            "task_spec_approval": decision if isinstance(decision, dict) else {},
            "terminated": True,
            "next_action": "terminated",
            "trace": append_trace(state, "hitl:task_spec_rejected"),
        }
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    confirmed = spec.model_copy(
        update={
            "id": new_id("spec"),
            "version": spec.version + 1,
            "parent_version_id": spec.id,
            "change_reason": "TaskSpec confirmed by user",
            "confirmed": True,
        }
    )
    return {
        "task_spec": confirmed.model_dump(mode="json"),
        "task_spec_confirmed": True,
        "task_spec_approval": decision if isinstance(decision, dict) else {"approved": True},
        "next_action": "run_retrieval_agent",
        "trace": append_trace(state, "hitl:task_spec_approved"),
    }


def approve_pipeline(state: WorkOrderGraphState) -> dict[str, Any]:
    representatives = [
        PipelineVersion.model_validate(item) for item in state["representative_pipelines"]
    ]
    if state.get("selected_pipeline_id"):
        return {"trace": append_trace(state, "hitl:pipeline_already_approved")}
    decision = interrupt(
        {
            "kind": "pipeline_approval",
            "work_order_id": state["work_order_id"],
            "pipelines": [
                {
                    "id": item.id,
                    "strategy": item.strategy,
                    "version": item.version,
                    "node_count": len(item.nodes),
                    "nodes": [node.model_dump(mode="json") for node in item.nodes],
                }
                for item in representatives
            ],
            "default_strategy": PipelineStrategy.BALANCED.value,
            "allowed_actions": ["approve", "reject", "edit_parameters", "terminate"],
        }
    )
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    if not approved:
        return {
            "pipeline_approval": decision if isinstance(decision, dict) else {},
            "terminated": True,
            "next_action": "terminated",
            "trace": append_trace(state, "hitl:pipeline_rejected"),
        }
    selected_id = decision.get("pipeline_id") if isinstance(decision, dict) else None
    if not selected_id:
        selected_id = next(
            item.id for item in representatives if item.strategy == PipelineStrategy.BALANCED
        )
    if selected_id not in {item.id for item in representatives}:
        raise ValueError("Selected pipeline is not one of the representative pipelines")
    selected = next(item for item in representatives if item.id == selected_id)
    family_versions = [
        PipelineVersion.model_validate(item)
        for item in state.get("pipeline_variants", ())
        if item.get("family_id") == selected.family_id
    ]
    approved_pipeline = selected.model_copy(
        update={
            "id": new_id("pipeline_version"),
            "version": max(item.version for item in family_versions) + 1,
            "parent_version_id": selected.id,
            "created_by": state["owner_id"],
            "change_reason": "Pipeline approved by user",
            "approved": True,
        }
    )
    return {
        "approved_pipeline": approved_pipeline.model_dump(mode="json"),
        "selected_pipeline_id": approved_pipeline.id,
        "pipeline_approval": decision if isinstance(decision, dict) else {"approved": True},
        "next_action": "run_strategy_agent",
        "trace": append_trace(state, "hitl:pipeline_approved"),
    }


def resolve_capability_gaps(state: WorkOrderGraphState) -> dict[str, Any]:
    gaps = [
        CapabilityCoverage.model_validate(item)
        for item in state.get("capability_coverage", [])
        if item.get("required")
        and item.get("status") != CapabilityCoverageStatus.COVERED
    ]
    if not gaps:
        return {
            "candidate_sufficient": True,
            "next_action": "generate_pipeline_candidates",
            "trace": append_trace(state, "hitl:capability_resolution_not_needed"),
        }
    decision = interrupt(
        {
            "kind": "capability_resolution",
            "work_order_id": state["work_order_id"],
            "attempt": state.get("capability_resolution_attempt", 0) + 1,
            "gaps": [item.model_dump(mode="json") for item in gaps],
            "options": [
                {
                    "id": "retry",
                    "label": "重新检索",
                    "action": "retry",
                },
                {
                    "id": "enable_remote",
                    "label": "启用远程模型后重试",
                    "action": "retry",
                    "enable_runtime_backends": ["remote"],
                },
                {
                    "id": "revise_task",
                    "label": "修改 TaskSpec",
                    "action": "revise_task",
                },
                {
                    "id": "terminate",
                    "label": "终止工单",
                    "action": "terminate",
                },
            ],
            "allowed_actions": ["retry", "revise_task", "terminate"],
        }
    )
    payload = decision if isinstance(decision, dict) else {"action": "retry"}
    action = str(payload.get("action", "retry"))
    if action == "terminate":
        return {
            "capability_resolution": payload,
            "capability_resolution_attempt": state.get(
                "capability_resolution_attempt", 0
            )
            + 1,
            "terminated": True,
            "next_action": "terminated",
            "trace": append_trace(state, "hitl:capability_resolution_terminated"),
        }
    if action == "revise_task":
        return {
            "capability_resolution": payload,
            "capability_resolution_attempt": state.get(
                "capability_resolution_attempt", 0
            )
            + 1,
            "next_action": "edit_task_spec",
            "trace": append_trace(state, "hitl:capability_resolution_revise_task"),
        }
    if action != "retry":
        raise ValueError(f"Unsupported capability resolution action: {action}")
    enabled = {
        *state.get("runtime_backend_overrides", []),
        *(str(item) for item in payload.get("enable_runtime_backends", [])),
    }
    invalid = enabled.difference({"cpu", "cuda", "remote"})
    if invalid:
        raise ValueError(f"Unsupported runtime backend overrides: {sorted(invalid)}")
    return {
        "capability_resolution": payload,
        "capability_resolution_attempt": state.get("capability_resolution_attempt", 0)
        + 1,
        "runtime_backend_overrides": sorted(enabled),
        "next_action": "run_retrieval_agent",
        "trace": append_trace(state, "hitl:capability_resolution_retry"),
    }
