from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from apps.api.main import create_app
from dataagent.agents.runner import AgentDecision
from dataagent.application.work_order_runtime import WorkOrderRuntime
from dataagent.application.dataset_versions import (
    build_logical_dataset_version,
    merge_repaired_dataset_version,
    write_dataset_manifest,
)
from dataagent.application.run_worker import LocalRunWorker
from dataagent.domain.runs import AssetOrigin, DatasetAsset
from dataagent.domain.operators import AssetRef
from dataagent.domain.specs import DataSourceSpec, TaskSpecVersion
from dataagent.imaging import analyze_image
from dataagent.execution.dataset_runner import (
    _is_provider_execution_evidence,
    _redact_parameters,
)
from dataagent.execution.dataset_runner import DatasetRunExecutor


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_run_audit_parameter_redaction_keeps_prompts_but_hides_credentials() -> None:
    assert _redact_parameters(
        {
            "system_prompt": "return JSON",
            "model_params": {"api_key": "secret-value", "base_url": "https://example.test"},
        }
    ) == {
        "system_prompt": "return JSON",
        "model_params": {"api_key": "***", "base_url": "https://example.test"},
    }


def test_provider_process_files_are_audit_evidence_not_delivery_artifacts() -> None:
    assert _is_provider_execution_evidence(
        AssetRef(
            uri="D:/runtime/output.jsonl",
            media_type="application/x-ndjson",
            sha256="a" * 64,
        )
    )
    assert not _is_provider_execution_evidence(
        AssetRef(
            uri="D:/runtime/labels.jsonl",
            media_type="application/x-ndjson",
            sha256="b" * 64,
        )
    )


def test_only_retryable_execution_failures_are_partial_completions() -> None:
    class Dataset:
        failed_count = 1

    class ExecutionFailureReport:
        reason_codes = ("EXECUTION_FAILURES_PRESENT",)

    class MixedFailureReport:
        reason_codes = (
            "EXECUTION_FAILURES_PRESENT",
            "REQUIRED_SEMANTIC_OUTPUT_MISSING",
        )

    assert DatasetRunExecutor._is_partial_completion(Dataset(), ExecutionFailureReport())
    assert not DatasetRunExecutor._is_partial_completion(Dataset(), MixedFailureReport())


def test_worker_rejects_repair_plan_outside_frozen_scope() -> None:
    run = {
        "operation_kind": "repair",
        "repair_scope": [
            {"source_uri": "D:/data/failed.png", "source_sha256": "failed-hash"}
        ],
    }
    escaped_plan = [
        {
            "source_uri": "D:/data/failed.png",
            "source_sha256": "failed-hash",
        },
        {
            "source_uri": "D:/data/already-kept.png",
            "source_sha256": "kept-hash",
        },
    ]

    with pytest.raises(RuntimeError, match="escaped"):
        DatasetRunExecutor._validate_repair_scope(run, escaped_plan)


def test_all_failed_repair_can_publish_logical_result_for_another_attempt() -> None:
    failed = [{"decision": "failed"}]

    DatasetRunExecutor._validate_publication_outcomes(
        {"operation_kind": "repair"},
        failed,
    )
    with pytest.raises(RuntimeError, match="empty dataset"):
        DatasetRunExecutor._validate_publication_outcomes(
            {"operation_kind": "production"},
            failed,
        )


def _ready_work_order(client: TestClient, source, work_order_id: str = "run_work_order"):
    headers = {"X-Owner-ID": "user_1"}
    client.post(
        "/api/work-orders/agent/start",
        headers=headers,
        json={
            "work_order_id": work_order_id,
            "requirement": "筛选清晰图片并去重",
            "data_sources": [
                {"type": "local_directory", "uri": str(source), "mapping": {}}
            ],
        },
    )
    candidates = client.post(
        f"/api/work-orders/{work_order_id}/agent/resume",
        headers=headers,
        json={"decision": {"approved": True}},
    ).json()
    retained = next(
        item
        for item in candidates["state"]["representative_pipelines"]
        if item["strategy"] == "retention_first"
    )
    return client.post(
        f"/api/work-orders/{work_order_id}/agent/resume",
        headers=headers,
        json={"decision": {"approved": True, "pipeline_id": retained["id"]}},
    ).json()


