from __future__ import annotations

from dataagent.application.conversation_actions import ConversationIntent
from dataagent.operators import build_operator_library
from dataagent.tools import (
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
