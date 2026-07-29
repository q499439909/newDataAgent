from __future__ import annotations

from PIL import Image

from dataagent.agents.runner import (
    AgentDecision,
    AgentRunner,
    AgentPlanningRequest,
    AgentTool,
)
from dataagent.agents.requirement import decide_requirement_agent_turn
from dataagent.agents.retrieval.nodes import generate_retrieval_plan
from dataagent.agents.processing.nodes import generate_pipeline_variants
from dataagent.agents.strategy.nodes import generate_sampling_plan
from dataagent.domain.common import new_id
from dataagent.domain.specs import TaskSpecVersion
from dataagent.operators import build_operator_library
from dataagent.execution.pipeline_trial import PipelineTrialRunner
import pytest


class ScriptedPlanner:
    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions = list(decisions)
        self.requests: list[AgentPlanningRequest] = []

    def decide(self, request: AgentPlanningRequest) -> AgentDecision:
        self.requests.append(request)
        return self.decisions.pop(0)


def test_agent_uses_tool_observation_before_finishing() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Need catalog evidence.",
                tool_name="search_catalog",
                tool_input={"query": "visible object count"},
            ),
            AgentDecision(
                action="finish",
                reason_summary="The candidate is supported by catalog evidence.",
                output={"candidate_ids": ["operator_object_counter_v1"]},
            ),
        ]
    )
    loop = AgentRunner(
        agent_name="retrieval",
        planner=planner,
        tools=(
            AgentTool(
                name="search_catalog",
                description="Search operators by capability metadata.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                execute=lambda payload: {
                    "candidates": [
                        {
                            "id": "operator_object_counter_v1",
                            "description": "Counts visible objects.",
                        }
                    ]
                },
            ),
        ),
        max_iterations=3,
    )

    result = loop.run(goal="Find a supported operator", context={"task_id": "task_1"})

    assert result.output == {"candidate_ids": ["operator_object_counter_v1"]}
    assert result.observations[0].tool_name == "search_catalog"
    assert planner.requests[1].observations[0].data["candidates"][0]["id"] == (
        "operator_object_counter_v1"
    )
    assert [item.action for item in result.decisions] == ["tool", "finish"]


def test_agent_observes_an_unavailable_tool_and_can_choose_an_allowed_tool() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Try an unavailable environment tool.",
                tool_name="list_directory",
                tool_input={},
            ),
            AgentDecision(
                action="tool",
                reason_summary="Use the governed planning tool instead.",
                tool_name="inspect_context",
                tool_input={},
            ),
            AgentDecision(
                action="finish",
                reason_summary="The governed observation is sufficient.",
                output={"ok": True},
            ),
        ]
    )
    loop = AgentRunner(
        agent_name="requirement",
        planner=planner,
        tools=(
            AgentTool(
                name="inspect_context",
                description="Inspect governed task context.",
                input_schema={"type": "object", "additionalProperties": False},
                execute=lambda payload: {"ok": True},
            ),
        ),
        max_iterations=4,
    )

    result = loop.run(goal="Use only governed tools", context={})

    assert result.status == "finished"
    assert result.observations[0].data == {
        "ok": False,
        "error_type": "unavailable_tool",
        "error": "Tool is not available to this Agent",
        "available_tools": ["inspect_context"],
    }
    assert planner.requests[1].observations[0].tool_name == "list_directory"


def test_agent_tool_can_compact_large_inputs_in_model_observations() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Validate a large draft.",
                tool_name="validate_draft",
                tool_input={"items": [{"value": index} for index in range(20)]},
            ),
            AgentDecision(
                action="finish",
                reason_summary="The validation result is sufficient.",
                output={"ok": True},
            ),
        ]
    )
    loop = AgentRunner(
        agent_name="requirement",
        planner=planner,
        tools=(
            AgentTool(
                name="validate_draft",
                description="Validate a draft.",
                input_schema={"type": "object"},
                execute=lambda payload: {"ok": False, "violations": ["missing"]},
                summarize_input=lambda payload: {
                    "item_count": len(payload["items"])
                },
            ),
        ),
        max_iterations=3,
    )

    loop.run(goal="Validate the draft", context={})

    assert planner.requests[1].observations[0].tool_input == {"item_count": 20}


