from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from ..operators import OperatorLibrary, build_operator_library
from ..domain.operators import RuntimeBackend
from ..agents.processing import build_processing_graph
from ..agents.requirement import build_requirement_graph
from ..agents.retrieval import build_retrieval_graph
from ..agents.shared import WorkOrderGraphState
from ..agents.strategy import build_strategy_graph
from ..experiences import PipelineExperienceRetriever
from .interrupts import approve_pipeline, confirm_task_spec, resolve_capability_gaps
from .routing import (
    route_after_pipeline_approval,
    route_after_retrieval,
    route_after_capability_resolution,
    route_after_spec_approval,
)


def build_main_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    operator_library: OperatorLibrary | None = None,
    allow_draft_datajuicer_candidates: bool = False,
    available_runtime_backends: frozenset[RuntimeBackend] = frozenset(
        {RuntimeBackend.CPU}
    ),
    experience_retriever: PipelineExperienceRetriever | None = None,
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
    graph.add_node("requirement_agent", build_requirement_graph())
    graph.add_node("confirm_task_spec", confirm_task_spec)
    graph.add_node(
        "retrieval_agent",
        build_retrieval_graph(
            operator_library.registry,
            allow_draft_candidates=allow_draft_datajuicer_candidates,
            available_runtime_backends=available_runtime_backends,
        ),
    )
    graph.add_node(
        "processing_agent",
        build_processing_graph(operator_library, experience_retriever),
    )
    graph.add_node("resolve_capability_gaps", resolve_capability_gaps)
    graph.add_node("approve_pipeline", approve_pipeline)
    graph.add_node("strategy_agent", build_strategy_graph())

    graph.add_edge(START, "requirement_agent")
    graph.add_edge("requirement_agent", "confirm_task_spec")
    graph.add_conditional_edges(
        "confirm_task_spec",
        route_after_spec_approval,
        {
            "confirm": "confirm_task_spec",
            "retrieval": "retrieval_agent",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "retrieval_agent",
        route_after_retrieval,
        {
            "processing": "processing_agent",
            "resolution": "resolve_capability_gaps",
        },
    )
    graph.add_conditional_edges(
        "resolve_capability_gaps",
        route_after_capability_resolution,
        {"retrieval": "retrieval_agent", "end": END},
    )
    graph.add_edge("processing_agent", "approve_pipeline")
    graph.add_conditional_edges(
        "approve_pipeline",
        route_after_pipeline_approval,
        {"strategy": "strategy_agent", "end": END},
    )
    graph.add_edge("strategy_agent", END)
    return graph.compile(checkpointer=checkpointer)
