from __future__ import annotations

from dataagent.agents.runtime import (
    AgentDecision,
    AgentPlanningRequest,
)
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.domain.evaluations import QCReport, QCStatus
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
    def __init__(self, *, run_outcome_action: str | None = None) -> None:
        self.requests: list[AgentPlanningRequest] = []
        self._agent_steps = {
            "requirement_planning": iter(
                (
                    AgentDecision(
                        action="tool",
                        reason_summary="Validate the source-grounded requirement.",
                        tool_name="validate_requirement_draft",
                        tool_input={
                            "objective": (
                                "image width must be at least seven pixels"
                            ),
                            "constraints": [
                                {
                                    "id": "constraint_arbitrary_metric",
                                    "source_text": (
                                        "image width must be at least seven pixels"
                                    ),
                                    "scope": "asset",
                                    "field": "image.width",
                                    "operator": "gte",
                                    "value": 7,
                                    "unit": "px",
                                    "required_evidence_type": "image_width",
                                }
                            ],
                            "clause_traces": [
                                {
                                    "source_text": (
                                        "image width must be at least seven pixels"
                                    ),
                                    "role": "constraint",
                                    "constraint_refs": [
                                        "constraint_arbitrary_metric"
                                    ],
                                }
                            ],
                        },
                    ),
                    AgentDecision(
                        action="finish",
                        reason_summary="The Requirement Draft is grounded.",
                        output={"use_validated_draft": True},
                    ),
                )
            ),
            "requirement": iter(
                [
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
                    *(
                        [
                            AgentDecision(
                                action="tool",
                                reason_summary=(
                                    "The formal Run observation requires "
                                    f"{run_outcome_action}."
                                ),
                                tool_name=run_outcome_action,
                            ),
                            *(
                                [
                                    AgentDecision(
                                        action="tool",
                                        reason_summary=(
                                            "Compile Pipelines from the newly "
                                            "retrieved candidates."
                                        ),
                                        tool_name="run_processing_agent",
                                    )
                                ]
                                if run_outcome_action
                                == "reretrieve_candidates"
                                else []
                            ),
                            *(
                                [
                                    AgentDecision(
                                        action="tool",
                                        reason_summary=(
                                            "Ask the user to approve a "
                                            "repaired Pipeline."
                                        ),
                                        tool_name="approve_pipeline",
                                    )
                                ]
                                if run_outcome_action
                                in {
                                    "recompile_pipeline",
                                    "reretrieve_candidates",
                                    "ask_user",
                                }
                                else []
                            ),
                        ]
                        if run_outcome_action
                        else []
                    ),
                ]
            ),
            "retrieval": iter(
                [
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
                    *(
                        [
                            AgentDecision(
                                action="tool",
                                reason_summary=(
                                    "Inspect the catalog after formal failure."
                                ),
                                tool_name="list_operator_catalog",
                                tool_input={},
                            ),
                            AgentDecision(
                                action="finish",
                                reason_summary=(
                                    "Alternative catalog evidence covers "
                                    "the constraint."
                                ),
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
                        ]
                        if run_outcome_action == "reretrieve_candidates"
                        else []
                    ),
                ]
            ),
            "processing": iter(
                [
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
                    *(
                        [
                            AgentDecision(
                                action="tool",
                                reason_summary=(
                                    "Compile repaired ordered proposals."
                                ),
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
                                                    "parameters": {
                                                        "min_width": 7
                                                    },
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
                                reason_summary=(
                                    "Trial the repaired proposals."
                                ),
                                tool_name="trial_pipeline_variants",
                                tool_input={},
                            ),
                            AgentDecision(
                                action="finish",
                                reason_summary=(
                                    "Repaired variants passed validation."
                                ),
                                output={"use_compiled_variants": True},
                            ),
                        ]
                        if run_outcome_action
                        in {
                            "recompile_pipeline",
                            "reretrieve_candidates",
                            "ask_user",
                        }
                        else []
                    ),
                ]
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
    ] == ["requirement", "retrieval", "processing"]
    assert planned["state"]["capability_coverage"][0]["capability"] == (
        "image.width"
    )
    assert all(
        item["source"] == "model"
        for item in planned["state"]["requirement_agent_decisions"]
    )


def test_negative_run_feedback_returns_observation_to_requirement_agent(
    tmp_path,
) -> None:
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
        "agent": "requirement",
        "status": "feedback_received",
        "summary": "Negative run feedback requires Pipeline replanning.",
        "run_id": run["id"],
        "accepted": False,
        "rating": 1,
        "comment": "漏删太多，需要重新编排",
    }


def test_requirement_agent_recompiles_after_formal_run_observation(
    tmp_path,
) -> None:
    planner = WorkOrderScriptedPlanner(
        run_outcome_action="recompile_pipeline",
    )
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
    run = runtime.submit_dataset_run(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        idempotency_key="formal-run-recompile",
    )
    assert runtime.run_store is not None
    runtime.run_store.mark_failed(
        run["id"],
        "document OCR evidence was not produced",
    )

    observed = runtime.observe_run_outcome(
        work_order_id=draft["work_order_id"],
        run_id=run["id"],
        owner_id="user_1",
    )

    assert observed["state"]["requirement_agent_decisions"][-2]["action"] == (
        "recompile_pipeline"
    )
    assert observed["state"]["requirement_agent_decisions"][-2]["source"] == "model"
    assert observed["state"]["selected_pipeline_id"] == ""
    assert observed["state"]["next_action"] == "approve_pipeline"
    assert observed["interrupts"][0]["value"]["kind"] == "pipeline_approval"


def test_requirement_agent_reretrieves_after_different_formal_failure(
    tmp_path,
) -> None:
    planner = WorkOrderScriptedPlanner(
        run_outcome_action="reretrieve_candidates",
    )
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
    quality = next(
        item
        for item in planned["state"]["representative_pipelines"]
        if item["strategy"] == "quality_first"
    )
    runtime.resume(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        decision={"approved": True, "pipeline_id": quality["id"]},
    )
    run = runtime.submit_dataset_run(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        idempotency_key="formal-run-reretrieve",
    )
    assert runtime.run_store is not None
    runtime.run_store.mark_failed(
        run["id"],
        "vehicle counter provider is unavailable",
    )

    observed = runtime.observe_run_outcome(
        work_order_id=draft["work_order_id"],
        run_id=run["id"],
        owner_id="user_1",
    )

    assert any(
        item["action"] == "reretrieve_candidates"
        and item["source"] == "model"
        for item in observed["state"]["requirement_agent_decisions"]
    )
    retrieval_requests = [
        request
        for request in planner.requests
        if request.agent_name == "retrieval"
    ]
    assert len(retrieval_requests) == 4
    assert observed["interrupts"][0]["value"]["kind"] == "pipeline_approval"


def test_requirement_agent_completes_only_after_passed_qc_observation(
    tmp_path,
) -> None:
    planner = WorkOrderScriptedPlanner(
        run_outcome_action="complete_work_order",
    )
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
    runtime.resume(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        decision={"approved": True, "pipeline_id": balanced["id"]},
    )
    run = runtime.submit_dataset_run(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        idempotency_key="formal-run-complete",
    )
    assert runtime.run_store is not None
    assert runtime.version_store is not None
    runtime.run_store.mark_succeeded(run["id"], "dataset_complete")
    report = QCReport(
        id="qc_report_complete",
        version=1,
        created_by="test",
        change_reason="known passed outcome",
        work_order_id=draft["work_order_id"],
        dataset_version_id="dataset_complete",
        pipeline_version_id=run["pipeline_version_id"],
        task_spec_version_id=run["task_spec_version_id"],
        run_id=run["id"],
        evaluator_version="test:1",
        status=QCStatus.PASSED,
        full_hard_rule_check=True,
        semantic_quality_verified=True,
        metrics={
            "hard_rule_violation_rate": 0.0,
            "retention_rate": 1.0,
            "execution_failure_rate": 0.0,
        },
    )
    runtime.version_store.save_if_absent(
        kind="qc_report",
        owner_id="user_1",
        payload=report.model_dump(mode="json"),
    )

    observed = runtime.observe_run_outcome(
        work_order_id=draft["work_order_id"],
        run_id=run["id"],
        owner_id="user_1",
    )

    assert observed["state"]["next_action"] == "complete_work_order"
    assert observed["state"]["latest_run_observation"]["qc_status"] == (
        "PASSED"
    )
    assert observed["state"]["resolved_run_ids"] == [run["id"]]
    assert observed["interrupts"] == []
    task_plan = {
        item["id"]: item["status"]
        for item in observed["state"]["task_plan"]
    }
    assert task_plan["execute_dataset"] == "completed"
    assert task_plan["evaluate_quality"] == "completed"
    assert task_plan["resolve_run_outcome"] == "completed"


def test_requirement_agent_schedules_only_failed_assets_for_repair(
    tmp_path,
) -> None:
    planner = WorkOrderScriptedPlanner(
        run_outcome_action="retry_failed_assets",
    )
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
    runtime.resume(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        decision={"approved": True, "pipeline_id": balanced["id"]},
    )
    run = runtime.submit_dataset_run(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        idempotency_key="formal-run-retry",
    )
    assert runtime.run_store is not None
    runtime.run_store.initialize_plan(
        run["id"],
        [
            {
                "sequence": 0,
                "source_uri": "audio://passed.wav",
                "source_sha256": "passed",
                "output_relative_path": "passed.wav",
            },
            {
                "sequence": 1,
                "source_uri": "audio://failed.wav",
                "source_sha256": "failed",
                "output_relative_path": "failed.wav",
            },
        ],
    )
    runtime.run_store.add_item(
        run["id"],
        {
            "sequence": 0,
            "source_uri": "audio://passed.wav",
            "source_sha256": "passed",
            "decision": "keep",
        },
    )
    runtime.run_store.add_item(
        run["id"],
        {
            "sequence": 1,
            "source_uri": "audio://failed.wav",
            "source_sha256": "failed",
            "decision": "failed",
            "reason_codes": ["LOUDNESS_EVIDENCE_MISSING"],
        },
    )
    runtime.run_store.mark_failed(
        run["id"],
        "one asset requires repair",
    )

    observed = runtime.observe_run_outcome(
        work_order_id=draft["work_order_id"],
        run_id=run["id"],
        owner_id="user_1",
    )
    runs = runtime.list_runs(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
    )
    repair = next(item for item in runs if item["operation_kind"] == "repair")

    assert observed["state"]["next_action"] == "await_run_outcome"
    assert observed["state"]["active_run_id"] == repair["id"]
    assert repair["status"] == "QUEUED"
    assert [item["source_uri"] for item in repair["repair_scope"]] == [
        "audio://failed.wav"
    ]


def test_requirement_agent_can_schedule_a_new_production_run_after_cancel(
    tmp_path,
) -> None:
    planner = WorkOrderScriptedPlanner(
        run_outcome_action="rerun_pipeline",
    )
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
    runtime.resume(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        decision={"approved": True, "pipeline_id": balanced["id"]},
    )
    cancelled = runtime.submit_dataset_run(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        idempotency_key="formal-run-cancelled",
    )
    assert runtime.run_store is not None
    runtime.run_store.request_cancel(cancelled["id"], "user_1")

    observed = runtime.observe_run_outcome(
        work_order_id=draft["work_order_id"],
        run_id=cancelled["id"],
        owner_id="user_1",
    )
    runs = runtime.list_runs(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
    )
    rerun = next(item for item in runs if item["id"] != cancelled["id"])

    assert rerun["operation_kind"] == "production"
    assert rerun["pipeline_version_id"] == cancelled["pipeline_version_id"]
    assert rerun["status"] == "QUEUED"
    assert observed["state"]["active_run_id"] == rerun["id"]
    assert observed["state"]["next_action"] == "await_run_outcome"


def test_requirement_agent_can_pause_for_human_run_resolution(tmp_path) -> None:
    planner = WorkOrderScriptedPlanner(run_outcome_action="ask_user")
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
    runtime.resume(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        decision={"approved": True, "pipeline_id": balanced["id"]},
    )
    run = runtime.submit_dataset_run(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        idempotency_key="formal-run-human",
    )
    assert runtime.run_store is not None
    runtime.run_store.mark_failed(run["id"], "ambiguous quality outcome")

    waiting = runtime.observe_run_outcome(
        work_order_id=draft["work_order_id"],
        run_id=run["id"],
        owner_id="user_1",
    )

    assert waiting["interrupts"][0]["value"]["kind"] == (
        "run_outcome_resolution"
    )
    resumed = runtime.resume(
        work_order_id=draft["work_order_id"],
        owner_id="user_1",
        decision={"action": "recompile_pipeline"},
    )
    assert resumed["interrupts"][0]["value"]["kind"] == "pipeline_approval"
