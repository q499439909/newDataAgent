from langgraph.graph import END, START, StateGraph

from ..shared import WorkOrderGraphState
from .nodes import generate_pipeline_variants, select_representative_pipelines


def build_processing_graph():
    graph = StateGraph(WorkOrderGraphState)
    graph.add_node("generate_pipeline_variants", generate_pipeline_variants)
    graph.add_node("select_representatives", select_representative_pipelines)
    graph.add_edge(START, "generate_pipeline_variants")
    graph.add_edge("generate_pipeline_variants", "select_representatives")
    graph.add_edge("select_representatives", END)
    return graph.compile()