def test_requirement_root_selects_the_next_specialist_from_state() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="finish",
                reason_summary="The confirmed task needs governed candidates.",
                output={"action": "run_retrieval_agent"},
            )
        ]
    )

    result = decide_requirement_agent_turn(
        {
            "task_spec": {"id": "spec_1"},
            "task_spec_confirmed": True,
            "requirement_agent_decisions": [],
            "agent_observations": [
                {
                    "agent": "requirement",
                    "status": "finished",
                    "summary": "TaskSpec confirmed.",
                }
            ],
        },
        planner=planner,
    )

    assert result["requirement_agent_action"] == "run_retrieval_agent"
    assert result["requirement_agent_decisions"][0]["source"] == "model"
    assert planner.requests[0].context["observations"][0]["agent"] == "requirement"


@pytest.mark.parametrize(
    "case_state",
    [
        pytest.param(
            {
                "task_spec": {
                    "id": "spec_image_visual_filter",
                    "constraints": [
                        {
                            "id": "C08",
                            "field": "image.subject_clothing_color",
                        }
                    ],
                },
                "retrieval_plan": {"id": "retrieval_plan_image"},
            },
            id="current_image_visual_filter",
        ),
        pytest.param(
            {
                "task_spec": {
                    "id": "spec_text_filter",
                    "constraints": [
                        {
                            "id": "C01",
                            "field": "document.character_count",
                        }
                    ],
                },
                "retrieval_plan": {"id": "retrieval_plan_text"},
            },
            id="generalized_text_filter",
        ),
        pytest.param(
            {
                "task_spec": {
                    "id": "spec_audio_filter",
                    "constraints": [
                        {
                            "id": "C01",
                            "field": "audio.duration",
                        }
                    ],
                },
                "retrieval_plan": {"id": "retrieval_plan_audio"},
            },
            id="generalized_audio_filter",
        ),
    ],
)
def test_requirement_root_rejects_finish_before_pipeline_compilation(
    case_state: dict,
) -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="finish",
                reason_summary="Retrieval is complete, so planning can finish.",
                output={"action": "finish_planning"},
            ),
            AgentDecision(
                action="tool",
                reason_summary="Pipeline variants still need compilation.",
                tool_name="run_processing_agent",
            ),
        ]
    )

    result = decide_requirement_agent_turn(
        {
            "task_spec": case_state["task_spec"],
            "task_spec_confirmed": True,
            "retrieval_plan": case_state["retrieval_plan"],
            "candidate_sufficient": True,
            "representative_pipelines": [],
            "requirement_agent_decisions": [],
            "agent_observations": [
                {
                    "agent": "retrieval",
                    "status": "finished",
                    "summary": "All requested constraints have candidates.",
                }
            ],
        },
        planner=planner,
    )

    assert result["requirement_agent_action"] == "run_processing_agent"
    assert "finish_planning" not in planner.requests[0].context["allowed_actions"]
    assert planner.requests[1].context["policy_observations"] == [
        {
            "error": "FINISH_BEFORE_COMPLETION_GATES",
            "proposed": "finish_planning",
            "required_next_action": "run_processing_agent",
            "message": "Candidate coverage is sufficient for Pipeline compilation.",
        }
    ]


def test_requirement_root_does_not_process_insufficient_candidates() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Coverage gaps must be resolved first.",
                tool_name="resolve_capability_gaps",
            )
        ]
    )

    result = decide_requirement_agent_turn(
        {
            "task_spec": {"id": "spec_1"},
            "task_spec_confirmed": True,
            "retrieval_plan": {"id": "retrieval_plan_1"},
            "candidate_sufficient": False,
            "representative_pipelines": [],
            "requirement_agent_decisions": [],
        },
        planner=planner,
    )

    assert result["requirement_agent_action"] == "resolve_capability_gaps"
    assert "run_processing_agent" not in planner.requests[0].context["allowed_actions"]
    assert "finish_planning" not in planner.requests[0].context["allowed_actions"]


def test_requirement_root_routes_to_approval_after_compilation() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Compiled variants need user approval.",
                tool_name="approve_pipeline",
            )
        ]
    )

    result = decide_requirement_agent_turn(
        {
            "task_spec": {"id": "spec_1"},
            "task_spec_confirmed": True,
            "retrieval_plan": {"id": "retrieval_plan_1"},
            "candidate_sufficient": True,
            "representative_pipelines": [{"id": "pipeline_1"}],
            "selected_pipeline_id": "",
            "requirement_agent_decisions": [],
        },
        planner=planner,
    )

    assert result["requirement_agent_action"] == "approve_pipeline"
    assert "finish_planning" not in planner.requests[0].context["allowed_actions"]


