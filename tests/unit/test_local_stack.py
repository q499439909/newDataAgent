from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.local_stack import (
    LocalRuntimeIdentity,
    LocalStackError,
    assert_matching_health,
    child_command,
)


def test_api_health_exposes_local_runtime_identity(monkeypatch) -> None:
    monkeypatch.setenv("DATAAGENT_INSTANCE_ID", "instance_test")
    monkeypatch.setenv("DATAAGENT_SOURCE_REVISION", "revision_test")

    client = TestClient(create_app(runtime=AgentRuntime()))

    assert client.get("/health").json() == {
        "status": "ok",
        "instance_id": "instance_test",
        "source_revision": "revision_test",
        "version": "0.1.0",
        "role": "api",
    }


def test_local_stack_rejects_an_api_from_another_launch() -> None:
    identity = LocalRuntimeIdentity(
        instance_id="instance_current",
        source_revision="revision_current",
        version="0.1.0",
    )

    with pytest.raises(LocalStackError, match="another DataAgent instance"):
        assert_matching_health(
            {
                "status": "ok",
                "instance_id": "instance_stale",
                "source_revision": "revision_stale",
                "version": "0.1.0",
            },
            identity,
        )


def test_local_stack_uses_python_modules_for_cross_platform_children() -> None:
    executable = Path("D:/runtime/python.exe")

    assert child_command(executable, "api") == [
        str(executable),
        "-m",
        "apps.api.runner",
    ]
    assert child_command(executable, "worker") == [
        str(executable),
        "-m",
        "apps.worker.runner",
    ]
    assert child_command(
        executable,
        "tui",
        owner_id="local-user",
        api_url="http://127.0.0.1:8000",
    ) == [
        str(executable),
        "-m",
        "apps.tui.runner",
        "--owner",
        "local-user",
        "--api-url",
        "http://127.0.0.1:8000",
    ]
