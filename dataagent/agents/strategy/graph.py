from langgraph.graph import END, START, StateGraph
from functools import partial

from ..runner import AgentPlanner
from ..shared import WorkOrderGraphState
from .nodes import generate_sampling_plan


def build_strategy_graph(planner: AgentPlanner | None = None):
    graph = StateGraph(WorkOrderGraphState)
    graph.add_node(
        "generate_sampling_plan",
        partial(generate_sampling_plan, planner=planner),
    )
    graph.add_edge(START, "generate_sampling_plan")
    graph.add_edge("generate_sampling_plan", END)
    return graph.compile()
