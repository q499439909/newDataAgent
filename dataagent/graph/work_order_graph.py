from __future__ import annotations

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..operators import OperatorLibrary, build_operator_library
from ..domain.operators import RuntimeBackend
from ..agents.processing import build_processing_graph
from ..agents.runner import AgentPlanner
from ..agents.requirement import (
    RequirementPlanner,
    build_requirement_graph,
    build_task_plan,
    decide_requirement_agent_turn,
)
from ..agents.retrieval import build_retrieval_graph
from ..agents.shared import WorkOrderGraphState
from ..agents.strategy import build_strategy_graph
from ..experiences import PipelineExperienceRetriever
from ..execution.pipeline_trial import PipelineTrialRunner
from .interrupts import approve_pipeline, confirm_task_spec, resolve_capability_gaps
from .state_migrations import migrate_work_order_state


def _route_requirement_agent(state: WorkOrderGraphState) -> str:
    return migrate_work_order_state(state)["requirement_agent_action"]


def _decide_requirement_agent(
    state: WorkOrderGraphState,
    *,
    planner: AgentPlanner | None,
) -> dict:
    return decide_requirement_agent_turn(
        migrate_work_order_state(state),
        planner=planner,
    )


def _resolved_run_ids(state: WorkOrderGraphState) -> list[str]:
    run_id = (state.get("latest_run_observation") or {}).get("run_id")
    resolved = list(state.get("resolved_run_ids", ()))
    if run_id and run_id not in resolved:
        resolved.append(run_id)
    return resolved


def _complete_work_order(state: WorkOrderGraphState) -> dict:
    updates = {
        "resolved_run_ids": _resolved_run_ids(state),
        "next_action": "complete_work_order",
        "current_agent": "requirement",
        "trace": [*state.get("trace", ()), "run:outcome_completed"],
    }
    return {
        **updates,
        "task_plan": build_task_plan({**state, **updates}),
    }


def _terminate_work_order(state: WorkOrderGraphState) -> dict:
    updates = {
        "resolved_run_ids": _resolved_run_ids(state),
        "terminated": True,
        "next_action": "terminated",
        "current_agent": "requirement",
        "trace": [*state.get("trace", ()), "run:work_order_terminated"],
    }
    return {
        **updates,
        "task_plan": build_task_plan({**state, **updates}),
    }


def _prepare_pipeline_recompile(state: WorkOrderGraphState) -> dict:
    updates = {
        "pipeline_variants": [],
        "representative_pipelines": [],
        "approved_pipeline": {},
        "selected_pipeline_id": "",
        "pipeline_approval": {},
        "sampling_plan": {},
        "resolved_run_ids": _resolved_run_ids(state),
        "next_action": "generate_pipeline_candidates",
        "current_agent": "processing",
        "trace": [*state.get("trace", ()), "run:recompile_selected"],
    }
    return {
        **updates,
        "task_plan": build_task_plan({**state, **updates}),
    }


def _prepare_candidate_retrieval(state: WorkOrderGraphState) -> dict:
    updates = {
        "retrieval_plan": {},
        "candidate_sufficient": False,
        "operator_candidates": [],
        "capability_coverage": [],
        "capability_resolution": {},
        "capability_resolution_attempt": 0,
        "runtime_backend_overrides": [],
        "pipeline_variants": [],
        "representative_pipelines": [],
        "approved_pipeline": {},
        "selected_pipeline_id": "",
        "pipeline_approval": {},
        "sampling_plan": {},
        "resolved_run_ids": _resolved_run_ids(state),
        "next_action": "run_retrieval_agent",
        "current_agent": "retrieval",
        "trace": [*state.get("trace", ()), "run:reretrieval_selected"],
    }
    return {
        **updates,
        "task_plan": build_task_plan({**state, **updates}),
    }


def _request_run_control(
    state: WorkOrderGraphState,
    *,
    next_action: str,
) -> dict:
    updates = {
        "resolved_run_ids": _resolved_run_ids(state),
        "next_action": next_action,
        "current_agent": "requirement",
        "trace": [*state.get("trace", ()), f"run:{next_action}_selected"],
    }
    return {
        **updates,
        "task_plan": build_task_plan({**state, **updates}),
    }


def _ask_user_about_run_outcome(state: WorkOrderGraphState) -> dict:
    outcome = state.get("latest_run_observation") or {}
    allowed_actions = [
        "reretrieve_candidates",
        "recompile_pipeline",
        "terminate",
    ]
    if outcome.get("retryable"):
        allowed_actions.insert(0, "retry_failed_assets")
    if outcome.get("run_status") == "CANCELLED":
        allowed_actions.insert(0, "rerun_pipeline")
    response = interrupt(
        {
            "kind": "run_outcome_resolution",
            "work_order_id": state["work_order_id"],
            "run_outcome": outcome,
            "allowed_actions": allowed_actions,
        }
    )
    if not isinstance(response, dict):
        raise ValueError("Run outcome resolution requires an object response")
    action = response.get("action")
    if action not in allowed_actions:
        raise ValueError(
            "Run outcome resolution action is not allowed: "
            f"{action}; allowed={allowed_actions}"
        )
    return {
        "requirement_agent_action": action,
        "next_action": action,
        "trace": [
            *state.get("trace", ()),
            "run:human_resolution_received",
        ],
    }


