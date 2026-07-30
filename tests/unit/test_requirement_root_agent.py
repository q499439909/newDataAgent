from __future__ import annotations

import pytest
from pydantic import ValidationError

from dataagent.agents.requirement import (
    assess_work_order_liveness,
    decide_requirement_agent_turn,
)
from dataagent.agents.turn import AgentAction, TurnInput, TurnResult


def test_turn_contracts_expose_explicit_source_and_stop_reason() -> None:
    turn = TurnInput(
        session_id="session_1",
        owner_id="owner_1",
        content="Continue the current WorkOrder.",
    )
    result = TurnResult(
        status="scheduled",
        work_order_id="work_1",
        stop_reason="turn_budget_exhausted",
    )

    assert turn.source == "user"
    assert result.stop_reason == "turn_budget_exhausted"


def test_delegate_action_requires_an_explicit_langgraph_target() -> None:
    with pytest.raises(ValidationError, match="delegate actions require target"):
        AgentAction(
            kind="delegate",
            reason="The next specialist must continue planning.",
        )


def test_requirement_root_agent_continues_pipeline_compilation() -> None:
    state = {
        "task_spec": {"id": "spec_current"},
        "task_spec_confirmed": True,
        "retrieval_plan": {"id": "retrieval_current"},
        "candidate_sufficient": True,
        "operator_plan": {"id": "operator_plan_current"},
        "operator_plan_confirmed": True,
        "representative_pipelines": [],
        "waiting": None,
        "next_action": "generate_pipeline_candidates",
    }

    result = decide_requirement_agent_turn(state)

    assert result["current_agent"] == "requirement"
    assert result["requirement_agent_action"] == "run_processing_agent"
    assert result["agent_action"] == {
        "kind": "delegate",
        "target": "processing_agent",
        "objective": (
            "Candidate coverage is sufficient for Pipeline compilation."
        ),
        "arguments": {},
        "reason": (
            "Candidate coverage is sufficient for Pipeline compilation."
        ),
    }
    assert assess_work_order_liveness(state).model_dump(mode="json") == {
        "status": "runnable",
        "reason": (
            "Candidate coverage is sufficient for Pipeline compilation."
        ),
        "required_action": "run_processing_agent",
    }


@pytest.mark.parametrize(
    ("state", "expected_route", "expected_target"),
    (
        pytest.param(
            {
                "task_spec": {"id": "spec_alpha"},
                "task_spec_confirmed": True,
                "retrieval_plan": {},
                "candidate_sufficient": False,
                "waiting": None,
            },
            "run_retrieval_agent",
            "retrieval_agent",
            id="different-domain-needs-capability-retrieval",
        ),
        pytest.param(
            {
                "task_spec": {"id": "spec_beta"},
                "task_spec_confirmed": True,
                "retrieval_plan": {"id": "retrieval_beta"},
                "candidate_sufficient": True,
                "operator_plan": {"id": "operator_plan_beta"},
                "operator_plan_confirmed": True,
                "representative_pipelines": [{"id": "pipeline_beta"}],
                "selected_pipeline_id": "pipeline_beta",
                "selected_pipeline_trial": {
                    "status": "static_validation_passed"
                },
                "sampling_plan": {},
                "waiting": None,
            },
            "run_strategy_agent",
            "strategy_agent",
            id="different-domain-needs-execution-strategy",
        ),
    ),
)
def test_requirement_root_agent_generalizes_from_structured_state(
    state: dict,
    expected_route: str,
    expected_target: str,
) -> None:
    result = decide_requirement_agent_turn(state)

    assert result["requirement_agent_action"] == expected_route
    assert result["agent_action"]["kind"] == "delegate"
    assert result["agent_action"]["target"] == expected_target
    assert assess_work_order_liveness(state).status == "runnable"


def test_requirement_root_agent_preserves_confirmation_interrupt() -> None:
    state = {
        "task_spec": {"id": "spec_boundary"},
        "task_spec_confirmed": False,
        "waiting": "task_spec_confirmation",
        "next_action": "confirm_task_spec",
    }

    result = decide_requirement_agent_turn(state)
    liveness = assess_work_order_liveness(state)

    assert result["requirement_agent_action"] == "confirm_task_spec"
    assert result["agent_action"]["kind"] == "ask_user"
    assert result["agent_action"]["target"] == "confirm_task_spec"
    assert liveness.status == "waiting"
    assert liveness.required_action == "confirm_task_spec"


def test_terminated_work_order_is_not_runnable() -> None:
    liveness = assess_work_order_liveness(
        {
            "terminated": True,
            "waiting": None,
            "next_action": "terminated",
        }
    )

    assert liveness.status == "terminal"
    assert liveness.required_action is None