def test_worker_publishes_immutable_dataset_and_preserves_sources(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    first = source / "first.png"
    duplicate = source / "duplicate.png"
    Image.new("RGB", (640, 480), (120, 130, 140)).save(first)
    duplicate.write_bytes(first.read_bytes())
    source_hashes = {path.name: _sha256(path) for path in (first, duplicate)}

    home = tmp_path / "runtime"
    runtime = WorkOrderRuntime(home)
    client = TestClient(create_app(runtime))
    final = _ready_work_order(client, source)
    approved = final["state"]["approved_pipeline"]
    assert approved["approved"] is True

    headers = {"X-Owner-ID": "user_1", "Idempotency-Key": "dataset-run-1"}
    submitted = client.post(
        "/api/work-orders/run_work_order/runs", headers=headers
    )
    assert submitted.status_code == 202
    assert submitted.json()["status"] == "QUEUED"
    repeated = client.post(
        "/api/work-orders/run_work_order/runs", headers=headers
    )
    assert repeated.json()["id"] == submitted.json()["id"]

    completed = LocalRunWorker(home).process_next()
    assert completed is not None
    assert completed["status"] == "SUCCEEDED"
    assert completed["progress"] == 2
    assert completed["kept"] == 1
    assert completed["rejected"] == 1

    run = client.get(
        f"/api/runs/{completed['id']}", headers={"X-Owner-ID": "user_1"}
    ).json()
    assert run["qc_report_id"]
    dataset = client.get(
        f"/api/datasets/{run['dataset_version_id']}",
        headers={"X-Owner-ID": "user_1"},
    ).json()
    assert dataset["pipeline_version_id"] == approved["id"]
    assert dataset["kept_count"] == 1
    assert dataset["rejected_count"] == 1
    assert dataset["original_files_unchanged"] is True
    assert dataset["version_kind"] == "logical"
    assert dataset["deliverable"] is False
    assert dataset["still_failed"] == []
    assert all(asset["origin_run_id"] == completed["id"] for asset in dataset["assets"])
    assert {
        asset["materialization"] for asset in dataset["assets"]
    } == {"local_file", "not_materialized"}
    manifest = Path(dataset["manifest_uri"])
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["id"] == dataset["id"]
    assert manifest_payload["assets"][0]["source_uri"] == dataset["assets"][0]["source_uri"]
    assert "artifacts" not in manifest_payload["assets"][0]
    assert "annotations" not in manifest_payload["assets"][0]
    report = client.get(
        f"/api/qc-reports/{run['qc_report_id']}",
        headers={"X-Owner-ID": "user_1"},
    ).json()
    assert report["dataset_version_id"] == dataset["id"]
    assert report["status"] == "PASSED"
    assert report["full_hard_rule_check"] is True
    assert report["semantic_quality_verified"] is False
    events = client.get(
        f"/api/runs/{completed['id']}/events",
        headers={"X-Owner-ID": "user_1"},
    ).json()
    event_types = {item["event_type"] for item in events}
    assert {
        "run_started",
        "run_planned",
        "asset_node_started",
        "asset_node_completed",
        "asset_completed",
        "run_succeeded",
    } <= event_types
    node_started = next(item for item in events if item["event_type"] == "asset_node_started")
    assert "parameters" in node_started["details"]
    node_results = client.get(
        f"/api/runs/{completed['id']}/node-results",
        headers={"X-Owner-ID": "user_1"},
    ).json()
    node_count = len(approved["nodes"])
    assert len(node_results) == 2 * node_count
    first_results = [item for item in node_results if item["asset_sequence"] == 0]
    duplicate_results = [item for item in node_results if item["asset_sequence"] == 1]
    assert [item["node_id"] for item in first_results] == [
        item["id"] for item in approved["nodes"]
    ]
    assert all(item["status"] == "completed" for item in first_results)
    rejected = next(item for item in duplicate_results if item["decision"] == "reject")
    assert rejected["reason_codes"]
    assert all(
        item["status"] == "skipped"
        for item in duplicate_results[duplicate_results.index(rejected) + 1 :]
    )
    assert {path.name: _sha256(path) for path in (first, duplicate)} == source_hashes
    exported = runtime.export_deliverable_dataset(
        dataset_version_id=dataset["id"],
        owner_id="user_1",
        destination=tmp_path / "deliverable",
    )
    assert exported["file_count"] == 1
    assert Path(exported["manifest_uri"]).is_file()
    assert len(list((tmp_path / "deliverable" / "files").rglob("*.png"))) == 1
    assert runtime.run_store is not None
    assert runtime.run_store.events(completed["id"], "user_1")[-1][
        "event_type"
    ] == "dataset_exported"


def test_queued_run_can_pause_resume_cancel_and_is_owner_isolated(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (640, 480), (120, 130, 140)).save(source / "sample.png")
    home = tmp_path / "runtime"
    client = TestClient(create_app(WorkOrderRuntime(home)))
    _ready_work_order(client, source, work_order_id="controlled_work_order")
    headers = {"X-Owner-ID": "user_1", "Idempotency-Key": "controlled-run"}
    run = client.post(
        "/api/work-orders/controlled_work_order/runs", headers=headers
    ).json()

    forbidden = client.get(
        f"/api/runs/{run['id']}", headers={"X-Owner-ID": "user_2"}
    )
    assert forbidden.status_code == 403
    paused = client.post(
        f"/api/runs/{run['id']}/control",
        headers={"X-Owner-ID": "user_1"},
        json={"action": "pause"},
    ).json()
    assert paused["status"] == "PAUSED"
    resumed = client.post(
        f"/api/runs/{run['id']}/control",
        headers={"X-Owner-ID": "user_1"},
        json={"action": "resume"},
    ).json()
    assert resumed["status"] == "QUEUED"
    cancelled = client.post(
        f"/api/runs/{run['id']}/control",
        headers={"X-Owner-ID": "user_1"},
        json={"action": "cancel"},
    ).json()
    assert cancelled["status"] == "CANCELLED"
    assert LocalRunWorker(home).process_next() is None


def test_worker_marks_run_failed_when_dataset_qc_fails(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (640, 480), (120, 130, 140)).save(source / "sample.png")
    home = tmp_path / "runtime"
    client = TestClient(create_app(WorkOrderRuntime(home)))
    _ready_work_order(client, source, work_order_id="qc_failed_work_order")
    submitted = client.post(
        "/api/work-orders/qc_failed_work_order/runs",
        headers={"X-Owner-ID": "user_1", "Idempotency-Key": "qc-failed-run"},
    ).json()

    class FailedReport:
        id = "qc_report_failed"
        status = "FAILED"
        reason_codes = ("REQUIRED_SEMANTIC_OUTPUT_MISSING",)

    class FailedEvaluator:
        def evaluate(self, **kwargs):
            return FailedReport()

    worker = LocalRunWorker(home)
    worker.executor.quality_evaluator = FailedEvaluator()
    completed = worker.process_next()

    assert completed is not None
    assert completed["id"] == submitted["id"]
    assert completed["status"] == "FAILED"
    assert completed["dataset_version_id"]
    assert "REQUIRED_SEMANTIC_OUTPUT_MISSING" in completed["error"]


def test_terminal_run_outcome_becomes_an_idempotent_agent_observation(
    tmp_path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (640, 480), (120, 130, 140)).save(
        source / "sample.png"
    )
    home = tmp_path / "runtime"
    runtime = WorkOrderRuntime(home)
    client = TestClient(create_app(runtime))
    _ready_work_order(
        client,
        source,
        work_order_id="observed_run_work_order",
    )
    submitted = client.post(
        "/api/work-orders/observed_run_work_order/runs",
        headers={
            "X-Owner-ID": "user_1",
            "Idempotency-Key": "observed-run",
        },
    ).json()

    assert runtime.run_store is not None
    runtime.run_store.mark_failed(
        submitted["id"],
        "audio loudness evidence was not produced",
    )

    first = runtime.observe_run_outcome(
        work_order_id="observed_run_work_order",
        run_id=submitted["id"],
        owner_id="user_1",
    )
    repeated = runtime.observe_run_outcome(
        work_order_id="observed_run_work_order",
        run_id=submitted["id"],
        owner_id="user_1",
    )

    observation = first["state"]["latest_run_observation"]
    assert observation["run_id"] == submitted["id"]
    assert observation["run_status"] == "FAILED"
    assert observation["error"] == (
        "audio loudness evidence was not produced"
    )
    assert observation["qc_status"] is None
    assert first["state"]["observed_run_ids"] == [submitted["id"]]
    assert repeated["state"]["agent_observations"] == first["state"][
        "agent_observations"
    ]
    assert repeated["state"]["observed_run_ids"] == [submitted["id"]]


def test_worker_notifies_agent_runtime_after_terminal_run(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (640, 480), (120, 130, 140)).save(
        source / "sample.png"
    )
    home = tmp_path / "runtime"
    runtime = WorkOrderRuntime(home)
    client = TestClient(create_app(runtime))
    _ready_work_order(
        client,
        source,
        work_order_id="background_observation_work_order",
    )
    submitted = client.post(
        "/api/work-orders/background_observation_work_order/runs",
        headers={
            "X-Owner-ID": "user_1",
            "Idempotency-Key": "background-observation-run",
        },
    ).json()
    outcome_runtime = WorkOrderRuntime(home)
    worker = LocalRunWorker(
        home,
        run_outcome_handler=lambda run: outcome_runtime.observe_run_outcome(
            work_order_id=run["work_order_id"],
            run_id=run["id"],
            owner_id=run["owner_id"],
        ),
    )

    completed = worker.process_next()
    state = runtime.state(
        work_order_id="background_observation_work_order",
        owner_id="user_1",
    )["state"]

    assert completed is not None
    assert completed["id"] == submitted["id"]
    assert state["latest_run_observation"]["run_id"] == submitted["id"]
    assert state["observed_run_ids"] == [submitted["id"]]
    events = runtime.get_run_events(
        run_id=submitted["id"],
        owner_id="user_1",
    )
    assert events[-1]["event_type"] == "run_outcome_notified"


def test_active_run_cannot_be_reported_as_a_terminal_observation(
    tmp_path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (640, 480), (120, 130, 140)).save(
        source / "sample.png"
    )
    home = tmp_path / "runtime"
    runtime = WorkOrderRuntime(home)
    client = TestClient(create_app(runtime))
    _ready_work_order(
        client,
        source,
        work_order_id="active_run_work_order",
    )
    submitted = client.post(
        "/api/work-orders/active_run_work_order/runs",
        headers={
            "X-Owner-ID": "user_1",
            "Idempotency-Key": "active-run",
        },
    ).json()

    with pytest.raises(ValueError, match="not terminal"):
        runtime.observe_run_outcome(
            work_order_id="active_run_work_order",
            run_id=submitted["id"],
            owner_id="user_1",
        )

    state = runtime.state(
        work_order_id="active_run_work_order",
        owner_id="user_1",
    )["state"]
    assert state.get("observed_run_ids", []) == []


def test_recorded_outcome_can_be_resumed_when_agent_decision_recovers(
    tmp_path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (640, 480), (120, 130, 140)).save(
        source / "sample.png"
    )
    home = tmp_path / "runtime"
    runtime = WorkOrderRuntime(home)
    client = TestClient(create_app(runtime))
    _ready_work_order(
        client,
        source,
        work_order_id="recoverable_observation_work_order",
    )
    submitted = client.post(
        "/api/work-orders/recoverable_observation_work_order/runs",
        headers={
            "X-Owner-ID": "user_1",
            "Idempotency-Key": "recoverable-observation-run",
        },
    ).json()
    assert runtime.run_store is not None
    runtime.run_store.mark_failed(submitted["id"], "temporary model outage")
    recorded = runtime.observe_run_outcome(
        work_order_id="recoverable_observation_work_order",
        run_id=submitted["id"],
        owner_id="user_1",
    )
    assert recorded["state"]["next_action"] == "resolve_run_outcome"

    class AskUserPlanner:
        def decide(self, request):
            return AgentDecision(
                action="tool",
                reason_summary="The recovered Agent needs a human decision.",
                tool_name="ask_user",
            )

    recovered_runtime = WorkOrderRuntime(home, agent_planner=AskUserPlanner())
    recovered = recovered_runtime.observe_run_outcome(
        work_order_id="recoverable_observation_work_order",
        run_id=submitted["id"],
        owner_id="user_1",
    )

    assert recovered["interrupts"][0]["value"]["kind"] == (
        "run_outcome_resolution"
    )


def test_worker_retries_a_failed_outcome_notification_from_durable_state(
    tmp_path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (640, 480), (120, 130, 140)).save(
        source / "sample.png"
    )
    home = tmp_path / "runtime"
    runtime = WorkOrderRuntime(home)
    client = TestClient(create_app(runtime))
    _ready_work_order(
        client,
        source,
        work_order_id="durable_notification_work_order",
    )
    submitted = client.post(
        "/api/work-orders/durable_notification_work_order/runs",
        headers={
            "X-Owner-ID": "user_1",
            "Idempotency-Key": "durable-notification-run",
        },
    ).json()
    attempts = 0

    def flaky_handler(run):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary Agent model outage")
        return runtime.observe_run_outcome(
            work_order_id=run["work_order_id"],
            run_id=run["id"],
            owner_id=run["owner_id"],
        )

    worker = LocalRunWorker(home, run_outcome_handler=flaky_handler)

    completed = worker.process_next()
    retried = worker.process_next()

    assert completed is not None
    assert completed["id"] == submitted["id"]
    assert retried is not None
    assert retried["id"] == submitted["id"]
    assert attempts == 2
    events = runtime.get_run_events(
        run_id=submitted["id"],
        owner_id="user_1",
    )
    assert [item["event_type"] for item in events[-2:]] == [
        "run_outcome_notification_failed",
        "run_outcome_notified",
    ]


def test_worker_does_not_awaken_historical_terminal_run_without_request(
    tmp_path,
) -> None:
    home = tmp_path / "runtime"
    runtime = WorkOrderRuntime(home)
    assert runtime.run_store is not None
    historical = runtime.run_store.create(
        run_id="historical_run",
        work_order_id="historical_work_order",
        owner_id="user_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="task_spec_1",
        idempotency_key="historical-run",
    )
    runtime.run_store.mark_failed(
        historical["id"],
        "failure recorded before Agent outcome handoff existed",
    )
    awakened: list[str] = []
    worker = LocalRunWorker(
        home,
        run_outcome_handler=lambda run: awakened.append(run["id"]),
    )

    assert worker.process_next() is None
    assert awakened == []


def test_worker_resumes_from_asset_checkpoint_without_reprocessing(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    first = source / "a.png"
    second = source / "b.png"
    Image.new("RGB", (640, 480), (120, 130, 140)).save(first)
    second.write_bytes(first.read_bytes())
    home = tmp_path / "runtime"
    client = TestClient(create_app(WorkOrderRuntime(home)))
    _ready_work_order(client, source, work_order_id="resumable_work_order")
    run = client.post(
        "/api/work-orders/resumable_work_order/runs",
        headers={"X-Owner-ID": "user_1", "Idempotency-Key": "resumable-run"},
    ).json()

    worker = LocalRunWorker(home)
    claimed = worker.run_store.claim_next()
    assert claimed is not None
    worker.run_store.set_total(run["id"], 2)
    staged = home / "runs" / run["id"] / "files" / "a.png"
    staged.parent.mkdir(parents=True)
    shutil.copy2(first, staged)
    metrics = analyze_image(first).model_dump(mode="json")
    worker.run_store.add_item(
        run["id"],
        {
            "sequence": 0,
            "source_uri": str(first.resolve()),
            "source_sha256": _sha256(first),
            "output_relative_path": "a.png",
            "output_sha256": _sha256(staged),
            "decision": "keep",
            "reason_codes": [],
            "metrics": metrics,
            "labels": {
                "quality_pass": True,
                "duplicate": False,
                "duplicate_group_id": "checkpoint_group_a",
                "canonical_asset_path": str(first.resolve()),
            },
        },
    )
    worker.run_store.initialize_plan(
        run["id"],
        [
            {
                "sequence": 0,
                "source_uri": str(first.resolve()),
                "source_sha256": _sha256(first),
                "output_relative_path": "a.png",
            },
            {
                "sequence": 1,
                "source_uri": str(second.resolve()),
                "source_sha256": _sha256(second),
                "output_relative_path": "b.png",
            },
        ],
    )
    worker.run_store.request_pause(run["id"], "user_1")
    worker.run_store.mark_paused(run["id"])
    worker.run_store.resume(run["id"], "user_1")
    Image.new("RGB", (640, 480), "white").save(source / "c.png")

    completed = LocalRunWorker(home).process_next()
    assert completed is not None
    assert completed["status"] == "SUCCEEDED"
    assert completed["progress"] == 2
    assert completed["total"] == 2
    assert completed["kept"] == 1
    assert completed["rejected"] == 1


def test_retry_failed_assets_creates_a_new_run_with_only_failed_sources(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    first = source / "ok.png"
    second = source / "timeout.png"
    Image.new("RGB", (64, 64), "white").save(first)
    Image.new("RGB", (64, 64), "black").save(second)
    home = tmp_path / "runtime"
    runtime = WorkOrderRuntime(home)
    client = TestClient(create_app(runtime))
    _ready_work_order(client, source, work_order_id="retry_work_order")
    previous = client.post(
        "/api/work-orders/retry_work_order/runs",
        headers={"X-Owner-ID": "user_1", "Idempotency-Key": "first-attempt"},
    ).json()
    assert runtime.run_store is not None
    plan = [
        {
            "sequence": index,
            "source_uri": str(path.resolve()),
            "source_sha256": _sha256(path),
            "output_relative_path": path.name,
        }
        for index, path in enumerate((first, second))
    ]
    runtime.run_store.initialize_plan(previous["id"], plan)
    for item, decision in zip(plan, ("keep", "failed"), strict=True):
        runtime.run_store.add_item(
            previous["id"],
            {
                **item,
                "output_relative_path": None,
                "output_sha256": None,
                "decision": decision,
                "reason_codes": ["OPERATOR_ERROR:TimeoutError"] if decision == "failed" else [],
                "metrics": {},
                "labels": {},
            },
        )

    candidates = client.get(
        f"/api/runs/{previous['id']}/repair-candidates",
        headers={"X-Owner-ID": "user_1"},
    )
    assert candidates.status_code == 200
    assert candidates.json()["retry_allowed"] is True
    assert len(candidates.json()["still_failed"]) == 1
    response = client.post(
        f"/api/runs/{previous['id']}/repairs",
        headers={
            "X-Owner-ID": "user_1",
            "Idempotency-Key": "retry-failed-only",
        },
    )
    assert response.status_code == 202
    retry = response.json()

    retry_plan = runtime.run_store.plan(retry["id"])
    assert retry["id"] != previous["id"]
    assert retry["operation_kind"] == "repair"
    assert retry["parent_run_id"] == previous["id"]
    assert retry["parent_dataset_version_id"] is None
    assert retry["repair_attempt"] == 1
    assert [item["source_uri"] for item in retry_plan] == [str(second.resolve())]
    assert retry_plan[0]["sequence"] == 0
    assert retry["repair_scope"] == [
        {
            "source_uri": str(second.resolve()),
            "source_sha256": _sha256(second),
            "parent_sequence": 1,
            "reason_codes": ["OPERATOR_ERROR:TimeoutError"],
        }
    ]
    event = runtime.run_store.events(retry["id"], "user_1")[0]
    assert event["event_type"] == "failed_assets_retry_scheduled"
    assert event["details"]["previous_run_id"] == previous["id"]


def test_repair_attempt_increments_and_scope_never_includes_parent_success(
    tmp_path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    kept = source / "kept.png"
    failed = source / "failed.png"
    Image.new("RGB", (64, 64), "white").save(kept)
    Image.new("RGB", (64, 64), "black").save(failed)
    runtime = WorkOrderRuntime(tmp_path / "runtime")
    assert runtime.run_store is not None
    original = runtime.run_store.create(
        run_id="run_original",
        work_order_id="work_order_1",
        owner_id="user_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="task_spec_1",
        idempotency_key="original",
    )
    plan = [
        {
            "sequence": index,
            "source_uri": str(path.resolve()),
            "source_sha256": _sha256(path),
            "output_relative_path": path.name,
        }
        for index, path in enumerate((kept, failed))
    ]
    runtime.run_store.initialize_plan(original["id"], plan)
    for item, decision in zip(plan, ("keep", "failed"), strict=True):
        runtime.run_store.add_item(
            original["id"],
            {
                **item,
                "output_relative_path": None,
                "output_sha256": None,
                "decision": decision,
                "reason_codes": ["OPERATOR_ERROR:TimeoutError"] if decision == "failed" else [],
                "metrics": {},
                "labels": {},
            },
        )

    first_repair = runtime.retry_failed_assets(
        previous_run_id=original["id"],
        owner_id="user_1",
        idempotency_key="repair-1",
    )
    first_plan = runtime.run_store.plan(first_repair["id"])
    runtime.run_store.add_item(
        first_repair["id"],
        {
            **first_plan[0],
            "output_relative_path": None,
            "output_sha256": None,
            "decision": "failed",
            "reason_codes": ["OPERATOR_ERROR:TimeoutError"],
            "metrics": {},
            "labels": {},
        },
    )

    second_repair = runtime.retry_failed_assets(
        previous_run_id=first_repair["id"],
        owner_id="user_1",
        idempotency_key="repair-2",
    )

    assert second_repair["repair_attempt"] == 2
    assert second_repair["parent_run_id"] == first_repair["id"]
    assert [item["source_uri"] for item in second_repair["repair_scope"]] == [
        str(failed.resolve())
    ]
    assert str(kept.resolve()) not in {
        item["source_uri"] for item in second_repair["repair_scope"]
    }
    second_plan = runtime.run_store.plan(second_repair["id"])
    runtime.run_store.add_item(
        second_repair["id"],
        {
            **second_plan[0],
            "output_relative_path": None,
            "output_sha256": None,
            "decision": "failed",
            "reason_codes": ["OPERATOR_ERROR:TimeoutError"],
            "metrics": {},
            "labels": {},
        },
    )
    third_repair = runtime.retry_failed_assets(
        previous_run_id=second_repair["id"],
        owner_id="user_1",
        idempotency_key="repair-3",
    )
    assert third_repair["repair_attempt"] == 3
    third_plan = runtime.run_store.plan(third_repair["id"])
    runtime.run_store.add_item(
        third_repair["id"],
        {
            **third_plan[0],
            "output_relative_path": None,
            "output_sha256": None,
            "decision": "failed",
            "reason_codes": ["OPERATOR_ERROR:TimeoutError"],
            "metrics": {},
            "labels": {},
        },
    )
    with pytest.raises(ValueError, match="maximum repair attempts"):
        runtime.retry_failed_assets(
            previous_run_id=third_repair["id"],
            owner_id="user_1",
            idempotency_key="repair-4",
        )


def test_executor_persists_repaired_dataset_before_quality_evaluation(
    tmp_path,
) -> None:
    home = tmp_path / "runtime"
    runtime = WorkOrderRuntime(home)
    assert runtime.version_store is not None
    assert runtime.run_store is not None
    source_root = tmp_path / "source"
    source_root.mkdir()
    kept_source = source_root / "kept.png"
    failed_source = source_root / "failed.png"
    kept_source.write_bytes(b"kept-source")
    failed_source.write_bytes(b"failed-source")
    parent_output = home / "datasets" / "dataset_parent" / "files" / "kept.png"
    parent_output.parent.mkdir(parents=True)
    parent_output.write_bytes(b"kept-output")
    repair_output_path = (
        home / "datasets" / "dataset_repair_output" / "files" / "failed.png"
    )
    repair_output_path.parent.mkdir(parents=True)
    repair_output_path.write_bytes(b"repair-output")
    parent = build_logical_dataset_version(
        dataset_id="dataset_parent",
        owner_id="user_1",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="task_spec_1",
        run_id="run_parent",
        source_roots=(str(source_root),),
        manifest_uri=str(home / "datasets" / "dataset_parent" / "manifest.json"),
        assets=(
            DatasetAsset(
                source_uri=str(kept_source),
                source_sha256=_sha256(kept_source),
                output_uri=str(parent_output),
                output_sha256=_sha256(parent_output),
                decision="keep",
            ),
            DatasetAsset(
                source_uri=str(failed_source),
                source_sha256=_sha256(failed_source),
                decision="failed",
                reason_codes=("OPERATOR_ERROR:TimeoutError",),
            ),
        ),
    )
    repair_output = build_logical_dataset_version(
        dataset_id="dataset_repair_output",
        owner_id="user_1",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="task_spec_1",
        run_id="run_repair",
        source_roots=(str(source_root),),
        manifest_uri=str(
            home / "datasets" / "dataset_repair_output" / "manifest.json"
        ),
        assets=(
            DatasetAsset(
                source_uri=str(failed_source),
                source_sha256=_sha256(failed_source),
                output_uri=str(repair_output_path),
                output_sha256=_sha256(repair_output_path),
                decision="keep",
            ),
        ),
    )
    for dataset in (parent, repair_output):
        write_dataset_manifest(dataset)
        runtime.version_store.save_if_absent(
            kind="dataset",
            owner_id="user_1",
            payload=dataset.model_dump(mode="json"),
        )
    repair_run = runtime.run_store.create(
        run_id="run_repair",
        work_order_id="work_order_1",
        owner_id="user_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="task_spec_1",
        idempotency_key="repair",
        operation_kind="repair",
        parent_run_id="run_parent",
        parent_dataset_version_id=parent.id,
        repair_scope=(
            {
                "source_uri": str(failed_source),
                "source_sha256": _sha256(failed_source),
                "parent_sequence": 1,
                "reason_codes": ["OPERATOR_ERROR:TimeoutError"],
            },
        ),
        repair_attempt=1,
    )

    repaired = LocalRunWorker(home).executor._merge_repair_output(
        repair_run,
        repair_output,
    )

    assert repaired.id == "dataset_repaired_repair"
    assert repaired.parent_dataset_version_id == parent.id
    assert repaired.failed_count == 0
    assert repaired.assets[0].asset_origin == AssetOrigin.PARENT_DATASET_VERSION
    assert repaired.assets[1].asset_origin == AssetOrigin.REPAIR_RUN
    assert Path(repaired.manifest_uri).is_file()
    stored = runtime.version_store.get(
        kind="dataset",
        entity_id=repaired.id,
        owner_id="user_1",
    )
    assert stored["repair_run_ids"] == ["run_repair"]
    assert runtime.run_store.events("run_repair", "user_1")[-1][
        "event_type"
    ] == "repaired_dataset_version_created"


def test_confirmed_exclusion_promotes_abandoned_dataset_and_allows_export(
    tmp_path,
) -> None:
    home = tmp_path / "runtime"
    runtime = WorkOrderRuntime(home)
    assert runtime.version_store is not None
    assert runtime.run_store is not None
    source_root = tmp_path / "source"
    source_root.mkdir()
    kept_source = source_root / "kept.png"
    failed_source = source_root / "failed.png"
    kept_output = home / "datasets" / "dataset_parent" / "files" / "kept.png"
    kept_output.parent.mkdir(parents=True)
    kept_source.write_bytes(b"kept-source")
    failed_source.write_bytes(b"failed-source")
    kept_output.write_bytes(b"kept-output")
    task_spec = TaskSpecVersion(
        id="task_spec_1",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="produce a deliverable dataset",
        data_sources=(
            DataSourceSpec(type="local_directory", uri=str(source_root)),
        ),
        confirmed=True,
    )
    runtime.version_store.save_if_absent(
        kind="task_spec",
        owner_id="user_1",
        payload=task_spec.model_dump(mode="json"),
    )
    parent = build_logical_dataset_version(
        dataset_id="dataset_parent",
        owner_id="user_1",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=task_spec.id,
        run_id="run_parent",
        source_roots=(str(source_root),),
        manifest_uri=str(home / "datasets" / "dataset_parent" / "manifest.json"),
        assets=(
            DatasetAsset(
                source_uri=str(kept_source),
                source_sha256=_sha256(kept_source),
                output_uri=str(kept_output),
                output_sha256=_sha256(kept_output),
                decision="keep",
            ),
            DatasetAsset(
                source_uri=str(failed_source),
                source_sha256=_sha256(failed_source),
                decision="failed",
                reason_codes=("OPERATOR_ERROR:TimeoutError",),
            ),
        ),
    )
    repair_output = build_logical_dataset_version(
        dataset_id="dataset_repair_output",
        owner_id="user_1",
        work_order_id="work_order_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=task_spec.id,
        run_id="run_repair_3",
        source_roots=(str(source_root),),
        manifest_uri=str(
            home / "datasets" / "dataset_repair_output" / "manifest.json"
        ),
        assets=(
            DatasetAsset(
                source_uri=str(failed_source),
                source_sha256=_sha256(failed_source),
                decision="failed",
                reason_codes=("OPERATOR_ERROR:TimeoutError",),
            ),
        ),
    )
    abandoned = merge_repaired_dataset_version(
        dataset_id="dataset_abandoned",
        owner_id="user_1",
        parent=parent,
        repair_output=repair_output,
        repair_run_id="run_repair_3",
        repair_attempt=3,
        manifest_uri=str(
            home / "datasets" / "dataset_abandoned" / "manifest.json"
        ),
    )
    write_dataset_manifest(abandoned)
    runtime.version_store.save_if_absent(
        kind="dataset",
        owner_id="user_1",
        payload=abandoned.model_dump(mode="json"),
    )
    runtime.run_store.create(
        run_id="run_repair_3",
        work_order_id="work_order_1",
        owner_id="user_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id=task_spec.id,
        idempotency_key="repair-3",
        operation_kind="repair",
        parent_run_id="run_repair_2",
        parent_dataset_version_id=parent.id,
        repair_scope=(
            {
                "source_uri": str(failed_source),
                "source_sha256": _sha256(failed_source),
                "parent_sequence": 1,
                "reason_codes": ["OPERATOR_ERROR:TimeoutError"],
            },
        ),
        repair_attempt=3,
    )
    runtime.run_store.mark_partial(
        "run_repair_3",
        abandoned.id,
        "Dataset QC failed: EXECUTION_FAILURES_PRESENT",
    )

    client = TestClient(create_app(runtime))
    candidates = client.get(
        f"/api/datasets/{abandoned.id}/repair-candidates",
        headers={"X-Owner-ID": "user_1"},
    )
    assert candidates.status_code == 200
    assert candidates.json()["retry_allowed"] is False
    assert candidates.json()["next_actions"] == ["exclude_abandoned_assets"]
    unconfirmed = client.post(
        f"/api/datasets/{abandoned.id}/exclude-abandoned",
        headers={"X-Owner-ID": "user_1"},
        json={"confirmed": False},
    )
    assert unconfirmed.status_code == 422
    response = client.post(
        f"/api/datasets/{abandoned.id}/exclude-abandoned",
        headers={"X-Owner-ID": "user_1"},
        json={"confirmed": True},
    )
    assert response.status_code == 200
    resolved = response.json()

    assert resolved["abandoned_assets"] == []
    assert len(resolved["excluded_assets"]) == 1
    run = runtime.run_store.get("run_repair_3", "user_1")
    assert run["status"] == "SUCCEEDED"
    assert run["dataset_version_id"] == resolved["id"]
    assert run["error"] is None
    export_response = client.post(
        f"/api/datasets/{resolved['id']}/exports",
        headers={"X-Owner-ID": "user_1"},
        json={"destination": str(tmp_path / "deliverable")},
    )
    assert export_response.status_code == 200
    exported = export_response.json()
    assert exported["file_count"] == 1
    excluded_report = json.loads(
        Path(exported["excluded_assets_uri"]).read_text(encoding="utf-8")
    )
    assert len(excluded_report["excluded_assets"]) == 1
