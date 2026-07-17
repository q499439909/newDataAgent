from __future__ import annotations

from dataagent.application.agent_runtime import AgentRuntime


def test_agent_checkpoint_and_versions_survive_runtime_restart(tmp_path) -> None:
    runtime = AgentRuntime(tmp_path)
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

    restarted = AgentRuntime(tmp_path)
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
    final_runtime = AgentRuntime(tmp_path)
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
    assert len(
        final_runtime.version_store.list_for_owner(kind="pipeline", owner_id="user_1")
    ) == 6
    assert len(
        final_runtime.version_store.list_for_owner(kind="sampling_plan", owner_id="user_1")
    ) == 1


def test_persistent_runtime_preserves_owner_isolation(tmp_path) -> None:
    AgentRuntime(tmp_path).start(
        owner_id="owner_a",
        work_order_id="private_work_order",
        requirement="filter images",
        data_sources=[
            {"type": "local_directory", "uri": "D:/images", "mapping": {}}
        ],
    )

    restarted = AgentRuntime(tmp_path)
    try:
        restarted.state(work_order_id="private_work_order", owner_id="owner_b")
    except PermissionError:
        pass
    else:
        raise AssertionError("Owner isolation was not enforced after restart")
