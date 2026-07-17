from langgraph.graph import END, START, StateGraph

from ..shared import WorkOrderGraphState
from .nodes import generate_task_spec, validate_task_spec


def build_requirement_graph():
    graph = StateGraph(WorkOrderGraphState)
    graph.add_node("generate_task_spec", generate_task_spec)
    graph.add_node("validate_task_spec", validate_task_spec)
    graph.add_edge(START, "generate_task_spec")
    graph.add_edge("generate_task_spec", "validate_task_spec")
    graph.add_edge("validate_task_spec", END)
    return graph.compile()
