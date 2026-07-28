from functools import partial

from langgraph.graph import END, START, StateGraph

from ..shared import WorkOrderGraphState
from .nodes import generate_task_spec, validate_task_spec
from .planner import RequirementPlanner


def build_requirement_graph(
    requirement_planner: RequirementPlanner | None = None,
):
    graph = StateGraph(WorkOrderGraphState)
    graph.add_node(
        "generate_task_spec",
        partial(
            generate_task_spec,
            requirement_planner=requirement_planner,
        ),
    )
    graph.add_node("validate_task_spec", validate_task_spec)
    graph.add_edge(START, "generate_task_spec")
    graph.add_edge("generate_task_spec", "validate_task_spec")
    graph.add_edge("validate_task_spec", END)
    return graph.compile()