def test_retrieval_agent_maps_constraints_to_catalog_evidence_without_fake_capability() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Inspect the governed catalog.",
                tool_name="list_operator_catalog",
                tool_input={},
            ),
            AgentDecision(
                action="finish",
                reason_summary="A generic hard-constraint evaluator covers the observable.",
                output={
                    "candidate_operator_ids": [
                        "builtin.hard_constraint_evaluator:1",
                        "builtin.manifest:1",
                    ],
                    "constraint_assignments": [
                        {
                            "constraint_id": "constraint_alpha",
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
    )
    library = build_operator_library(include_datajuicer=False)
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_1",
        objective="Retain assets whose width is at least four pixels.",
        data_sources=(
            {"type": "local_directory", "uri": r"D:\data\generic"},
        ),
        constraints=(
            {
                "id": "constraint_alpha",
                "source_text": "width is at least four pixels",
                "scope": "asset",
                "field": "image.width",
                "operator": "gte",
                "value": 4,
                "unit": "px",
                "required_evidence_type": "image_width",
            },
        ),
        confirmed=True,
    )

    result = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=library.registry,
        planner=planner,
    )

    assert result["candidate_sufficient"] is True
    assert result["capability_coverage"][0]["capability"] == (
        "image.width"
    )
    assert not result["capability_coverage"][0]["capability"].startswith(
        "constraint:"
    )
    assert [item.tool_name for item in planner.requests[1].observations] == [
        "list_operator_catalog"
    ]


def test_processing_agent_chooses_operator_order_and_parameters_through_tool_loop() -> None:
    proposals = [
        {
            "strategy": strategy,
            "nodes": [
                {
                    "operator_version_id": "builtin.hard_constraint_evaluator:1",
                    "parameters": {"min_width": 4},
                    "constraint_ids": ["constraint_alpha"],
                },
                {
                    "operator_version_id": "builtin.manifest:1",
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
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Compile the three proposed ordered pipelines.",
                tool_name="compile_pipeline_variants",
                tool_input={"pipelines": proposals},
            ),
            AgentDecision(
                action="tool",
                reason_summary="Trial the compiled artifacts before offering them.",
                tool_name="trial_pipeline_variants",
                tool_input={},
            ),
            AgentDecision(
                action="finish",
                reason_summary="All three artifacts passed deterministic validation.",
                output={"use_compiled_variants": True},
            ),
        ]
    )
    library = build_operator_library(include_datajuicer=False)
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_1",
        objective="Retain assets whose width is at least four pixels.",
        data_sources=(
            {"type": "local_directory", "uri": r"D:\data\generic"},
        ),
        constraints=(
            {
                "id": "constraint_alpha",
                "source_text": "width is at least four pixels",
                "scope": "asset",
                "field": "image.width",
                "operator": "gte",
                "value": 4,
                "unit": "px",
                "required_evidence_type": "image_width",
            },
        ),
        confirmed=True,
    )
    candidates = [
        {
            "intent": "image.width",
            "capability": "image.width",
            "operator_version_id": "builtin.hard_constraint_evaluator:1",
            "provider_id": "native",
            "provider_operator_ref": "hard_constraint_evaluator",
            "display_name": "Hard Constraint Evaluator",
            "score": 0,
            "runtime_backend": "cpu",
            "status": "PUBLIC_RELEASE",
            "executable": True,
        },
        {
            "intent": "manifest",
            "capability": "manifest",
            "operator_version_id": "builtin.manifest:1",
            "provider_id": "native",
            "provider_operator_ref": "manifest",
            "display_name": "Manifest",
            "score": 0,
            "runtime_backend": "cpu",
            "status": "PUBLIC_RELEASE",
            "executable": True,
        },
    ]

    result = generate_pipeline_variants(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "operator_candidates": candidates,
            "capability_coverage": [],
            "trace": [],
        },
        operator_library=library,
        planner=planner,
    )

    assert len(result["pipeline_variants"]) == 3
    assert [
        node["operator_version_id"]
        for node in result["pipeline_variants"][0]["nodes"]
    ] == [
        "builtin.hard_constraint_evaluator:1",
        "builtin.manifest:1",
    ]
    assert planner.requests[1].observations[0].tool_name == (
        "compile_pipeline_variants"
    )
    assert planner.requests[2].observations[1].tool_name == (
        "trial_pipeline_variants"
    )