def build_work_order_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    operator_library: OperatorLibrary | None = None,
    allow_draft_datajuicer_candidates: bool = False,
    available_runtime_backends: frozenset[RuntimeBackend] = frozenset(
        {RuntimeBackend.CPU}
    ),
    experience_retriever: PipelineExperienceRetriever | None = None,
    requirement_planner: RequirementPlanner | None = None,
    agent_planner: AgentPlanner | None = None,
    pipeline_trial_runner: PipelineTrialRunner | None = None,
):
    """Build the governed planning and formal Run-outcome decision graph."""

    operator_library = operator_library or build_operator_library(
        include_datajuicer=False
    )
    graph = StateGraph(WorkOrderGraphState)
    graph.add_node(
        "requirement_agent",
        partial(_decide_requirement_agent, planner=agent_planner),
    )
    graph.add_node(
        "requirement_planning_agent",
        build_requirement_graph(
            requirement_planner,
            agent_planner=agent_planner,
        ),
    )
    graph.add_node("confirm_task_spec", confirm_task_spec)
    graph.add_node(
        "retrieval_agent",
        build_retrieval_graph(
            operator_library.registry,
            allow_draft_candidates=allow_draft_datajuicer_candidates,
            available_runtime_backends=available_runtime_backends,
            planner=agent_planner,
            experience_retriever=experience_retriever,
        ),
    )
    graph.add_node(
        "processing_agent",
        build_processing_graph(
            operator_library,
            experience_retriever,
            planner=agent_planner,
            trial_runner=pipeline_trial_runner,
        ),
    )
    graph.add_node("resolve_capability_gaps", resolve_capability_gaps)
    graph.add_node("approve_pipeline", approve_pipeline)
    graph.add_node("strategy_agent", build_strategy_graph(agent_planner))
    graph.add_node("complete_work_order", _complete_work_order)
    graph.add_node("terminate_work_order", _terminate_work_order)
    graph.add_node(
        "retry_failed_assets",
        partial(_request_run_control, next_action="retry_failed_assets"),
    )
    graph.add_node(
        "rerun_pipeline",
        partial(_request_run_control, next_action="rerun_pipeline"),
    )
    graph.add_node(
        "ask_user",
        _ask_user_about_run_outcome,
    )
    graph.add_node("reretrieve_candidates", _prepare_candidate_retrieval)
    graph.add_node("recompile_pipeline", _prepare_pipeline_recompile)

    graph.add_edge(START, "requirement_agent")
    graph.add_conditional_edges(
        "requirement_agent",
        _route_requirement_agent,
        {
            "run_requirement_agent": "requirement_planning_agent",
            "confirm_task_spec": "confirm_task_spec",
            "run_retrieval_agent": "retrieval_agent",
            "resolve_capability_gaps": "resolve_capability_gaps",
            "run_processing_agent": "processing_agent",
            "approve_pipeline": "approve_pipeline",
            "run_strategy_agent": "strategy_agent",
            "complete_work_order": "complete_work_order",
            "retry_failed_assets": "retry_failed_assets",
            "rerun_pipeline": "rerun_pipeline",
            "reretrieve_candidates": "reretrieve_candidates",
            "recompile_pipeline": "recompile_pipeline",
            "ask_user": "ask_user",
            "finish_planning": END,
            "terminate": "terminate_work_order",
        },
    )
    graph.add_edge("requirement_planning_agent", "requirement_agent")
    graph.add_edge("confirm_task_spec", "requirement_agent")
    graph.add_edge("retrieval_agent", "requirement_agent")
    graph.add_edge("resolve_capability_gaps", "requirement_agent")
    graph.add_edge("processing_agent", "requirement_agent")
    graph.add_edge("approve_pipeline", "requirement_agent")
    graph.add_edge("strategy_agent", "requirement_agent")
    graph.add_edge("complete_work_order", END)
    graph.add_edge("terminate_work_order", END)
    graph.add_edge("retry_failed_assets", END)
    graph.add_edge("rerun_pipeline", END)
    graph.add_conditional_edges(
        "ask_user",
        _route_requirement_agent,
        {
            "retry_failed_assets": "retry_failed_assets",
            "rerun_pipeline": "rerun_pipeline",
            "reretrieve_candidates": "reretrieve_candidates",
            "recompile_pipeline": "recompile_pipeline",
            "terminate": "terminate_work_order",
        },
    )
    graph.add_edge("reretrieve_candidates", "retrieval_agent")
    graph.add_edge("recompile_pipeline", "processing_agent")
    return graph.compile(checkpointer=checkpointer)
