from __future__ import annotations

from typing import Any, TypedDict


class WorkOrderGraphState(TypedDict, total=False):
    work_order_id: str
    owner_id: str
    requirement: str
    data_sources: list[dict[str, Any]]

    task_spec: dict[str, Any]
    task_spec_confirmed: bool
    task_spec_approval: dict[str, Any]

    retrieval_plan: dict[str, Any]
    candidate_sufficient: bool
    operator_candidates: list[dict[str, Any]]
    capability_coverage: list[dict[str, Any]]
    capability_resolution: dict[str, Any]
    capability_resolution_attempt: int
    runtime_backend_overrides: list[str]
    pipeline_experience_matches: list[dict[str, Any]]

    pipeline_variants: list[dict[str, Any]]
    representative_pipelines: list[dict[str, Any]]
    approved_pipeline: dict[str, Any]
    selected_pipeline_id: str
    pipeline_approval: dict[str, Any]

    sampling_plan: dict[str, Any]
    current_agent: str
    next_action: str
    terminated: bool
    trace: list[str]


def append_trace(state: WorkOrderGraphState, event: str) -> list[str]:
    return [*state.get("trace", []), event]
