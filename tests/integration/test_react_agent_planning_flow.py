from __future__ import annotations

from dataagent.agents.runtime import (
    AgentDecision,
    AgentPlanningRequest,
)
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.domain.specs import RequirementDraft


class ArbitraryRequirementPlanner:
    def plan(self, request) -> RequirementDraft:
        return RequirementDraft(
            objective=request.requirement,
            constraints=(
                {
                    "id": "constraint_arbitrary_metric",
                    "source_text": request.requirement,
                    "scope": "asset",
                    "field": "image.width",
                    "operator": "gte",
                    "value": 7,
                    "unit": "px",
                    "required_evidence_type": "image_width",
                },
            ),
        )


class WorkOrderScriptedPlanner:
    def __init__(self) -> None:
        self.requests: list[AgentPlanningRequest] = []
        self._agent_steps = {
            "main": iter(
                (
                    AgentDecision(
                        action="tool",
                        reason_summary="Structure the requirement first.",
                        tool_name="run_requirement_agent",
                    ),
                    AgentDecision(
                        action="tool",
                        reason_summary="Require explicit TaskSpec confirmation.",
                        tool_name="confirm_task_spec",
                    ),
                    AgentDecision(
                        action="tool",
                        reason_summary="Retrieve governed candidates.",
                        tool_name="run_retrieval_agent",
                    ),
                    AgentDecision(
                        action="tool",
                        reason_summary="Design Pipeline variants from candidates.",
                        tool_name="run_processing_agent",
                    ),
                    AgentDecision(
                        action="tool",
                        reason_summary="Ask the user to choose a Pipeline.",
                        tool_name="approve_pipeline",
                    ),
                    AgentDecision(
                        action="tool",
                        reason_summary="Prepare the execution strategy.",
                        tool_name="run_strategy_agent",
                    ),
                    AgentDecision(
                        action="finish",
                        reason_summary="Planning is complete.",
                        output={"action": "finish_planning"},
                    ),
                )
            ),
            "retrieval": iter(
                (
                    AgentDecision(
                        action="tool",
                        reason_summary="Inspect the catalog.",
                        tool_name="list_operator_catalog",
                        tool_input={},
                    ),
                    AgentDecision(
                        action="finish",
                        reason_summary="Catalog evidence covers the constraint.",
                        output={
                            "candidate_operator_ids": [
                                "builtin.hard_constraint_evaluator:1",
                                "builtin.manifest:1",
                            ],
                            "constraint_assignments": [
                                {
                                    "constraint_id": (
                                        "constraint_arbitrary_metric"
                                    ),
                                    "operator_version_id": (
                                        "builtin.hard_constraint_evaluator:1"
                                    ),
                                }
                            ],
                            "sufficient": True,
                            "gaps": [],
                        },
                    ),
                )
            ),
            "processing": iter(
                (
                    AgentDecision(
                        action="tool",
                        reason_summary="Compile three ordered proposals.",
                        tool_name="compile_pipeline_variants",
                        tool_input={
                            "pipelines": [
                                {
                                    "strategy": strategy,
                                    "nodes": [
                                        {
                                            "operator_version_id": (
                                                "builtin.hard_constraint_evaluator:1"
                                            ),
                                            "parameters": {"min_width": 7},
                                            "constraint_ids": [
                                                "constraint_arbitrary_metric"
                                            ],
                                        },
                                        {
                                            "operator_version_id": (
                                                "builtin.manifest:1"
                                            ),
                                            "parameters": {},
                                            "constraint_ids": [],
                                        },
                                    ],
                                }
                                for strategy in (
                                    "retention_first",
                                    "balanced",
                                    "quality_first",
                                )
                            ]
                        },
                    ),
                    AgentDecision(
                        action="tool",
                        reason_summary="Trial the compiled proposals.",
                        tool_name="trial_pipeline_variants",
                        tool_input={},
                    ),
                    AgentDecision(
                        action="finish",
                        reason_summary="All variants passed validation.",
                        output={"use_compiled_variants": True},
                    ),
                )
            ),
            "strategy": iter(
                (
                    AgentDecision(
                        action="tool",
                        reason_summary="Inspect approved pipeline.",
                        tool_name="inspect_approved_pipeline",
                        tool_input={},
                    ),
                    AgentDecision(
                        action="tool",
                        reason_summary="Build execution strategy.",
                        tool_name="build_sampling_plan",
                        tool_input={"random_seed": 42},
                    ),
                    AgentDecision(
                        action="finish",
                        reason_summary="Strategy ready.",
                        output={"use_sampling_plan": True},
                    ),
                )
            ),
        }

    def decide(self, request: AgentPlanningRequest) -> AgentDecision:
        self.requests.append(request)
        return next(self._agent_steps[request.agent_name])


def test_work_order_runs_model_actions_and_specialist_tool_observations() -> None:
    planner = WorkOrderScriptedPlanner()
    runtime = AgentRuntime(
        include_datajuicer=False,
        requirement_planner=ArbitraryRequirementPlanner(),
        agent_planner=planner,
    )

    draft = runtime.start(
        owner_id="user_1",
        requirement="image width must be at least seven pixels",
        data_sources=[
            {"type": "local_directory", "uri": r"D:\data\arbitrary"},
        ],
    )
    planned = runtime.resume(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        decision={"approved": True},
    )

    assert planned["interrupts"][0]["value"]["kind"] == "pipeline_approval"
    assert {
        item["strategy"]
        for item in planned["state"]["representative_pipelines"]
    } == {"retention_first", "balanced", "quality_first"}
    assert [
        item["agent"] for item in planned["state"]["agent_observations"]
    ] == ["retrieval", "processing"]
    assert planned["state"]["capability_coverage"][0]["capability"] == (
        "image.width"
    )
    assert all(
        item["source"] == "model"
        for item in planned["state"]["main_agent_decisions"]
    )


def test_negative_run_feedback_returns_observation_to_main_agent_state(tmp_path) -> None:
    planner = WorkOrderScriptedPlanner()
    runtime = AgentRuntime(
        home=tmp_path,
        include_datajuicer=False,
        requirement_planner=ArbitraryRequirementPlanner(),
        agent_planner=planner,
    )

    draft = runtime.start(
        owner_id="user_1",
        requirement="image width must be at least seven pixels",
        data_sources=[
            {"type": "local_directory", "uri": r"D:\data\arbitrary"},
        ],
    )
    planned = runtime.resume(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        decision={"approved": True},
    )
    balanced = next(
        item
        for item in planned["state"]["representative_pipelines"]
        if item["strategy"] == "balanced"
    )
    ready = runtime.resume(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        decision={"approved": True, "pipeline_id": balanced["id"]},
    )
    assert ready["state"]["next_action"] == "submit_dataset_run"
    run = runtime.submit_dataset_run(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        idempotency_key="feedback-run",
    )
    assert runtime.run_store is not None
    runtime.run_store.mark_failed(run["id"], "trial result was not acceptable")

    runtime.record_run_feedback(
        run_id=run["id"],
        owner_id="user_1",
        accepted=False,
        reusable=False,
        rating=1,
        comment="漏删太多，需要重新编排",
    )
    state = runtime.state(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
    )["state"]

    assert state["next_action"] == "generate_pipeline_candidates"
    assert state["selected_pipeline_id"] == ""
    assert state["representative_pipelines"] == []
    assert state["agent_observations"][-1] == {
        "agent": "main",
        "status": "feedback_received",
        "summary": "Negative run feedback requires Pipeline replanning.",
        "run_id": run["id"],
        "accepted": False,
        "rating": 1,
        "comment": "漏删太多，需要重新编排",
    }
