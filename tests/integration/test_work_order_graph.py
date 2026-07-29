from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from dataagent.domain.pipelines import PipelineStrategy
from dataagent.graph import build_work_order_graph


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


def test_requirement_agent_is_the_root_graph_agent() -> None:
    graph = build_work_order_graph()

    nodes = graph.get_graph().nodes

    assert "requirement_agent" in nodes
    assert "requirement_planning_agent" in nodes
    assert "main_agent" not in nodes


def test_four_agent_graph_interrupts_and_resumes() -> None:
    graph = build_work_order_graph(InMemorySaver())
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
    assert [
        item["action"] for item in final["requirement_agent_decisions"]
    ] == [
        "run_requirement_agent",
        "confirm_task_spec",
        "run_retrieval_agent",
        "run_processing_agent",
        "approve_pipeline",
        "run_strategy_agent",
        "finish_planning",
    ]
    assert final["agent_state_version"] == 2
    assert "main_agent_decisions" not in final
    assert next(
        item
        for item in final["task_plan"]
        if item["id"] == "submit_dataset_run"
    ) == {
        "id": "submit_dataset_run",
        "label": "Submit the approved dataset run",
        "status": "ready",
    }
    assert [item["id"] for item in final["task_plan"][-3:]] == [
        "execute_dataset",
        "evaluate_quality",
        "resolve_run_outcome",
    ]
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
    graph = build_work_order_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "thread_reject"}}
    graph.invoke(initial_state(), config)

    final = graph.invoke(Command(resume={"approved": False, "reason": "needs edits"}), config)

    assert final["terminated"] is True
    assert "retrieval_plan" not in final
    assert final["next_action"] == "terminated"


def test_task_spec_can_be_revised_before_confirmation() -> None:
    graph = build_work_order_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "thread_spec_revision"}}
    first = graph.invoke(initial_state(), config)

    revised = graph.invoke(
        Command(
            resume={
                "action": "edit_spec",
                "task_spec_patch": {
                    "exclusion_requirements": ["Exclude synthetic images"],
                    "preferences": {"output_layout": "by_class"},
                },
            }
        ),
        config,
    )

    assert revised["__interrupt__"][0].value["kind"] == "task_spec_confirmation"
    assert revised["task_spec"]["version"] == first["task_spec"]["version"] + 1
    assert revised["task_spec"]["confirmed"] is False
    assert revised["task_spec"]["exclusion_requirements"] == [
        "Exclude synthetic images"
    ]
    assert revised["task_spec"]["preferences"]["output_layout"] == "by_class"
    assert revised["trace"][-1] == "hitl:task_spec_revised"

    approved = graph.invoke(Command(resume={"approved": True}), config)
    assert approved["task_spec"]["confirmed"] is True
    assert approved["__interrupt__"][0].value["kind"] == "capability_resolution"


def test_graph_rejects_unknown_task_patch_fields_at_the_revision_boundary() -> None:
    graph = build_work_order_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "thread_invalid_patch"}}
    graph.invoke(initial_state(), config)

    with pytest.raises(ValueError, match="clasification"):
        graph.invoke(
            Command(
                resume={
                    "action": "edit_spec",
                    "task_spec_patch": {
                        "clasification": {
                            "labels": [
                                {"id": "cat", "display_name": "Cat"},
                                {"id": "dog", "display_name": "Dog"},
                            ]
                        }
                    },
                }
            ),
            config,
        )


def test_graph_does_not_confirm_task_spec_with_unresolved_ambiguities() -> None:
    graph = build_work_order_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "thread_clarification"}}
    state = initial_state() | {
        "requirement": "去掉不真实、不是实拍直出的图片，把猫和狗分开"
    }
    first = graph.invoke(state, config)
    assert first["task_spec"]["ambiguities"] == [
        "hard_constraints.authenticity_scope",
        "preferences.mixed_policy",
        "preferences.unknown_policy",
        "hard_constraints.preserve_source",
        "preferences.output_layout",
    ]

    still_waiting = graph.invoke(Command(resume={"approved": True}), config)
    assert still_waiting["task_spec_confirmed"] is False
    assert still_waiting["__interrupt__"][0].value["kind"] == "task_spec_confirmation"
