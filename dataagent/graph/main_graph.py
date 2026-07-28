from __future__ import annotations

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from ..operators import OperatorLibrary, build_operator_library
from ..domain.operators import RuntimeBackend
from ..agents.processing import build_processing_graph
from ..agents.runtime import AgentPlanner
from ..agents.main import decide_main_agent_turn
from ..agents.requirement import RequirementPlanner, build_requirement_graph
from ..agents.retrieval import build_retrieval_graph
from ..agents.shared import WorkOrderGraphState
from ..agents.strategy import build_strategy_graph
from ..experiences import PipelineExperienceRetriever
from ..execution.pipeline_trial import PipelineTrialRunner
from .interrupts import approve_pipeline, confirm_task_spec, resolve_capability_gaps


def _route_main_agent(state: WorkOrderGraphState) -> str:
    return state["main_agent_action"]


def build_main_graph(
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
    """Build the four-agent decision graph.

    The graph deliberately stops after SamplingPlan creation. Dataset execution,
    evaluation and model feedback are external deterministic runs added in the
    next application-layer milestone.
    """

    operator_library = operator_library or build_operator_library(
        include_datajuicer=False
    )
    graph = StateGraph(WorkOrderGraphState)
    graph.add_node(
        "main_agent",
        partial(decide_main_agent_turn, planner=agent_planner),
    )
    graph.add_node(
        "requirement_agent",
        build_requirement_graph(requirement_planner),
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

    graph.add_edge(START, "main_agent")
    graph.add_conditional_edges(
        "main_agent",
        _route_main_agent,
        {
            "run_requirement_agent": "requirement_agent",
            "confirm_task_spec": "confirm_task_spec",
            "run_retrieval_agent": "retrieval_agent",
            "resolve_capability_gaps": "resolve_capability_gaps",
            "run_processing_agent": "processing_agent",
            "approve_pipeline": "approve_pipeline",
            "run_strategy_agent": "strategy_agent",
            "finish_planning": END,
            "terminate": END,
        },
    )
    graph.add_edge("requirement_agent", "main_agent")
    graph.add_edge("confirm_task_spec", "main_agent")
    graph.add_edge("retrieval_agent", "main_agent")
    graph.add_edge("resolve_capability_gaps", "main_agent")
    graph.add_edge("processing_agent", "main_agent")
    graph.add_edge("approve_pipeline", "main_agent")
    graph.add_edge("strategy_agent", "main_agent")
    return graph.compile(checkpointer=checkpointer)
