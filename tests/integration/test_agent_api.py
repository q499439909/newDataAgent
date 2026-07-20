from __future__ import annotations

import inspect

from fastapi.testclient import TestClient
from PIL import Image

from apps.api.main import create_app
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.operators.protocol import OperatorResult
from dataagent.operators.providers import (
    DataJuicerOperatorProvider,
    ProviderExecuteResult,
)


def test_web_and_tui_can_share_and_resume_one_agent_thread() -> None:
    client = TestClient(create_app(AgentRuntime()))
    headers = {"X-Owner-ID": "user_1"}
    start = client.post(
        "/api/work-orders/agent/start",
        headers=headers,
        json={
            "work_order_id": "work_order_api",
            "requirement": "筛选清晰图片并去重",
            "data_sources": [
                {"type": "local_directory", "uri": "D:/images", "mapping": {}}
            ],
        },
    )
    assert start.status_code == 201
    first = start.json()
    assert first["interrupts"][0]["value"]["kind"] == "task_spec_confirmation"

    spec_approved = client.post(
        "/api/work-orders/work_order_api/agent/resume",
        headers=headers,
        json={"decision": {"approved": True, "channel": "tui"}},
    )
    assert spec_approved.status_code == 200
    second = spec_approved.json()
    assert second["thread_id"] == first["thread_id"]
    assert second["interrupts"][0]["value"]["kind"] == "pipeline_approval"
    approval_pipelines = second["interrupts"][0]["value"]["pipelines"]
    assert all(
        item["execution_eligibility"] == {"eligible": True, "violations": []}
        for item in approval_pipelines
    )
    assert all(
        node["operator_status"] in {"PERSONAL_RELEASE", "PUBLIC_RELEASE"}
        for item in approval_pipelines
        for node in item["nodes"]
    )

    balanced = next(
        item
        for item in second["state"]["representative_pipelines"]
        if item["strategy"] == "balanced"
    )
    pipeline_approved = client.post(
        "/api/work-orders/work_order_api/agent/resume",
        headers=headers,
        json={
            "decision": {
                "approved": True,
                "pipeline_id": balanced["id"],
                "channel": "web",
            }
        },
    )
    assert pipeline_approved.status_code == 200
    final = pipeline_approved.json()
    assert final["thread_id"] == first["thread_id"]
    assert final["interrupts"] == []
    assert final["state"]["selected_pipeline_id"] != balanced["id"]
    assert final["state"]["approved_pipeline"]["parent_version_id"] == balanced["id"]
    assert final["state"]["approved_pipeline"]["approved"] is True
    assert final["state"]["sampling_plan"]["random_seed"] == 42


def test_agent_thread_is_owner_isolated() -> None:
    client = TestClient(create_app(AgentRuntime()))
    client.post(
        "/api/work-orders/agent/start",
        headers={"X-Owner-ID": "owner_a"},
        json={
            "work_order_id": "private_work_order",
            "requirement": "filter images",
            "data_sources": [
                {"type": "local_directory", "uri": "D:/images", "mapping": {}}
            ],
        },
    )

    response = client.get(
        "/api/work-orders/private_work_order/agent/state",
        headers={"X-Owner-ID": "owner_b"},
    )
    assert response.status_code == 403


