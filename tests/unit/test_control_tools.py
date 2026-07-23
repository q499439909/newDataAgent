from __future__ import annotations

from dataagent.application.conversation_actions import ConversationIntent
from dataagent.domain.operators import OperatorStatus
from dataagent.domain.pipelines import PipelineNode, PipelineStrategy, PipelineVersion
from dataagent.operators import OperatorRegistry, build_operator_library
from dataagent.tools import (
    GovernedToolLoop,
    ToolContext,
    ToolRegistry,
    ToolResult,
    build_p0_tool_registry,
)


def _context(**updates) -> ToolContext:
    library = build_operator_library(include_datajuicer=False)
    values = {
        "owner_id": "user_1",
        "operator_registry": library.registry,
        "control_context": {},
        "control_facts": {},
    }
    values.update(updates)
    return ToolContext(**values)


def _pipeline(
    *,
    operator_version_id: str = "builtin.quality_filter:1",
    parameters: dict | None = None,
) -> PipelineVersion:
    return PipelineVersion(
        id="pipeline_1",
        family_id="pipeline_balanced",
        version=1,
        created_by="user_1",
        change_reason="tool test",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id="spec_1",
        nodes=(
            PipelineNode(
                id="quality",
                operator_version_id=operator_version_id,
                name="Quality",
                category="FILTERING",
                parameters=parameters or {"confidence_threshold": 0.55},
            ),
        ),
        created_from="processing_agent",
    )


def test_tool_input_validation_returns_complete_observation() -> None:
    registry = build_p0_tool_registry()

    result = registry.execute("retrieve_operators", _context(), {})

    assert result == ToolResult(
        ok=False,
        tool="retrieve_operators",
        status="failed",
        summary="Tool input validation failed.",
        data={},
        evidence=(),
        next_actions=(),
        requires_confirmation=False,
        error_type="input_validation_error",
    )


def test_governed_tool_loop_redacts_secrets_and_records_evidence() -> None:
    loop = GovernedToolLoop(build_p0_tool_registry())
    result, trace = loop.execute(
        name="query_control_facts",
        stage="inspect_control_facts",
        context=_context(
            control_facts={"run": {"id": "run_1", "status": "RUNNING"}}
        ),
        raw_input={"facets": ["run"]},
    )

    assert result.ok is True
    assert trace["tool"] == "query_control_facts"
    assert trace["display_name"] == "Control Fact Reader"
    assert trace["evidence_ids"] == ["run_1"]
    assert trace["duration_ms"] >= 0
    assert "thought" not in trace

    _, invalid_trace = loop.execute(
        name="propose_control_action",
        stage="validate_control_action",
        context=_context(control_context={"api_key": "do-not-store"}),
        raw_input={
            "action": {
                "intent": "CHAT",
                "reply": "",
                "api_key": "do-not-store",
            }
        },
    )
    assert "api_key" not in invalid_trace["parameters"]["action"]
    assert "do-not-store" not in str(invalid_trace)


def test_control_action_trace_summarizes_task_patch_instead_of_dumping_it() -> None:
    loop = GovernedToolLoop(build_p0_tool_registry())
    _, trace = loop.execute(
        name="propose_control_action",
        stage="validate_control_action",
        context=_context(
            control_context={
                "allowed_actions": ["START_WORK_ORDER"],
            }
        ),
        raw_input={
            "action": {
                "intent": "START_WORK_ORDER",
                "source": "D:/images",
                "requirement": "Filter and classify images",
                "task_spec_patch": {
                    "classification": {
                        "labels": [
                            {"id": "cat", "display_name": "Cat"},
                            {"id": "dog", "display_name": "Dog"},
                        ],
                        "mixed_label": "mixed",
                        "unknown_label": "unknown",
                    },
                    "semantic_requirements": [
                        "a detailed private requirement",
                        "another private requirement",
                    ],
                    "hard_constraints": {"preserve_source": True},
                },
            }
        },
    )

    patch = trace["parameters"]["action"]["task_spec_patch"]
    assert patch["classification"]["label_ids"] == ["cat", "dog"]
    assert patch["semantic_requirement_count"] == 2
    assert patch["hard_constraint_fields"] == ["preserve_source"]
    assert "a detailed private requirement" not in str(trace)


def test_tool_registry_rejects_duplicates_and_forbidden_tools() -> None:
    registry = build_p0_tool_registry()
    retrieve = registry.get("retrieve_operators")

    try:
        registry.register(retrieve)
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate tool registration was accepted")

    forbidden = retrieve.model_copy(update={"name": "execute_operator"})
    try:
        ToolRegistry().register(forbidden)
    except ValueError as exc:
        assert "forbidden" in str(exc)
    else:
        raise AssertionError("forbidden tool registration was accepted")

    assert "execute_operator" not in registry.names()
    assert "run_shell" not in registry.names()


def test_retrieve_operators_reads_the_registry_and_returns_evidence() -> None:
    registry = build_p0_tool_registry()

    result = registry.execute(
        "retrieve_operators",
        _context(),
        {
            "requirement": "Filter blurry images",
            "required_capabilities": ["image_quality"],
            "available_runtime_backends": ["cpu"],
            "limit": 5,
        },
    )

    assert result.ok is True
    assert result.status == "succeeded"
    assert result.data["operators"]
    assert any(
        item["operator_version_id"] == "builtin.quality_filter:1"
        for item in result.data["operators"]
    )
    assert all(item.kind == "operator_version" for item in result.evidence)


