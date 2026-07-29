from __future__ import annotations

import json
from typing import Any, Iterator

import httpx


class ControlPlaneError(RuntimeError):
    pass


class ControlPlaneClient:
    def __init__(
        self,
        *,
        base_url: str,
        owner_id: str,
        timeout: float = 600.0,
    ) -> None:
        self.owner_id = owner_id
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Owner-ID": owner_id},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def create_conversation(self) -> dict[str, Any]:
        return self._request("POST", "/api/conversations")

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/conversations/{conversation_id}")

    def send_message(self, conversation_id: str, content: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/conversations/{conversation_id}/messages",
            json={"content": content},
        )

    def stream_message(
        self, conversation_id: str, content: str
    ) -> Iterator[dict[str, Any]]:
        try:
            with self._client.stream(
                "POST",
                f"/api/conversations/{conversation_id}/messages/stream",
                json={"content": content},
            ) as response:
                if response.is_error:
                    response.read()
                    try:
                        detail = response.json().get("detail", response.text)
                    except ValueError:
                        detail = response.text
                    raise ControlPlaneError(
                        f"控制平面对话流请求失败：{detail}"
                    )
                for line in response.iter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if event.get("type") == "error":
                        raise ControlPlaneError(
                            f"控制平面对话流中断：{event.get('message', '-')}"
                        )
                    yield event
        except httpx.HTTPError as exc:
            raise ControlPlaneError(
                f"Control plane stream failed: {exc}"
            ) from exc

    def bind_work_order(self, conversation_id: str, work_order_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/conversations/{conversation_id}/work-order",
            json={"work_order_id": work_order_id},
        )

    def start_work_order(
        self, *, requirement: str, source: str, work_order_id: str | None = None
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/work-orders/agent/start",
            json={
                "requirement": requirement,
                "data_sources": [
                    {"type": "local_directory", "uri": source, "mapping": {}}
                ],
                "work_order_id": work_order_id,
            },
        )

    def state(self, work_order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/work-orders/{work_order_id}/agent/state")

    def resume(self, work_order_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/work-orders/{work_order_id}/agent/resume",
            json={"decision": decision},
        )

    def submit_run(self, work_order_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/work-orders/{work_order_id}/runs",
            headers={"Idempotency-Key": idempotency_key},
        )

    def list_runs(self, work_order_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/work-orders/{work_order_id}/runs")

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/runs/{run_id}")

    def get_run_node_results(self, run_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/runs/{run_id}/node-results")

    def control_run(self, run_id: str, action: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/api/runs/{run_id}/control", json={"action": action}
        )

    def get_dataset(self, dataset_version_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/datasets/{dataset_version_id}")

    def get_qc_report(self, qc_report_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/qc-reports/{qc_report_id}")

    def repair_candidates(self, reference_id: str) -> dict[str, Any]:
        resource = "datasets" if reference_id.startswith("dataset_") else "runs"
        return self._request(
            "GET",
            f"/api/{resource}/{reference_id}/repair-candidates",
        )

    def retry_failed_assets(
        self, run_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/runs/{run_id}/repairs",
            headers={"Idempotency-Key": idempotency_key},
        )

    def exclude_abandoned_assets(self, dataset_version_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/datasets/{dataset_version_id}/exclude-abandoned",
            json={"confirmed": True},
        )

    def export_dataset(
        self, dataset_version_id: str, destination: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/datasets/{dataset_version_id}/exports",
            json={"destination": destination},
        )

    def _request(self, method: str, path: str, **kwargs: Any):
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ControlPlaneError(f"Control plane request failed: {exc}") from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            prefix = {
                404: "没有找到对应资源",
                409: "当前状态与请求冲突",
                422: "请求未执行",
                503: "控制平面暂时不可用",
            }.get(
                response.status_code,
                f"控制平面请求失败（HTTP {response.status_code}）",
            )
            raise ControlPlaneError(f"{prefix}：{detail}")
        return response.json()