def test_processing_agent_repairs_pipeline_after_trial_observation() -> None:
    incomplete = [
        {
            "strategy": strategy,
            "nodes": [
                {
                    "operator_version_id": "builtin.manifest:1",
                    "parameters": {},
                    "constraint_ids": [],
                }
            ],
        }
        for strategy in (
            "retention_first",
            "balanced",
            "quality_first",
        )
    ]
    repaired = [
        {
            "strategy": strategy,
            "nodes": [
                {
                    "operator_version_id": "builtin.hard_constraint_evaluator:1",
                    "parameters": {"min_width": 4},
                    "constraint_ids": ["constraint_alpha"],
                },
                {
                    "operator_version_id": "builtin.manifest:1",
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
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Try the historical template first.",
                tool_name="compile_pipeline_variants",
                tool_input={"pipelines": incomplete},
            ),
            AgentDecision(
                action="tool",
                reason_summary="Repair from the validator observation.",
                tool_name="compile_pipeline_variants",
                tool_input={"pipelines": repaired},
            ),
            AgentDecision(
                action="tool",
                reason_summary="Trial the repaired variants.",
                tool_name="trial_pipeline_variants",
                tool_input={},
            ),
            AgentDecision(
                action="finish",
                reason_summary="The repaired variants passed trial validation.",
                output={"use_compiled_variants": True},
            ),
        ]
    )
    library = build_operator_library(include_datajuicer=False)
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_1",
        objective="Retain assets whose width is at least four pixels.",
        data_sources=(
            {"type": "local_directory", "uri": r"D:\data\generic"},
        ),
        constraints=(
            {
                "id": "constraint_alpha",
                "source_text": "width is at least four pixels",
                "scope": "asset",
                "field": "image.width",
                "operator": "gte",
                "value": 4,
                "unit": "px",
                "required_evidence_type": "image_width",
            },
        ),
        confirmed=True,
    )
    candidates = [
        {
            "intent": "image.width",
            "capability": "image.width",
            "operator_version_id": "builtin.hard_constraint_evaluator:1",
            "provider_id": "native",
            "provider_operator_ref": "hard_constraint_evaluator",
            "display_name": "Hard Constraint Evaluator",
            "score": 0,
            "runtime_backend": "cpu",
            "status": "PUBLIC_RELEASE",
            "executable": True,
        },
        {
            "intent": "manifest",
            "capability": "manifest",
            "operator_version_id": "builtin.manifest:1",
            "provider_id": "native",
            "provider_operator_ref": "manifest",
            "display_name": "Manifest",
            "score": 0,
            "runtime_backend": "cpu",
            "status": "PUBLIC_RELEASE",
            "executable": True,
        },
    ]

    result = generate_pipeline_variants(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "operator_candidates": candidates,
            "capability_coverage": [
                {
                    "capability_id": "constraint_alpha",
                    "capability": "image.width",
                    "description": "width is at least four pixels",
                    "required": True,
                    "status": "covered",
                    "selected_operator_version_id": (
                        "builtin.hard_constraint_evaluator:1"
                    ),
                    "candidates": [],
                }
            ],
            "latest_run_feedback": {
                "run_id": "run_failed",
                "accepted": False,
                "comment": "Too many missed rejects",
            },
            "trace": [],
        },
        operator_library=library,
        planner=planner,
    )

    assert len(result["pipeline_variants"]) == 3
    assert planner.requests[0].context["latest_run_feedback"]["run_id"] == (
        "run_failed"
    )
    assert planner.requests[1].observations[-1].tool_name == (
        "compile_pipeline_variants"
    )
    assert "constraint_alpha" in planner.requests[1].observations[-1].data[
        "error"
    ]
    assert planner.requests[3].observations[-1].data["ok"] is True


