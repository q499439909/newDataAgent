from __future__ import annotations

from typing import Any, TypedDict


CURRENT_AGENT_STATE_VERSION = 2


class WorkOrderGraphState(TypedDict, total=False):
    agent_state_version: int
    work_order_id: str
    owner_id: str
    requirement: str
    requirement_messages: list[dict[str, Any]]
    data_sources: list[dict[str, Any]]
    requirement_draft: dict[str, Any]
    requirement_clarification_request: dict[str, Any]

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
    operator_plan: dict[str, Any]
    operator_plan_confirmed: bool
    operator_plan_approval: dict[str, Any]

    pipeline_variants: list[dict[str, Any]]
    representative_pipelines: list[dict[str, Any]]
    approved_pipeline: dict[str, Any]
    selected_pipeline_id: str
    pipeline_approval: dict[str, Any]
    selected_pipeline_trial: dict[str, Any]

    sampling_plan: dict[str, Any]
    current_agent: str
    next_action: str
    waiting: str | None
    terminated: bool
    trace: list[str]
    requirement_agent_action: str
    requirement_agent_decisions: list[dict[str, Any]]
    agent_action: dict[str, Any]
    agent_observations: list[dict[str, Any]]
    task_plan: list[dict[str, str]]
    latest_run_feedback: dict[str, Any]
    latest_run_observation: dict[str, Any]
    observed_run_ids: list[str]
    resolved_run_ids: list[str]
    active_run_id: str


def append_trace(state: WorkOrderGraphState, event: str) -> list[str]:
    return [*state.get("trace", []), event]
