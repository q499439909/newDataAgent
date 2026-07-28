from __future__ import annotations

from dataagent.application.conversation_actions import parse_conversation_action
from dataagent.application.conversation_policy import (
    allowed_conversation_actions,
    validate_conversation_action,
)


def test_requirement_clarification_is_allowed_only_at_its_interrupt() -> None:
    context = {
        "work_order_id": "work_order_1",
        "agent_state": {
            "waiting": "requirement_clarification",
            "next_action": "clarify_requirement",
        },
    }

    allowed = allowed_conversation_actions(context)

    assert "CLARIFY_REQUIREMENT" in allowed
    assert "APPROVE" not in allowed


def test_semantically_unverified_pipeline_requires_recompilation() -> None:
    context = {
        "work_order_id": "work_order_1",
        "agent_state": {"waiting": None, "next_action": "submit_dataset_run"},
        "task_spec": {"confirmed": True},
        "latest_run": {"id": "run_1", "status": "SUCCEEDED"},
        "latest_run_pipeline_eligibility": {"eligible": True, "violations": []},
        "latest_qc_report": {"semantic_quality_verified": False},
    }
    action = parse_conversation_action({"intent": "RERUN_PIPELINE"})

    violation = validate_conversation_action(action, context)

    assert violation is not None
    assert violation.code == "SEMANTIC_RECOMPILE_REQUIRED"


def test_current_verified_pipeline_can_be_rerun() -> None:
    context = {
        "work_order_id": "work_order_1",
        "agent_state": {"waiting": None, "next_action": "submit_dataset_run"},
        "task_spec": {"confirmed": True},
        "latest_run": {"id": "run_1", "status": "SUCCEEDED"},
        "latest_run_pipeline_eligibility": {"eligible": True, "violations": []},
        "latest_qc_report": {"semantic_quality_verified": True},
    }
    action = parse_conversation_action({"intent": "RERUN_PIPELINE"})

    assert validate_conversation_action(action, context) is None
