from __future__ import annotations

import pytest

from dataagent.application.work_order_runtime import WorkOrderRuntime
from dataagent.domain.specs import RequirementDraft


class CompleteRequirementPlanner:
    def plan(self, request):
        return RequirementDraft(
            objective=request.requirement,
            clause_traces=(
                {
                    "source_text": request.requirement,
                    "role": "context",
                },
            ),
        )


def test_resume_rejects_a_stale_approval_boundary(tmp_path) -> None:
    runtime = WorkOrderRuntime(
        tmp_path,
        include_datajuicer=False,
        requirement_planner=CompleteRequirementPlanner(),
    )
    started = runtime.start(
        owner_id="user_1",
        work_order_id="guarded_work_order",
        requirement="Prepare the referenced records.",
        data_sources=[
            {"type": "local_directory", "uri": "D:/records", "mapping": {}}
        ],
    )
    assert started["interrupts"][0]["value"]["kind"] == "task_spec_confirmation"

    with pytest.raises(ValueError, match="Stale approval boundary"):
        runtime.resume(
            work_order_id="guarded_work_order",
            owner_id="user_1",
            decision={
                "approved": True,
                "expected_interrupt_kind": "operator_plan_confirmation",
            },
        )

    current = runtime.state(
        work_order_id="guarded_work_order",
        owner_id="user_1",
    )
    assert current["interrupts"][0]["value"]["kind"] == "task_spec_confirmation"


def test_agent_checkpoint_and_versions_survive_runtime_restart(tmp_path) -> None:
    runtime = WorkOrderRuntime(tmp_path)
    first = runtime.start(
        owner_id="user_1",
        work_order_id="persistent_work_order",
        requirement="筛选清晰图片并去重",
        data_sources=[
            {"type": "local_directory", "uri": "D:/images", "mapping": {}}
        ],
    )
    assert first["interrupts"][0]["value"]["kind"] == "task_spec_confirmation"
    thread_id = first["thread_id"]

    restarted = WorkOrderRuntime(tmp_path)
    second = restarted.resume(
        work_order_id="persistent_work_order",
        owner_id="user_1",
        decision={"approved": True},
    )
    assert second["thread_id"] == thread_id
    assert second["interrupts"][0]["value"]["kind"] == "pipeline_approval"
    assert len(second["state"]["representative_pipelines"]) == 3

    balanced = next(
        item
        for item in second["state"]["representative_pipelines"]
        if item["strategy"] == "balanced"
    )
    final_runtime = WorkOrderRuntime(tmp_path)
    final = final_runtime.resume(
        work_order_id="persistent_work_order",
        owner_id="user_1",
        decision={"approved": True, "pipeline_id": balanced["id"]},
    )
    assert final["thread_id"] == thread_id
    assert final["interrupts"] == []
    assert final["state"]["sampling_plan"]["random_seed"] == 42

    assert final_runtime.version_store is not None
    assert len(
        final_runtime.version_store.list_for_owner(kind="task_spec", owner_id="user_1")
    ) == 2
    assert len(
        final_runtime.version_store.list_for_owner(kind="retrieval_plan", owner_id="user_1")
    ) == 1
    pipelines = final_runtime.version_store.list_for_owner(
        kind="pipeline", owner_id="user_1"
    )
    assert len(pipelines) == 4
    assert sum(item["approved"] for item in pipelines) == 1
    assert len(
        final_runtime.version_store.list_for_owner(kind="sampling_plan", owner_id="user_1")
    ) == 1


def test_persistent_runtime_preserves_owner_isolation(tmp_path) -> None:
    WorkOrderRuntime(tmp_path).start(
        owner_id="owner_a",
        work_order_id="private_work_order",
        requirement="filter images",
        data_sources=[
            {"type": "local_directory", "uri": "D:/images", "mapping": {}}
        ],
    )

    restarted = WorkOrderRuntime(tmp_path)
    try:
        restarted.state(work_order_id="private_work_order", owner_id="owner_b")
    except PermissionError:
        pass
    else:
        raise AssertionError("Owner isolation was not enforced after restart")
