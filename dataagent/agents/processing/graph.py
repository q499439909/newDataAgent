from functools import partial

from langgraph.graph import END, START, StateGraph

from ...operators import OperatorLibrary
from ...execution.pipeline_trial import PipelineTrialRunner
from ...experiences import PipelineExperienceRetriever
from ..runtime import AgentPlanner
from ..shared import WorkOrderGraphState
from .nodes import generate_pipeline_variants, select_representative_pipelines


def build_processing_graph(
    operator_library: OperatorLibrary | None = None,
    experience_retriever: PipelineExperienceRetriever | None = None,
    planner: AgentPlanner | None = None,
    trial_runner: PipelineTrialRunner | None = None,
):
    graph = StateGraph(WorkOrderGraphState)
    graph.add_node(
        "generate_pipeline_variants",
        partial(
            generate_pipeline_variants,
            operator_library=operator_library,
            experience_retriever=experience_retriever,
            planner=planner,
            trial_runner=trial_runner,
        ),
    )
    graph.add_node("select_representatives", select_representative_pipelines)
    graph.add_edge(START, "generate_pipeline_variants")
    graph.add_edge("generate_pipeline_variants", "select_representatives")
    graph.add_edge("select_representatives", END)
    return graph.compile()
