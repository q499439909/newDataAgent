from __future__ import annotations

from contextlib import contextmanager
from io import StringIO
import time

import httpx
import pytest
from rich.console import Console

from apps.tui.app import TuiApp, parse_new_command
from apps.tui.api_client import ControlPlaneClient, ControlPlaneError
from apps.tui.session import TuiSession


class FakeControlPlaneClient:
    def __init__(self) -> None:
        self.stage = "new"
        self.controls: list[str] = []
        self.exports: list[tuple[str, str]] = []

    def create_conversation(self):
        return {"id": "conversation_1", "work_order_id": None, "messages": []}

    def get_conversation(self, conversation_id):
        return {
            "id": conversation_id,
            "work_order_id": "work_order_1" if self.stage != "new" else None,
            "messages": [],
        }

    def send_message(self, conversation_id, content):
        if "请创建一个图片数据任务" in content:
            self.stage = "spec"
            turn = self._turn(
                [{"id": "interrupt_1", "value": {"kind": "task_spec_confirmation"}}]
            )
            return {
                "conversation_id": conversation_id,
                "work_order_id": "work_order_1",
                "reply": "已创建工单。",
                "turn": turn,
                "run": None,
            }
        return {
            "conversation_id": conversation_id,
            "work_order_id": "work_order_1" if self.stage != "new" else None,
            "reply": "你好，我是 DataAgent。",
            "turn": None,
            "run": None,
        }

    def bind_work_order(self, conversation_id, work_order_id):
        self.stage = "ready"
        return {
            "id": conversation_id,
            "work_order_id": work_order_id,
            "messages": [],
        }

    def start_work_order(self, *, requirement, source, work_order_id=None):
        assert requirement == "筛选清晰图片"
        assert source == "D:\\images"
        self.stage = "spec"
        return self._turn(
            [{"id": "interrupt_1", "value": {"kind": "task_spec_confirmation"}}]
        )

    def state(self, work_order_id):
        return self._turn([])

    def resume(self, work_order_id, decision):
        assert decision["channel"] == "tui"
        if self.stage == "spec":
            self.stage = "pipeline"
            return self._turn(
                [
                    {
                        "id": "interrupt_2",
                        "value": {
                            "kind": "pipeline_approval",
                            "pipelines": [
                                {
                                    "id": "pipeline_retain",
                                    "strategy": "retention_first",
                                    "version": 1,
                                },
                                {
                                    "id": "pipeline_balanced",
                                    "strategy": "balanced",
                                    "version": 1,
                                },
                                {
                                    "id": "pipeline_quality",
                                    "strategy": "quality_first",
                                    "version": 1,
                                },
                            ],
                        },
                    }
                ]
            )
        assert decision["pipeline_id"] == "pipeline_quality"
        self.stage = "ready"
        return self._turn([])

    def submit_run(self, work_order_id, idempotency_key):
        assert idempotency_key.startswith("tui-work_order_1-")
        return self._run("QUEUED")

    def list_runs(self, work_order_id):
        return [self._run("SUCCEEDED")]

    def get_run(self, run_id):
        return self._run("SUCCEEDED")

    def get_run_node_results(self, run_id):
        return [
            {
                "source_uri": "D:/images/rejected.png",
                "node_id": "quality",
                "operator_version_id": "builtin.quality_filter:1",
                "status": "completed",
                "decision": "reject",
                "reason_codes": ["QUALITY_BELOW_THRESHOLD"],
                "duration_ms": 12,
            }
        ]

    def control_run(self, run_id, action):
        self.controls.append(action)
        return self._run({"pause": "PAUSED", "resume": "QUEUED", "cancel": "CANCELLED"}[action])

    def get_dataset(self, dataset_version_id):
        return {
            "id": dataset_version_id,
            "source_count": 1,
            "kept_count": 1,
            "rejected_count": 0,
            "failed_count": 0,
            "manifest_uri": "D:/datasets/dataset_1/manifest.json",
        }

    def get_qc_report(self, qc_report_id):
        return {"id": qc_report_id, "status": "PASSED"}

    def repair_candidates(self, reference_id):
        return {
            "reference_id": reference_id,
            "run_id": "run_1",
            "dataset_version_id": "dataset_partial",
            "run_status": "PARTIAL",
            "repair_attempt": 1,
            "maximum_repair_attempts": 3,
            "retry_allowed": True,
            "still_failed": [
                {
                    "source_uri": "D:/images/failed.png",
                    "reason_codes": ["OPERATOR_ERROR:TimeoutError"],
                    "repair_attempts": 1,
                }
            ],
            "abandoned_assets": [],
            "excluded_assets": [],
            "next_actions": ["retry_failed_assets"],
        }

    def retry_failed_assets(self, run_id, idempotency_key):
        assert run_id == "run_1"
        assert idempotency_key.startswith("tui-repair-run_1-")
        return {**self._run("QUEUED"), "id": "run_repair_2"}

    def exclude_abandoned_assets(self, dataset_version_id):
        assert dataset_version_id == "dataset_abandoned"
        return {
            "id": "dataset_resolved",
            "still_failed": [],
            "abandoned_assets": [],
            "excluded_assets": [{"source_uri": "D:/images/failed.png"}],
        }

    def export_dataset(self, dataset_version_id, destination):
        self.exports.append((dataset_version_id, destination))
        return {
            "id": "export_1",
            "dataset_version_id": dataset_version_id,
            "root_uri": destination,
            "manifest_uri": f"{destination}/manifest.json",
            "excluded_assets_uri": f"{destination}/excluded_assets.json",
            "file_count": 1,
        }

    @staticmethod
    def _turn(interrupts):
        return {
            "work_order_id": "work_order_1",
            "thread_id": "thread_1",
            "state": {"next_action": "test"},
            "interrupts": interrupts,
        }

    @staticmethod
    def _run(status):
        return {
            "id": "run_1",
            "status": status,
            "progress": 1,
            "total": 1,
            "dataset_version_id": "dataset_1" if status == "SUCCEEDED" else None,
            "qc_report_id": "qc_report_1" if status == "SUCCEEDED" else None,
        }


