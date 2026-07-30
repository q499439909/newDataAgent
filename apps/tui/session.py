from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterator, Protocol


class TuiClient(Protocol):
    def create_conversation(self) -> dict[str, Any]: ...

    def get_conversation(self, conversation_id: str) -> dict[str, Any]: ...

    def send_message(self, conversation_id: str, content: str) -> dict[str, Any]: ...

    def stream_message(
        self, conversation_id: str, content: str
    ) -> Iterator[dict[str, Any]]: ...

    def bind_work_order(
        self, conversation_id: str, work_order_id: str
    ) -> dict[str, Any]: ...

    def start_work_order(
        self, *, requirement: str, source: str, work_order_id: str | None = None
    ) -> dict[str, Any]: ...

    def state(self, work_order_id: str) -> dict[str, Any]: ...

    def resume(self, work_order_id: str, decision: dict[str, Any]) -> dict[str, Any]: ...

    def submit_run(self, work_order_id: str, idempotency_key: str) -> dict[str, Any]: ...

    def list_runs(self, work_order_id: str) -> list[dict[str, Any]]: ...

    def get_run(self, run_id: str) -> dict[str, Any]: ...

    def get_run_node_results(self, run_id: str) -> list[dict[str, Any]]: ...

    def control_run(self, run_id: str, action: str) -> dict[str, Any]: ...

    def get_dataset(self, dataset_version_id: str) -> dict[str, Any]: ...

    def get_qc_report(self, qc_report_id: str) -> dict[str, Any]: ...

    def repair_candidates(self, reference_id: str) -> dict[str, Any]: ...

    def retry_failed_assets(
        self, run_id: str, idempotency_key: str
    ) -> dict[str, Any]: ...

    def exclude_abandoned_assets(self, dataset_version_id: str) -> dict[str, Any]: ...

    def export_dataset(
        self, dataset_version_id: str, destination: str
    ) -> dict[str, Any]: ...


