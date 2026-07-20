from functools import partial

from langgraph.graph import END, START, StateGraph

from ...operators.registry import OperatorRegistry
from ...domain.operators import RuntimeBackend
from ..shared import WorkOrderGraphState
from .nodes import assess_candidate_sufficiency, generate_retrieval_plan


def build_retrieval_graph(
    operator_registry: OperatorRegistry | None = None,
    *,
    allow_draft_candidates: bool = False,
    available_runtime_backends: frozenset[RuntimeBackend] = frozenset(
        {RuntimeBackend.CPU}
    ),
):
    graph = StateGraph(WorkOrderGraphState)
    graph.add_node(
        "generate_retrieval_plan",
        partial(
            generate_retrieval_plan,
            operator_registry=operator_registry,
            allow_draft_candidates=allow_draft_candidates,
            available_runtime_backends=available_runtime_backends,
        ),
    )
    graph.add_node("assess_candidate_sufficiency", assess_candidate_sufficiency)
    graph.add_edge(START, "generate_retrieval_plan")
    graph.add_edge("generate_retrieval_plan", "assess_candidate_sufficiency")
    graph.add_edge("assess_candidate_sufficiency", END)
    return graph.compile()