def test_parse_new_command_preserves_windows_path() -> None:
    source, requirement = parse_new_command("D:\\images\\incoming | 筛选清晰图片")
    assert source == "D:\\images\\incoming"
    assert requirement == "筛选清晰图片"


def test_tui_session_controls_the_shared_work_order_and_run() -> None:
    client = FakeControlPlaneClient()
    session = TuiSession(client)

    first = session.start(requirement="筛选清晰图片", source="D:\\images")
    assert first["interrupts"][0]["value"]["kind"] == "task_spec_confirmation"
    second = session.approve()
    assert second["interrupts"][0]["value"]["kind"] == "pipeline_approval"
    final = session.approve("quality")
    assert final["interrupts"] == []

    submitted = session.submit_run()
    assert submitted["status"] == "QUEUED"
    assert session.control("pause")["status"] == "PAUSED"
    assert session.control("resume")["status"] == "QUEUED"
    dataset, report = session.result()
    assert dataset["id"] == "dataset_1"
    assert report is not None
    assert report["status"] == "PASSED"


class StubConsole:
    def __init__(self, source: str = "D:\\images") -> None:
        self.source = source
        self.messages: list[str] = []
        self.input_calls = 0

    def print(self, value="") -> None:
        self.messages.append(str(value))

    def input(self, prompt: str) -> str:
        self.input_calls += 1
        self.messages.append(prompt)
        return self.source

    @contextmanager
    def status(self, message: str):
        self.messages.append(message)
        yield


def test_tui_greeting_does_not_start_a_work_order() -> None:
    console = StubConsole()
    session = TuiSession(FakeControlPlaneClient())
    app = TuiApp(session, console=console)

    app.handle("你好！")

    assert session.work_order_id is None
    assert console.input_calls == 0
    assert any("DataAgent" in message for message in console.messages)


def test_tui_requirement_is_sent_to_conversation_without_local_prompt() -> None:
    console = StubConsole()
    session = TuiSession(FakeControlPlaneClient())
    app = TuiApp(session, console=console)

    app.handle("筛选清晰图片")

    assert session.work_order_id is None
    assert console.input_calls == 0
    assert any("DataAgent" in message for message in console.messages)


