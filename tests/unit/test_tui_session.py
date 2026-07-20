from __future__ import annotations

from contextlib import contextmanager
from io import StringIO

from rich.console import Console

from apps.tui.app import TuiApp, parse_new_command
from apps.tui.session import TuiSession


class FakeControlPlaneClient:
    def __init__(self) -> None:
        self.stage = "new"
        self.controls: list[str] = []

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

    def control_run(self, run_id, action):
        self.controls.append(action)
        return self._run({"pause": "PAUSED", "resume": "QUEUED", "cancel": "CANCELLED"}[action])

    def get_dataset(self, dataset_version_id):
        return {"id": dataset_version_id, "kept_count": 1}

    def get_qc_report(self, qc_report_id):
        return {"id": qc_report_id, "status": "PASSED"}

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
                            "datajuicer.image_tagging_vlm_mapper.remote_api:1"
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
    assert "datajuicer.image_tagging_vlm_mapper.remote_api:1" in output
    assert "Strategy differences" in output
    assert "PERSONAL_RELEASE" in output and "DRAFT" in output
    assert "yes" in output
    assert "0.35" in output and "0.55" in output and "0.75" in output
