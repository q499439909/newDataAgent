from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app


def test_web_cockpit_is_served_from_control_plane() -> None:
    client = TestClient(create_app())

    assert client.get("/web").status_code == 200
    response = client.get("/web/")

    assert response.status_code == 200
    assert "DataAgent 工作台" in response.text


def test_web_start_payload_matches_tui_data_source_contract() -> None:
    script = Path("apps/web/app.js").read_text(encoding="utf-8")

    assert 'type: "local_directory"' in script
    assert "uri: source" in script
    assert "mapping: {}" in script
    assert 'kind: "local_directory"' not in script
    assert "path: source" not in script


def test_web_cockpit_reads_langgraph_interrupt_value_wrapper() -> None:
    script = Path("apps/web/app.js").read_text(encoding="utf-8")

    assert "function unwrapInterrupt" in script
    assert "interrupt?.value || interrupt" in script
    assert "unwrapInterrupt(payload.interrupts[0])?.kind" in script
    assert "unwrapInterrupt(payload.interrupts?.[0])" in script
