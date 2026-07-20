from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from dataagent.domain.pipelines import PipelineStrategy
from dataagent.graph import build_main_graph


def initial_state() -> dict:
    return {
        "work_order_id": "work_order_1",
        "owner_id": "user_1",
        "requirement": "筛选清晰图片并去重",
        "data_sources": [
            {
                "type": "local_directory",
                "uri": "D:/images",
                "mapping": {},
            }
        ],
        "trace": [],
    }


def test_four_agent_graph_interrupts_and_resumes() -> None:
    graph = build_main_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "thread_1"}}

    first = graph.invoke(initial_state(), config)
    assert first["__interrupt__"][0].value["kind"] == "task_spec_confirmation"
    assert first["task_spec"]["confirmed"] is False

    second = graph.invoke(Command(resume={"approved": True}), config)
    assert second["__interrupt__"][0].value["kind"] == "pipeline_approval"
    assert second["task_spec_confirmed"] is True
    assert second["candidate_sufficient"] is True
    assert len(second["pipeline_variants"]) == 3
    assert len(second["representative_pipelines"]) == 3
    assert {item["strategy"] for item in second["representative_pipelines"]} == {
        strategy.value for strategy in PipelineStrategy
    }
    approval_pipelines = second["__interrupt__"][0].value["pipelines"]
    assert len(approval_pipelines) == 3
    assert all(item["nodes"] for item in approval_pipelines)
    assert approval_pipelines[0]["nodes"][0]["operator_version_id"] == (
        "builtin.decode_check:1"
    )

    balanced = next(
        item for item in second["representative_pipelines"] if item["strategy"] == "balanced"
    )
    final = graph.invoke(
        Command(resume={"approved": True, "pipeline_id": balanced["id"]}), config
    )
    assert "__interrupt__" not in final
    assert final["selected_pipeline_id"] != balanced["id"]
    assert final["approved_pipeline"]["parent_version_id"] == balanced["id"]
    assert final["approved_pipeline"]["approved"] is True
    assert final["approved_pipeline"]["version"] == 2
    assert final["sampling_plan"]["random_seed"] == 42
    assert final["next_action"] == "submit_dataset_run"
    assert final["trace"] == [
        "requirement:task_spec_generated",
        "requirement:task_spec_validated",
        "hitl:task_spec_approved",
        "retrieval:plan_generated",
        "retrieval:candidate_sufficiency_checked",
        "processing:variants_generated",
        "processing:representatives_selected",
        "hitl:pipeline_approved",
        "strategy:sampling_plan_generated",
    ]


def test_rejected_task_spec_terminates_before_retrieval() -> None:
    graph = build_main_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "thread_reject"}}
    graph.invoke(initial_state(), config)

    final = graph.invoke(Command(resume={"approved": False, "reason": "needs edits"}), config)

    assert final["terminated"] is True
    assert "retrieval_plan" not in final
    assert final["next_action"] == "terminated"
