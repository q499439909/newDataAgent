from __future__ import annotations

from apps.tui.app import parse_new_command
from apps.tui.session import TuiSession


class FakeControlPlaneClient:
    def __init__(self) -> None:
        self.stage = "new"
        self.controls: list[str] = []

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
