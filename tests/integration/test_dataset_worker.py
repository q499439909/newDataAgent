from __future__ import annotations

import hashlib
import shutil

from fastapi.testclient import TestClient
from PIL import Image

from apps.api.main import create_app
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.application.run_worker import LocalRunWorker
from dataagent.imaging import analyze_image


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    client = TestClient(create_app(AgentRuntime(home)))
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
    assert {"run_started", "run_planned", "asset_completed", "run_succeeded"} <= event_types
    assert {path.name: _sha256(path) for path in (first, duplicate)} == source_hashes


def test_queued_run_can_pause_resume_cancel_and_is_owner_isolated(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (640, 480), (120, 130, 140)).save(source / "sample.png")
    home = tmp_path / "runtime"
    client = TestClient(create_app(AgentRuntime(home)))
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
    client = TestClient(create_app(AgentRuntime(home)))
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


def test_worker_resumes_from_asset_checkpoint_without_reprocessing(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    first = source / "a.png"
    second = source / "b.png"
    Image.new("RGB", (640, 480), (120, 130, 140)).save(first)
    second.write_bytes(first.read_bytes())
    home = tmp_path / "runtime"
    client = TestClient(create_app(AgentRuntime(home)))
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
            "labels": {"quality_pass": True},
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