@dataclass
class TuiSession:
    client: TuiClient
    conversation_id: str | None = None
    work_order_id: str | None = None
    active_run_id: str | None = None
    turn: dict[str, Any] | None = None

    def ensure_conversation(self) -> dict[str, Any]:
        if self.conversation_id:
            return self.client.get_conversation(self.conversation_id)
        conversation = self.client.create_conversation()
        self.conversation_id = conversation["id"]
        self.work_order_id = conversation.get("work_order_id")
        return conversation

    def chat(self, content: str) -> dict[str, Any]:
        self.ensure_conversation()
        response = self.client.send_message(self.conversation_id, content)
        self._accept_chat_response(response)
        return response

    def chat_stream(self, content: str) -> Iterator[dict[str, Any]]:
        self.ensure_conversation()
        stream_message = getattr(self.client, "stream_message", None)
        if not callable(stream_message):
            yield {"type": "final", "response": self.chat(content)}
            return
        for event in stream_message(self.conversation_id, content):
            if event.get("type") == "final":
                response = event["response"]
                self._accept_chat_response(response)
            yield event

    def _accept_chat_response(self, response: dict[str, Any]) -> None:
        self.work_order_id = response.get("work_order_id") or self.work_order_id
        if response.get("turn"):
            self.turn = response["turn"]
            self.work_order_id = self.turn["work_order_id"]
        if response.get("run"):
            self.active_run_id = response["run"]["id"]

    def start(self, *, requirement: str, source: str) -> dict[str, Any]:
        response = self.chat(
            f"请创建一个图片数据任务。数据目录是 {source}。需求是：{requirement}"
        )
        if not response.get("turn"):
            raise ValueError(response.get("reply") or "Work order was not created")
        return response["turn"]

    def open(self, work_order_id: str) -> dict[str, Any]:
        self.ensure_conversation()
        self.client.bind_work_order(self.conversation_id, work_order_id)
        self.turn = self.client.state(work_order_id)
        self.work_order_id = work_order_id
        runs = self.client.list_runs(work_order_id)
        self.active_run_id = runs[0]["id"] if runs else None
        return self.turn

    def refresh(self) -> dict[str, Any]:
        return self.open(self._require_work_order())

    def approve(self, strategy: str = "balanced") -> dict[str, Any]:
        work_order_id = self._require_work_order()
        interrupt = self._interrupt()
        kind = interrupt.get("kind")
        decision: dict[str, Any] = {"approved": True, "channel": "tui"}
        if kind == "pipeline_approval":
            normalized = {
                "retain": "retention_first",
                "retention": "retention_first",
                "quality": "quality_first",
            }.get(strategy, strategy)
            pipelines = interrupt.get("pipelines", [])
            selected = next(
                (item for item in pipelines if item.get("strategy") == normalized), None
            )
            if selected is None:
                raise ValueError(f"Unknown pipeline strategy: {strategy}")
            decision["pipeline_id"] = selected["id"]
        elif kind not in {
            "task_spec_confirmation",
            "operator_plan_confirmation",
        }:
            raise ValueError("Current work order is not waiting for approval")
        self.turn = self.client.resume(work_order_id, decision)
        return self.turn

    def reject(self, reason: str = "rejected from tui") -> dict[str, Any]:
        work_order_id = self._require_work_order()
        self._interrupt()
        self.turn = self.client.resume(
            work_order_id,
            {"approved": False, "reason": reason, "channel": "tui"},
        )
        return self.turn

    def submit_run(self) -> dict[str, Any]:
        work_order_id = self._require_work_order()
        run = self.client.submit_run(
            work_order_id, f"tui-{work_order_id}-{uuid.uuid4().hex[:12]}"
        )
        self.active_run_id = run["id"]
        return run

    def runs(self) -> list[dict[str, Any]]:
        return self.client.list_runs(self._require_work_order())

    def run(self, run_id: str | None = None) -> dict[str, Any]:
        selected = run_id or self.active_run_id
        if not selected:
            raise ValueError("No active run")
        self.active_run_id = selected
        return self.client.get_run(selected)

    def control(self, action: str, run_id: str | None = None) -> dict[str, Any]:
        selected = run_id or self.active_run_id
        if not selected:
            raise ValueError("No active run")
        self.active_run_id = selected
        return self.client.control_run(selected, action)

    def audit(self, run_id: str | None = None) -> list[dict[str, Any]]:
        selected = run_id or self.active_run_id
        if not selected:
            raise ValueError("No active run")
        self.active_run_id = selected
        return self.client.get_run_node_results(selected)

    def result(
        self, run_id: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        run = self.run(run_id)
        dataset = (
            self.client.get_dataset(run["dataset_version_id"])
            if run.get("dataset_version_id")
            else None
        )
        report = (
            self.client.get_qc_report(run["qc_report_id"])
            if run.get("qc_report_id")
            else None
        )
        return dataset or {}, report

    def repair(self, reference_id: str | None = None) -> dict[str, Any]:
        selected = reference_id or self.active_run_id
        if not selected:
            raise ValueError("Run or DatasetVersion id is required")
        candidates = self.client.repair_candidates(selected)
        result: dict[str, Any] = {"candidates": candidates, "run": None}
        if candidates.get("retry_allowed"):
            run = self.client.retry_failed_assets(
                candidates["run_id"],
                f"tui-repair-{candidates['run_id']}-{uuid.uuid4().hex[:12]}",
            )
            self.active_run_id = run["id"]
            result["run"] = run
        return result

    def exclude(self, dataset_version_id: str) -> dict[str, Any]:
        if not dataset_version_id:
            raise ValueError("DatasetVersion id is required")
        return self.client.exclude_abandoned_assets(dataset_version_id)

    def export(self, dataset_version_id: str, destination: str) -> dict[str, Any]:
        if not dataset_version_id or not destination:
            raise ValueError("DatasetVersion id and destination are required")
        return self.client.export_dataset(dataset_version_id, destination)

    def _interrupt(self) -> dict[str, Any]:
        if not self.turn:
            self.refresh()
        interrupts = self.turn.get("interrupts", []) if self.turn else []
        if not interrupts:
            raise ValueError("Current work order has no pending approval")
        return interrupts[0]["value"]

    def _require_work_order(self) -> str:
        if not self.work_order_id:
            raise ValueError("No work order is open")
        return self.work_order_id
