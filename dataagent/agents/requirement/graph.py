from functools import partial

from langgraph.graph import END, START, StateGraph

from ..shared import WorkOrderGraphState
from ..runtime import AgentPlanner
from .nodes import clarify_requirement, generate_task_spec, validate_task_spec
from .planner import RequirementPlanner


def build_requirement_graph(
    requirement_planner: RequirementPlanner | None = None,
    *,
    agent_planner: AgentPlanner | None = None,
):
    def route_after_generation(state: WorkOrderGraphState) -> str:
        return state["next_action"]

    graph = StateGraph(WorkOrderGraphState)
    graph.add_node(
        "generate_task_spec",
        partial(
            generate_task_spec,
            requirement_planner=requirement_planner,
            agent_planner=agent_planner,
        ),
    )
    graph.add_node("clarify_requirement", clarify_requirement)
    graph.add_node("validate_task_spec", validate_task_spec)
    graph.add_edge(START, "generate_task_spec")
    graph.add_conditional_edges(
        "generate_task_spec",
        route_after_generation,
        {
            "clarify_requirement": "clarify_requirement",
            "confirm_task_spec": "validate_task_spec",
        },
    )
    graph.add_edge("clarify_requirement", "generate_task_spec")
    graph.add_edge("validate_task_spec", END)
    return graph.compile()