def test_tui_streams_actions_before_rendering_the_final_reply() -> None:
    class StreamingClient(FakeControlPlaneClient):
        def stream_message(self, conversation_id, content):
            yield {
                "type": "action",
                "action": {
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
                },
            }
            yield {
                "type": "final",
                "response": self.send_message(conversation_id, content),
            }

    stream = StringIO()
    console = Console(file=stream, width=220, color_system=None)
    app = TuiApp(TuiSession(StreamingClient()), console=console)

    app.handle("你好")

    output = stream.getvalue()
    assert output.index("Requirement Analyzer") < output.index("DataAgent>")
    assert output.count("Requirement Analyzer") == 1


def test_tui_pipeline_approval_renders_real_nodes_and_strategy_differences() -> None:
    stream = StringIO()
    console = Console(file=stream, width=220, color_system=None)
    app = TuiApp(TuiSession(FakeControlPlaneClient()), console=console)
    pipelines = []
    for strategy, threshold in (
        ("retention_first", 0.35),
        ("balanced", 0.55),
        ("quality_first", 0.75),
    ):
        pipelines.append(
            {
                "id": f"pipeline_{strategy}",
                "strategy": strategy,
                "version": 1,
                "nodes": [
                    {
                        "id": "quality_filter",
                        "operator_version_id": "builtin.quality_filter:1",
                        "runtime_backend": "cpu",
                        "operator_status": "PERSONAL_RELEASE",
                        "parameters": {"confidence_threshold": threshold},
                    },
                    {
                        "id": "image_classification",
                        "operator_version_id": (
                            "datajuicer.image_tagging_vlm_mapper.remote_api:2"
                        ),
                        "runtime_backend": "remote",
                        "operator_status": "DRAFT",
                        "parameters": {"tag_field_name": "image_tags"},
                    },
                ],
                "execution_eligibility": {"eligible": True, "violations": []},
            }
        )
    app._render_turn(
        {
            "work_order_id": "work_order_1",
            "thread_id": "thread_1",
            "state": {"current_agent": "processing", "next_action": "approve_pipeline"},
            "interrupts": [
                {"value": {"kind": "pipeline_approval", "pipelines": pipelines}}
            ],
        }
    )

    output = stream.getvalue()
    assert "builtin.quality_filter:1" in output
    assert "datajuicer.image_tagging_vlm_mapper.remote_api:2" in output
    assert "Strategy differences" in output
    assert "PERSONAL_RELEASE" in output and "DRAFT" in output
    assert "yes" in output
    assert "Blocked reason" in output
    assert "0.35" in output and "0.55" in output and "0.75" in output


def test_tui_renders_auditable_action_summary_without_hidden_reasoning() -> None:
    stream = StringIO()
    console = Console(file=stream, width=220, color_system=None)
    app = TuiApp(TuiSession(FakeControlPlaneClient()), console=console)

    app._render_conversation(
        {
            "reply": "TaskSpec 已确认。",
            "turn": None,
            "run": None,
            "action_trace": [
                {
                    "id": "tool_trace_1",
                    "stage": "retrieve_operator_candidates",
                    "tool": "retrieve_operators",
                    "display_name": "Operator Retriever",
                    "status": "succeeded",
                    "parameters": {
                        "required_capabilities": [
                            "image_quality",
                            "image_classification",
                        ]
                    },
                    "duration_ms": 12,
                    "summary": "Found 12 candidate operators.",
                    "evidence_ids": ["operator_1", "operator_2"],
                    "error_type": None,
                }
            ],
        }
    )

    output = stream.getvalue()
    assert "行动摘要" in output
    assert "Operator Retriever" in output
    assert "Found 12 candidate operators." in output
    assert "operator_1" in output
    assert "12 ms" in output
    assert "思维链" not in output


def test_tui_pipeline_table_shows_execution_block_reason() -> None:
    stream = StringIO()
    console = Console(file=stream, width=220, color_system=None)
    app = TuiApp(TuiSession(FakeControlPlaneClient()), console=console)
    app._render_turn(
        {
            "work_order_id": "work_order_1",
            "thread_id": "thread_1",
            "state": {"current_agent": "processing", "next_action": "approve_pipeline"},
            "interrupts": [
                {
                    "value": {
                        "kind": "pipeline_approval",
                        "pipelines": [
                            {
                                "id": "pipeline_quality",
                                "strategy": "quality_first",
                                "version": 1,
                                "nodes": [],
                                "execution_eligibility": {
                                    "eligible": False,
                                    "violations": ["provider parameter schema is invalid"],
                                },
                            }
                        ],
                    }
                }
            ],
        }
    )

    output = stream.getvalue()
    assert "no" in output
    assert "provider parameter schema is invalid" in output


