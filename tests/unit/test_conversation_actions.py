from __future__ import annotations

import pytest

from dataagent.application.conversation_actions import (
    ConversationActionError,
    ConversationIntent,
    parse_conversation_action,
)


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "EDIT_TASK_SPEC", "reply": "修改。"},
        {"intent": "SELECT_PIPELINE", "reply": "选择。"},
        {"intent": "CONTROL_RUN", "reply": "控制。"},
        {"intent": "QUERY_CONTROL_FACTS", "facets": [], "reply": "查询。"},
        {"intent": "APPROVE", "strategy": "balanced", "reply": "确认。"},
        {"intent": "NOT_A_REAL_ACTION", "reply": "执行。"},
    ],
)
def test_invalid_action_shapes_are_rejected(payload) -> None:
    with pytest.raises(ConversationActionError):
        parse_conversation_action(payload)


def test_valid_actions_have_intent_specific_required_fields() -> None:
    edit = parse_conversation_action(
        {
            "intent": "EDIT_TASK_SPEC",
            "task_spec_patch": {
                "hard_constraints": {"disabled_capabilities": ["image_quality"]}
            },
        }
    )
    pipeline = parse_conversation_action(
        {"intent": "SELECT_PIPELINE", "strategy": "balanced"}
    )
    facts = parse_conversation_action(
        {"intent": "QUERY_CONTROL_FACTS", "facets": ["run", "pipeline"]}
    )

    assert edit.intent == ConversationIntent.EDIT_TASK_SPEC
    assert pipeline.strategy == "balanced"
    assert facts.facets == ("run", "pipeline")
