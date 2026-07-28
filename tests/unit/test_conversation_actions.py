from __future__ import annotations

import pytest

from dataagent.application.conversation_actions import (
    ConversationActionError,
    ConversationIntent,
    conversation_action_json_schema,
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
        {
            "intent": "START_WORK_ORDER",
            "requirement": "Classify images",
            "task_spec_patch": {"clasification": {"labels": []}},
        },
        {
            "intent": "EDIT_TASK_SPEC",
            "task_spec_patch": {
                "preferences": {"unknown_policy": "guess"}
            },
        },
        {
            "intent": "EDIT_TASK_SPEC",
            "task_spec_patch": {"semantic_requirements": [42]},
        },
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
    clarification = parse_conversation_action(
        {
            "intent": "CLARIFY_REQUIREMENT",
            "answer": "Use a similarity threshold of 0.8.",
        }
    )
    start = parse_conversation_action(
        {
            "intent": "START_WORK_ORDER",
            "requirement": "Classify pigs and dogs",
            "source": "D:/images",
            "task_spec_patch": {
                "classification": {
                    "labels": [
                        {"id": "pig", "display_name": "Pig"},
                        {"id": "dog", "display_name": "Dog"},
                    ]
                }
            },
        }
    )

    assert edit.intent == ConversationIntent.EDIT_TASK_SPEC
    assert pipeline.strategy == "balanced"
    assert facts.facets == ("run", "pipeline")
    assert clarification.answer == "Use a similarity threshold of 0.8."
    assert start.task_spec_patch is not None
    assert start.task_spec_patch.classification is not None
    assert start.task_spec_patch.classification.labels[0].id == "pig"


def test_task_spec_patch_normalizes_safe_model_shape_variants() -> None:
    action = parse_conversation_action(
        {
            "intent": "START_WORK_ORDER",
            "requirement": "Classify cats and dogs",
            "task_spec_patch": {
                "classification": {
                    "labels": [
                        {"id": "cat", "display_name": "Cat"},
                        {"id": "dog", "display_name": "Dog"},
                    ],
                    "mixed_label": {
                        "id": "mixed",
                        "display_name": "Mixed",
                    },
                    "unknown_label": {
                        "id": "unknown",
                        "display_name": "Unknown",
                    },
                },
                "semantic_requirements": {
                    "clarity": "Exclude blurry images",
                    "relevance": "Keep cats and dogs",
                },
            },
        }
    )

    assert action.task_spec_patch is not None
    assert action.task_spec_patch.classification is not None
    assert action.task_spec_patch.classification.mixed_label == "mixed"
    assert action.task_spec_patch.classification.unknown_label == "unknown"
    assert action.task_spec_patch.semantic_requirements == (
        "Exclude blurry images",
        "Keep cats and dogs",
    )


def test_model_schema_does_not_expose_internal_audit_metadata() -> None:
    schema_text = str(conversation_action_json_schema())

    assert "resolved_by" not in schema_text
    assert "fallback_reason" not in schema_text
    assert "TaskSpecPatch" in schema_text
    assert "ClassificationSpec" in schema_text
