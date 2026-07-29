from __future__ import annotations

import json

from fastapi.testclient import TestClient

from apps.api.main import create_app
from dataagent.application.work_order_runtime import WorkOrderRuntime


class FakeStreamingConversationService:
    def get(self, thread_id: str, owner_id: str):
        return {"id": thread_id, "owner_id": owner_id}

    def send(
        self,
        *,
        thread_id: str,
        owner_id: str,
        content: str,
        action_sink=None,
    ):
        action = {
            "id": "action_trace_1",
            "stage": "analyze_requirement",
            "stage_label": "正在理解需求",
            "kind": "model",
            "tool": "conversation_turn",
            "display_name": "Requirement Analyzer",
            "status": "succeeded",
            "parameters": {"model": "glm-5.2"},
            "duration_ms": 8,
            "summary": "Resolved intent: CHAT.",
            "evidence_ids": [],
            "error_type": None,
        }
        if action_sink is not None:
            action_sink(action)
        return {
            "conversation_id": thread_id,
            "work_order_id": None,
            "reply": f"收到：{content}",
            "turn": None,
            "run": None,
            "action_trace": [action],
            "messages": [],
        }


def test_conversation_stream_emits_actions_before_final_response(tmp_path) -> None:
    app = create_app(
        WorkOrderRuntime(tmp_path / "runtime"),
        conversation_service=FakeStreamingConversationService(),
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/conversations/conversation_1/messages/stream",
        headers={"X-Owner-ID": "user_1"},
        json={"content": "你好"},
    ) as response:
        events = [
            json.loads(line)
            for line in response.iter_lines()
            if line
        ]

    assert response.status_code == 200
    assert [event["type"] for event in events] == ["action", "final"]
    assert events[0]["action"]["display_name"] == "Requirement Analyzer"
    assert events[1]["response"]["reply"] == "收到：你好"
