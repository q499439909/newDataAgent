from __future__ import annotations

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..operators import OperatorLibrary, build_operator_library
from ..domain.operators import RuntimeBackend
from ..domain.pipelines import PipelineVersion
from ..domain.specs import TaskSpecVersion
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
from ..execution.pipeline_trial import (
    PipelineTrialRequest,
    PipelineTrialRunner,
    PipelineTrialStatus,
)
from .interrupts import (
    approve_pipeline,
    confirm_operator_plan,
    confirm_task_spec,
    resolve_pipeline_trial,
    resolve_capability_gaps,
)
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
        "selected_pipeline_trial": {},
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
        "operator_plan": {},
        "operator_plan_confirmed": False,
        "operator_plan_approval": {},
        "capability_coverage": [],
        "capability_resolution": {},
        "capability_resolution_attempt": 0,
        "runtime_backend_overrides": [],
        "pipeline_variants": [],
        "representative_pipelines": [],
        "approved_pipeline": {},
        "selected_pipeline_id": "",
        "pipeline_approval": {},
        "selected_pipeline_trial": {},
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


def _trial_selected_pipeline(
    state: WorkOrderGraphState,
    *,
    operator_library: OperatorLibrary,
    trial_runner: PipelineTrialRunner | None,
) -> dict:
    pipeline = PipelineVersion.model_validate(state["approved_pipeline"])
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    if trial_runner is None:
        try:
            operator_library.runtime.validate_pipeline(pipeline)
        except (KeyError, ValueError) as exc:
            trial = {
                "pipeline_version_id": pipeline.id,
                "status": "failed",
                "mode": "static_validation",
                "execution_failures": [str(exc)],
            }
        else:
            trial = {
                "pipeline_version_id": pipeline.id,
                "status": "static_validation_passed",
                "mode": "static_validation",
                "execution_failures": [],
            }
    else:
        observation = trial_runner.run(
            PipelineTrialRequest(task_spec=spec, pipeline=pipeline)
        )
        trial = observation.model_dump(mode="json")
    passed = trial["status"] in {
        PipelineTrialStatus.PASSED.value,
        "static_validation_passed",
    }
    agent_observation = {
        "agent": "processing",
        "status": "selected_pipeline_trial",
        "summary": (
            "The selected Pipeline passed its bounded trial."
            if passed
            else "The selected Pipeline trial requires resolution."
        ),
        "pipeline_trial": trial,
    }
    return {
        "selected_pipeline_trial": trial,
        "next_action": (
            "run_strategy_agent" if passed else "resolve_pipeline_trial"
        ),
        "agent_observations": [
            *state.get("agent_observations", ()),
            agent_observation,
        ],
        "trace": [
            *state.get("trace", ()),
            (
                "processing:selected_pipeline_trial_passed"
                if passed
                else "processing:selected_pipeline_trial_failed"
            ),
        ],
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
    graph.add_node("confirm_operator_plan", confirm_operator_plan)
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
    graph.add_node(
        "trial_selected_pipeline",
        partial(
            _trial_selected_pipeline,
            operator_library=operator_library,
            trial_runner=pipeline_trial_runner,
        ),
    )
    graph.add_node("resolve_pipeline_trial", resolve_pipeline_trial)
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
            "confirm_operator_plan": "confirm_operator_plan",
            "resolve_capability_gaps": "resolve_capability_gaps",
            "run_processing_agent": "processing_agent",
            "approve_pipeline": "approve_pipeline",
            "trial_selected_pipeline": "trial_selected_pipeline",
            "resolve_pipeline_trial": "resolve_pipeline_trial",
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
    graph.add_edge("confirm_operator_plan", "requirement_agent")
    graph.add_edge("retrieval_agent", "requirement_agent")
    graph.add_edge("resolve_capability_gaps", "requirement_agent")
    graph.add_edge("processing_agent", "requirement_agent")
    graph.add_edge("approve_pipeline", "requirement_agent")
    graph.add_edge("trial_selected_pipeline", "requirement_agent")
    graph.add_edge("resolve_pipeline_trial", "requirement_agent")
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
