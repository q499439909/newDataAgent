from __future__ import annotations

from dataagent.application.control_tools import WorkOrderControlTools


class FakeWorkOrderRuntime:
    def __init__(self) -> None:
        self.resumed = []

    def state(self, *, work_order_id, owner_id):
        return {
            "work_order_id": work_order_id,
            "owner_id": owner_id,
            "state": {},
            "interrupts": [],
        }

    def resume(self, *, work_order_id, owner_id, decision):
        self.resumed.append((work_order_id, owner_id, decision))
        return {
            "work_order_id": work_order_id,
            "thread_id": "thread_1",
            "state": {"next_action": "run_retrieval_agent"},
            "interrupts": [],
        }


def test_agent_and_ui_can_share_typed_work_order_control() -> None:
    runtime = FakeWorkOrderRuntime()
    controls = WorkOrderControlTools(runtime)

    result, trace = controls.execute(
        name="resume_work_order",
        owner_id="owner_1",
        raw_input={
            "work_order_id": "work_1",
            "decision": {"approved": True},
        },
    )

    assert result.ok is True
    assert runtime.resumed == [
        ("work_1", "owner_1", {"approved": True})
    ]
    assert trace["tool"] == "resume_work_order"
    assert trace["status"] == "succeeded"


def test_invalid_work_order_control_input_never_reaches_runtime() -> None:
    runtime = FakeWorkOrderRuntime()
    controls = WorkOrderControlTools(runtime)

    result, _ = controls.execute(
        name="resume_work_order",
        owner_id="owner_1",
        raw_input={
            "work_order_id": "work_1",
            "decision": {"approved": True},
            "unexpected": "value",
        },
    )

    assert result.error_type == "input_validation_error"
    assert runtime.resumed == []