def test_processing_agent_repairs_from_real_sample_trial_observation(
    tmp_path,
) -> None:
    source = tmp_path / "asset.png"
    Image.new("RGB", (80, 80), color="white").save(source)
    incomplete = [
        {
            "strategy": strategy,
            "nodes": [
                {
                    "operator_version_id": "builtin.manifest:1",
                    "parameters": {},
                    "constraint_ids": ["constraint_alpha"],
                }
            ],
        }
        for strategy in ("retention_first", "balanced", "quality_first")
    ]
    repaired = [
        {
            "strategy": strategy,
            "nodes": [
                {
                    "operator_version_id": "builtin.decode_check:1",
                    "parameters": {},
                    "constraint_ids": ["constraint_alpha"],
                },
                {
                    "operator_version_id": "builtin.manifest:1",
                    "parameters": {},
                    "constraint_ids": [],
                },
            ],
        }
        for strategy in ("retention_first", "balanced", "quality_first")
    ]
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Compile the initial candidates.",
                tool_name="compile_pipeline_variants",
                tool_input={"pipelines": incomplete},
            ),
            AgentDecision(
                action="tool",
                reason_summary="Run the initial candidates on real samples.",
                tool_name="trial_pipeline_variants",
                tool_input={},
            ),
            AgentDecision(
                action="tool",
                reason_summary="Repair the missing Evidence observation.",
                tool_name="compile_pipeline_variants",
                tool_input={"pipelines": repaired},
            ),
            AgentDecision(
                action="tool",
                reason_summary="Run the repaired candidates.",
                tool_name="trial_pipeline_variants",
                tool_input={},
            ),
            AgentDecision(
                action="finish",
                reason_summary="The repaired candidates have real Evidence.",
                output={"use_compiled_variants": True},
            ),
        ]
    )
    library = build_operator_library(include_datajuicer=False)
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_1",
        objective="Retain assets whose width is at least 64 pixels.",
        data_sources=(
            {"type": "local_directory", "uri": str(tmp_path)},
        ),
        constraints=(
            {
                "id": "constraint_alpha",
                "source_text": "width is at least 64 pixels",
                "scope": "asset",
                "field": "image.width",
                "operator": "gte",
                "value": 64,
                "unit": "pixel",
                "required_evidence_type": "image_metadata",
            },
        ),
        confirmed=True,
    )
    candidates = [
        {
            "intent": capability,
            "capability": capability,
            "operator_version_id": operator_id,
            "provider_id": "native",
            "provider_operator_ref": operator_id,
            "display_name": operator_id,
            "score": 0,
            "runtime_backend": "cpu",
            "status": "PUBLIC_RELEASE",
            "executable": True,
        }
        for capability, operator_id in (
            ("image.width", "builtin.decode_check:1"),
            ("manifest", "builtin.manifest:1"),
        )
    ]

    result = generate_pipeline_variants(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "operator_candidates": candidates,
            "capability_coverage": [],
            "trace": [],
        },
        operator_library=library,
        planner=planner,
        trial_runner=PipelineTrialRunner(
            library,
            trial_root=tmp_path / "trials",
        ),
    )

    failed_trial = planner.requests[2].observations[-1]
    passed_trial = planner.requests[4].observations[-1]
    assert failed_trial.tool_name == "trial_pipeline_variants"
    assert failed_trial.data["ok"] is False
    assert {
        item["failure_code"]
        for pipeline in failed_trial.data["pipelines"]
        for item in pipeline["constraint_results"]
    } == {"MISSING_EVIDENCE"}
    assert passed_trial.data["ok"] is True
    assert all(
        pipeline["status"] == "passed"
        for pipeline in passed_trial.data["pipelines"]
    )
    assert len(result["pipeline_variants"]) == 3


def test_strategy_agent_uses_tool_observation_to_build_sampling_plan() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Inspect the approved Pipeline first.",
                tool_name="inspect_approved_pipeline",
                tool_input={},
            ),
            AgentDecision(
                action="tool",
                reason_summary="Build a deterministic strategy from observations.",
                tool_name="build_sampling_plan",
                tool_input={
                    "quota": {"train": 100},
                    "priority_weights": {"failure_neighbor": 2.0},
                    "diversity_constraints": {
                        "max_per_visual_cluster": 25,
                    },
                    "random_seed": 99,
                },
            ),
            AgentDecision(
                action="finish",
                reason_summary="The sampling plan is ready.",
                output={"use_sampling_plan": True},
            ),
        ]
    )
    spec = TaskSpecVersion(
        id="spec_strategy",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_strategy",
        objective="Retain images",
        data_sources=(
            {"type": "local_directory", "uri": r"D:\data\generic"},
        ),
        quotas={"train": 100},
        confirmed=True,
    )
    pipeline = {
        "id": "pipeline_approved",
        "family_id": "pipeline_balanced",
        "version": 2,
        "created_by": "user_1",
        "change_reason": "approved",
        "strategy": "balanced",
        "task_spec_version_id": spec.id,
        "nodes": [
            {
                "id": "manifest",
                "operator_version_id": "builtin.manifest:1",
                "name": "Manifest",
                "category": "output",
                "parameters": {},
                "runtime_backend": "cpu",
                "required": True,
            }
        ],
        "edges": [],
        "created_from": "test",
        "approved": True,
    }

    result = generate_sampling_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "approved_pipeline": pipeline,
            "selected_pipeline_id": "pipeline_approved",
            "trace": [],
        },
        planner=planner,
    )

    assert result["sampling_plan"]["random_seed"] == 99
    assert result["agent_observations"][-1]["agent"] == "strategy"
    assert planner.requests[1].observations[0].tool_name == (
        "inspect_approved_pipeline"
    )


