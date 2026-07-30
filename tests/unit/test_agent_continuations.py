from __future__ import annotations

from threading import Event
from time import monotonic, sleep

import pytest

from dataagent.application.agent_continuations import (
    AgentContinuationManager,
)


class BlockingRuntime:
    def __init__(self) -> None:
        self.release = Event()

    def state(self, *, work_order_id: str, owner_id: str) -> dict:
        return {
            "work_order_id": work_order_id,
            "thread_id": "thread_1",
            "state": {
                "owner_id": owner_id,
                "operator_plan_confirmed": False,
            },
            "interrupts": [
                {
                    "value": {
                        "kind": "operator_plan_confirmation",
                    }
                }
            ],
        }

    def resume(
        self,
        *,
        work_order_id: str,
        owner_id: str,
        decision: dict,
    ) -> dict:
        assert decision == {"approved": True}
        self.release.wait(timeout=2)
        return {
            "work_order_id": work_order_id,
            "thread_id": "thread_1",
            "state": {
                "owner_id": owner_id,
                "operator_plan_confirmed": True,
                "representative_pipelines": [{"id": "pipeline_1"}],
            },
            "interrupts": [
                {"value": {"kind": "pipeline_approval"}}
            ],
        }


def test_slow_continuation_leaves_the_http_boundary_immediately() -> None:
    runtime = BlockingRuntime()
    manager = AgentContinuationManager(runtime, max_workers=1)

    started = monotonic()
    submitted = manager.submit(
        work_order_id="work_1",
        owner_id="user_1",
        decision={"approved": True},
    )

    assert monotonic() - started < 0.2
    assert submitted["status"] in {"queued", "running"}
    assert submitted["turn"]["interrupts"] == []
    assert submitted["turn"]["state"]["next_action"] == (
        "run_processing_agent"
    )

    runtime.release.set()
    deadline = monotonic() + 2
    completed = submitted
    while completed["status"] != "completed" and monotonic() < deadline:
        sleep(0.01)
        completed = manager.get(
            turn_id=submitted["turn_id"],
            owner_id="user_1",
        )

    assert completed["status"] == "completed"
    assert completed["turn"]["interrupts"][0]["value"]["kind"] == (
        "pipeline_approval"
    )


def test_task_spec_approval_overlay_reports_retrieval() -> None:
    turn = AgentContinuationManager._running_overlay(
        {
            "work_order_id": "work_1",
            "thread_id": "thread_1",
            "state": {},
            "interrupts": [
                {"value": {"kind": "task_spec_confirmation"}}
            ],
        },
        status="queued",
    )

    assert turn["state"]["current_agent"] == "retrieval"
    assert turn["state"]["next_action"] == "run_retrieval_agent"
    assert turn["interrupts"] == []

    running = AgentContinuationManager._running_overlay(
        turn,
        status="running",
    )
    assert running["state"]["current_agent"] == "retrieval"
    assert running["state"]["next_action"] == "run_retrieval_agent"


def test_async_submit_rejects_a_stale_boundary_before_queueing() -> None:
    runtime = BlockingRuntime()
    manager = AgentContinuationManager(runtime, max_workers=1)

    with pytest.raises(ValueError, match="Stale approval boundary"):
        manager.submit(
            work_order_id="work_1",
            owner_id="user_1",
            decision={
                "approved": True,
                "expected_interrupt_kind": "task_spec_confirmation",
            },
        )
