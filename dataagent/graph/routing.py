from __future__ import annotations

from ..agents.shared import WorkOrderGraphState


def route_after_spec_approval(state: WorkOrderGraphState) -> str:
    return "end" if state.get("terminated") else "retrieval"


def route_after_retrieval(state: WorkOrderGraphState) -> str:
    return "processing" if state.get("candidate_sufficient") else "resolution"


def route_after_capability_resolution(state: WorkOrderGraphState) -> str:
    if state.get("terminated") or state.get("next_action") == "edit_task_spec":
        return "end"
    return "retrieval"


def route_after_pipeline_approval(state: WorkOrderGraphState) -> str:
    return "end" if state.get("terminated") else "strategy"