@pytest.mark.parametrize(
    ("field", "parameter"),
    (
        ("image.width", "min_width"),
        ("image.height", "min_height"),
    ),
)
def test_same_agent_contract_generalizes_across_observables(
    field: str,
    parameter: str,
) -> None:
    constraint_id = "constraint_generated"
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="finish",
                reason_summary="Catalog metadata supports the observable.",
                output={
                    "candidate_operator_ids": [
                        "builtin.hard_constraint_evaluator:1",
                        "builtin.manifest:1",
                    ],
                    "constraint_assignments": [
                        {
                            "constraint_id": constraint_id,
                            "operator_version_id": (
                                "builtin.hard_constraint_evaluator:1"
                            ),
                        }
                    ],
                    "sufficient": True,
                    "gaps": [],
                },
            )
        ]
    )
    library = build_operator_library(include_datajuicer=False)
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by="user_1",
        change_reason="generalization",
        work_order_id="work_generalization",
        objective=f"{field} is at least 17 pixels",
        data_sources=(
            {"type": "local_directory", "uri": r"D:\data\generic"},
        ),
        constraints=(
            {
                "id": constraint_id,
                "source_text": f"{field} is at least 17 pixels",
                "scope": "asset",
                "field": field,
                "operator": "gte",
                "value": 17,
                "unit": "px",
                "required_evidence_type": field.replace(".", "_"),
            },
        ),
        confirmed=True,
    )

    result = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=library.registry,
        planner=planner,
    )

    operator = library.registry.get(
        result["capability_coverage"][0]["selected_operator_version_id"]
    )
    assert parameter in operator.parameter_schema["properties"]


def test_invalid_constraint_assignment_becomes_observation_and_is_replanned() -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="finish",
                reason_summary="Initial proposal.",
                output={
                    "candidate_operator_ids": [
                        "builtin.decode_check:1",
                        "builtin.manifest:1",
                    ],
                    "constraint_assignments": [
                        {
                            "constraint_id": "constraint_width",
                            "operator_version_id": "builtin.decode_check:1",
                        }
                    ],
                    "sufficient": True,
                    "gaps": [],
                },
            ),
            AgentDecision(
                action="finish",
                reason_summary="Repaired from validator observation.",
                output={
                    "candidate_operator_ids": [
                        "builtin.hard_constraint_evaluator:1",
                        "builtin.manifest:1",
                    ],
                    "constraint_assignments": [
                        {
                            "constraint_id": "constraint_width",
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
    )
    library = build_operator_library(include_datajuicer=False)
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by="user_1",
        change_reason="boundary",
        work_order_id="work_boundary",
        objective="image width is at least 64 pixels",
        data_sources=(
            {"type": "local_directory", "uri": r"D:\data\generic"},
        ),
        constraints=(
            {
                "id": "constraint_width",
                "source_text": "image width is at least 64 pixels",
                "scope": "asset",
                "field": "image.width",
                "operator": "gte",
                "value": 64,
                "unit": "px",
                "required_evidence_type": "image_width",
            },
        ),
        confirmed=True,
    )

    result = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=library.registry,
        planner=planner,
    )

    validation = planner.requests[1].observations[-1]
    assert validation.tool_name == "validate_finish"
    assert validation.data["errors"][0]["code"] == "UNSUPPORTED_CONSTRAINT"
    assert result["capability_coverage"][0]["selected_operator_version_id"] == (
        "builtin.hard_constraint_evaluator:1"
    )