def test_query_control_facts_returns_only_grounded_requested_facets() -> None:
    registry = build_p0_tool_registry()
    context = _context(
        control_facts={
            "run": {"id": "run_1", "status": "PARTIAL"},
            "dataset": {"id": "dataset_1", "still_failed": 2},
            "secret": {"value": "must not leak"},
        }
    )

    result = registry.execute(
        "query_control_facts",
        context,
        {"facets": ["run", "dataset"]},
    )

    assert result.ok is True
    assert result.data["facts"] == {
        "run": {"id": "run_1", "status": "PARTIAL"},
        "dataset": {"id": "dataset_1", "still_failed": 2},
    }
    assert {item.id for item in result.evidence} == {"run_1", "dataset_1"}


def test_propose_control_action_returns_policy_violation_without_execution() -> None:
    registry = build_p0_tool_registry()
    context = _context(
        control_context={
            "work_order_id": "work_1",
            "agent_state": {
                "waiting": "pipeline_approval",
                "next_action": "approve_pipeline",
            },
            "task_spec": {"confirmed": True},
        }
    )

    result = registry.execute(
        "propose_control_action",
        context,
        {
            "action": {
                "intent": ConversationIntent.SUBMIT_RUN.value,
                "reply": "",
            }
        },
    )

    assert result.ok is False
    assert result.status == "policy_violation"
    assert result.error_type == "ACTION_NOT_ALLOWED_IN_CURRENT_STATE"
    assert result.data["executed"] is False
    assert "SELECT_PIPELINE" in result.next_actions


def test_pipeline_artifact_tools_compile_and_validate_released_pipeline() -> None:
    registry = build_p0_tool_registry()
    compiled = registry.execute(
        "compile_pipeline_artifact",
        _context(),
        {"pipeline": _pipeline().model_dump(mode="json")},
    )

    validated = registry.execute(
        "validate_pipeline_artifact",
        _context(),
        {"content": compiled.data["content"]},
    )

    assert compiled.ok is True
    assert compiled.data["approved"] is False
    assert validated.ok is True
    assert validated.data["schema_ok"] is True
    assert validated.data["checksum_ok"] is True
    assert validated.data["operators_ok"] is True
    assert validated.data["parameters_ok"] is True
    assert validated.data["production_eligible"] is True
    assert validated.data["blockers"] == []


def test_pipeline_artifact_validation_reports_checksum_mismatch() -> None:
    registry = build_p0_tool_registry()
    compiled = registry.execute(
        "compile_pipeline_artifact",
        _context(),
        {"pipeline": _pipeline().model_dump(mode="json")},
    )
    tampered = compiled.data["content"].replace(
        "confidence_threshold: 0.55",
        "confidence_threshold: 0.75",
    )

    result = registry.execute(
        "validate_pipeline_artifact",
        _context(),
        {"content": tampered},
    )

    assert result.ok is False
    assert result.error_type == "pipeline_artifact_checksum_mismatch"
    assert result.data["schema_ok"] is True
    assert result.data["checksum_ok"] is False
    assert result.data["blockers"][0]["code"] == "CHECKSUM_MISMATCH"


def test_pipeline_artifact_validation_reports_operator_and_parameter_blockers() -> None:
    tools = build_p0_tool_registry()
    context = _context()
    missing = tools.execute(
        "compile_pipeline_artifact",
        context,
        {
            "pipeline": _pipeline(
                operator_version_id="missing.operator:1"
            ).model_dump(mode="json")
        },
    )
    invalid = tools.execute(
        "compile_pipeline_artifact",
        context,
        {
            "pipeline": _pipeline(
                parameters={"confidence_threshold": 2.0}
            ).model_dump(mode="json")
        },
    )

    missing_result = tools.execute(
        "validate_pipeline_artifact",
        context,
        {"content": missing.data["content"]},
    )
    invalid_result = tools.execute(
        "validate_pipeline_artifact",
        context,
        {"content": invalid.data["content"]},
    )

    assert missing_result.data["operators_ok"] is False
    assert missing_result.data["blockers"][0]["code"] == "OPERATOR_UNAVAILABLE"
    assert invalid_result.data["parameters_ok"] is False
    assert invalid_result.data["blockers"][0]["code"] == "PARAMETER_SCHEMA_VIOLATION"


def test_pipeline_artifact_validation_never_promotes_draft_operator() -> None:
    library = build_operator_library(include_datajuicer=False)
    draft = library.registry.get("builtin.quality_filter:1").model_copy(
        update={
            "id": "datajuicer.image_quality_filter.remote_api:1",
            "family_id": "datajuicer.image_quality_filter.remote_api",
            "status": OperatorStatus.DRAFT,
        }
    )
    context = _context(operator_registry=OperatorRegistry((draft,)))
    tools = build_p0_tool_registry()
    compiled = tools.execute(
        "compile_pipeline_artifact",
        context,
        {
            "pipeline": _pipeline(
                operator_version_id=draft.id
            ).model_dump(mode="json")
        },
    )

    result = tools.execute(
        "validate_pipeline_artifact",
        context,
        {"content": compiled.data["content"]},
    )

    assert result.ok is False
    assert result.data["production_eligible"] is False
    assert result.data["operators"][0]["status"] == "DRAFT"
    assert result.data["blockers"][0]["code"] == "OPERATOR_NOT_RELEASED"