def test_tui_automatically_reports_run_terminal_status() -> None:
    class ProgressingClient(FakeControlPlaneClient):
        def __init__(self) -> None:
            super().__init__()
            self.polls = 0

        def get_run(self, run_id):
            self.polls += 1
            return self._run("SUCCEEDED" if self.polls >= 1 else "RUNNING")

    client = ProgressingClient()
    console = StubConsole()
    app = TuiApp(TuiSession(client), console=console)
    app._render_conversation(
        {"reply": "submitted", "turn": None, "run": client._run("QUEUED")}
    )

    deadline = time.monotonic() + 2.5
    while not any("finished with status SUCCEEDED" in item for item in console.messages):
        if time.monotonic() >= deadline:
            raise AssertionError("run monitor did not report terminal status")
        time.sleep(0.02)

    assert client.polls == 2
    assert app._run_monitors == {}


def test_tui_audit_renders_grounded_per_node_reasons() -> None:
    stream = StringIO()
    console = Console(file=stream, width=220, color_system=None)
    session = TuiSession(FakeControlPlaneClient(), active_run_id="run_1")
    app = TuiApp(session, console=console)

    app.handle("/audit")

    output = stream.getvalue()
    assert "D:/images/rejected.png" in output
    assert "builtin.quality_filter:1" in output
    assert "QUALITY_BELOW_THRESHOLD" in output


def test_tui_repair_exclude_and_export_use_grounded_control_endpoints() -> None:
    stream = StringIO()
    console = Console(file=stream, width=220, color_system=None)
    client = FakeControlPlaneClient()
    session = TuiSession(client, active_run_id="run_1")
    app = TuiApp(session, console=console)

    app.handle("/repair")
    app.handle("/exclude dataset_abandoned")
    app.handle(r"/export dataset_resolved D:\exports\cat dog result")

    output = stream.getvalue()
    assert session.active_run_id == "run_repair_2"
    assert "D:/images/failed.png" in output
    assert "OPERATOR_ERROR:TimeoutError" in output
    assert "1/3" in output
    assert "dataset_resolved" in output
    assert "export_1" in output
    assert client.exports == [
        ("dataset_resolved", r"D:\exports\cat dog result")
    ]


def test_control_plane_errors_are_translated_without_raw_status_prefix() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"detail": "Pipeline cannot be approved for execution"},
        )

    client = ControlPlaneClient(
        base_url="http://dataagent.test",
        owner_id="user_1",
    )
    client._client.close()
    client._client = httpx.Client(
        base_url="http://dataagent.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ControlPlaneError) as exc_info:
        client.state("work_order_1")

    message = str(exc_info.value)
    assert message.startswith("请求未执行：")
    assert "Pipeline cannot be approved for execution" in message
    assert not message.startswith("422:")
    client.close()


def test_control_plane_client_parses_ndjson_conversation_stream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/messages/stream")
        return httpx.Response(
            200,
            content=(
                '{"type":"action","action":{"tool":"conversation_turn"}}\n'
                '{"type":"final","response":{"reply":"完成"}}\n'
            ).encode("utf-8"),
            headers={"content-type": "application/x-ndjson"},
        )

    client = ControlPlaneClient(
        base_url="http://dataagent.test",
        owner_id="user_1",
    )
    client._client.close()
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://dataagent.test",
        headers={"X-Owner-ID": "user_1"},
    )

    events = list(client.stream_message("conversation_1", "你好"))

    assert [event["type"] for event in events] == ["action", "final"]
    assert events[1]["response"]["reply"] == "完成"
    client.close()


def test_control_plane_client_default_timeout_is_ten_minutes() -> None:
    client = ControlPlaneClient(
        base_url="http://dataagent.test",
        owner_id="user_1",
    )

    timeout = client._client.timeout

    assert timeout.connect == 600.0
    assert timeout.read == 600.0
    assert timeout.write == 600.0
    assert timeout.pool == 600.0
    client.close()
