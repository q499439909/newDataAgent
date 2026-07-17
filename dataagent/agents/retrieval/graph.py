from langgraph.graph import END, START, StateGraph

from ..shared import WorkOrderGraphState
from .nodes import assess_candidate_sufficiency, generate_retrieval_plan


def build_retrieval_graph():
    graph = StateGraph(WorkOrderGraphState)
    graph.add_node("generate_retrieval_plan", generate_retrieval_plan)
    graph.add_node("assess_candidate_sufficiency", assess_candidate_sufficiency)
    graph.add_edge(START, "generate_retrieval_plan")
    graph.add_edge("generate_retrieval_plan", "assess_candidate_sufficiency")
    graph.add_edge("assess_candidate_sufficiency", END)
    return graph.compile()
