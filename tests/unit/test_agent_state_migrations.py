from dataagent.graph.state_migrations import (
    CURRENT_AGENT_STATE_VERSION,
    migrate_work_order_state,
)


def test_migrates_legacy_main_agent_checkpoint_fields() -> None:
    migrated = migrate_work_order_state(
        {
            "work_order_id": "work_1",
            "main_agent_action": "run_processing_agent",
            "main_agent_decisions": [
                {"sequence": 1, "action": "run_processing_agent"}
            ],
        }
    )

    assert migrated["agent_state_version"] == CURRENT_AGENT_STATE_VERSION
    assert migrated["requirement_agent_action"] == "run_processing_agent"
    assert migrated["requirement_agent_decisions"] == [
        {"sequence": 1, "action": "run_processing_agent"}
    ]
    assert "main_agent_action" not in migrated
    assert "main_agent_decisions" not in migrated


def test_current_checkpoint_is_semantically_unchanged() -> None:
    current = {
        "agent_state_version": CURRENT_AGENT_STATE_VERSION,
        "work_order_id": "work_2",
        "requirement_agent_action": "run_retrieval_agent",
        "requirement_agent_decisions": [
            {"sequence": 1, "action": "run_retrieval_agent"}
        ],
    }

    assert migrate_work_order_state(current) == current


def test_canonical_fields_win_when_legacy_checkpoint_conflicts() -> None:
    migrated = migrate_work_order_state(
        {
            "work_order_id": "work_3",
            "requirement_agent_action": "confirm_task_spec",
            "requirement_agent_decisions": [
                {"sequence": 2, "action": "confirm_task_spec"}
            ],
            "main_agent_action": "run_processing_agent",
            "main_agent_decisions": [
                {"sequence": 1, "action": "run_processing_agent"}
            ],
        }
    )

    assert migrated["requirement_agent_action"] == "confirm_task_spec"
    assert migrated["requirement_agent_decisions"] == [
        {"sequence": 2, "action": "confirm_task_spec"}
    ]
    assert "main_agent_action" not in migrated
    assert "main_agent_decisions" not in migrated