def test_operator_catalog_and_persistent_preview_api(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    image_path = source / "sample.png"
    Image.new("RGB", (640, 480), (120, 130, 140)).save(image_path)

    client = TestClient(create_app(AgentRuntime(tmp_path / "runtime")))
    headers = {"X-Owner-ID": "user_1"}
    categories = client.get("/api/operator-categories", headers=headers)
    assert categories.status_code == 200
    assert categories.json()["INGESTION"] == 1
    filters = client.get("/api/operators?category=FILTERING", headers=headers)
    assert filters.status_code == 200
    assert any(
        item["secondary_category"] == "image_quality" for item in filters.json()
    )

    client.post(
        "/api/work-orders/agent/start",
        headers=headers,
        json={
            "work_order_id": "preview_work_order",
            "requirement": "筛选清晰图片并去重",
            "data_sources": [
                {"type": "local_directory", "uri": str(source), "mapping": {}}
            ],
        },
    )
    second = client.post(
        "/api/work-orders/preview_work_order/agent/resume",
        headers=headers,
        json={"decision": {"approved": True}},
    ).json()
    pipeline = next(
        item
        for item in second["state"]["representative_pipelines"]
        if item["strategy"] == "retention_first"
    )

    preview = client.post(
        "/api/work-orders/preview_work_order/previews",
        headers=headers,
        json={"pipeline_version_id": pipeline["id"], "source_path": str(image_path)},
    )
    assert preview.status_code == 200
    assert preview.json()["pipeline_version_id"] == pipeline["id"]
    assert preview.json()["items"][0]["node_id"] == "ingest"

    outside = tmp_path / "outside.png"
    Image.new("RGB", (32, 32), "white").save(outside)
    forbidden = client.post(
        "/api/work-orders/preview_work_order/previews",
        headers=headers,
        json={"pipeline_version_id": pipeline["id"], "source_path": str(outside)},
    )
    assert forbidden.status_code == 403


def test_provider_execution_api_is_scoped_to_work_order_sources(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    image_path = source / "sample.png"
    Image.new("RGB", (64, 48), (120, 130, 140)).save(image_path)

    def shape_filter(min_width: int = 1) -> None:
        return None

    class Searcher:
        def search(self):
            return [
                {
                    "name": "image_shape_filter",
                    "desc": "Filter by image shape",
                    "type": "filter",
                    "tags": ["cpu", "image"],
                    "sig": inspect.signature(shape_filter),
                }
            ]

    provider = DataJuicerOperatorProvider(
        searcher_factory=Searcher,
        executor=lambda request: ProviderExecuteResult(
            ok=True,
            result=OperatorResult(
                output_path=request.input_data.current_path,
                labels={"provider_called": True},
            ),
        ),
        provider_version="test",
    )
    runtime = AgentRuntime(tmp_path / "runtime", include_datajuicer=False)
    runtime.operator_library.providers.register(provider)
    client = TestClient(create_app(runtime))
    headers = {"X-Owner-ID": "user_1"}
    client.post(
        "/api/work-orders/agent/start",
        headers=headers,
        json={
            "work_order_id": "provider_work_order",
            "requirement": "filter images",
            "data_sources": [
                {"type": "local_directory", "uri": str(source), "mapping": {}}
            ],
        },
    )
    client.post(
        "/api/work-orders/provider_work_order/agent/resume",
        headers=headers,
        json={"decision": {"approved": True}},
    )

    health = client.get("/api/operator-providers", headers=headers)
    assert health.status_code == 200
    assert health.json()[0]["provider_id"] == "datajuicer"
    catalog = client.get(
        "/api/operator-providers/datajuicer/operators?query=shape",
        headers=headers,
    )
    assert catalog.status_code == 200
    assert catalog.json()[0]["provider_operator_ref"] == "image_shape_filter"
    executed = client.post(
        "/api/work-orders/provider_work_order/operator-providers/datajuicer/execute",
        headers=headers,
        json={
            "provider_operator_ref": "image_shape_filter",
            "source_path": str(image_path),
            "parameters": {"min_width": 1},
        },
    )
    assert executed.status_code == 200
    assert executed.json()["labels"]["provider_called"] is True

    outside = tmp_path / "outside.png"
    Image.new("RGB", (32, 32), "white").save(outside)
    forbidden = client.post(
        "/api/work-orders/provider_work_order/operator-providers/datajuicer/execute",
        headers=headers,
        json={
            "provider_operator_ref": "image_shape_filter",
            "source_path": str(outside),
            "parameters": {},
        },
    )
    assert forbidden.status_code == 403
