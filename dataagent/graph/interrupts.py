from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from ..agents.shared import WorkOrderGraphState, append_trace
from ..domain.common import new_id
from ..domain.pipelines import PipelineStrategy, PipelineVersion
from ..domain.specs import TaskSpecVersion


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
                }
                for item in representatives
            ],
            "default_strategy": PipelineStrategy.BALANCED,
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
    return {
        "selected_pipeline_id": selected_id,
        "pipeline_approval": decision if isinstance(decision, dict) else {"approved": True},
        "next_action": "run_strategy_agent",
        "trace": append_trace(state, "hitl:pipeline_approved"),
    }
