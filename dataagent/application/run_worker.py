from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from ..execution import DatasetRunExecutor
from ..evaluation import QualityEvaluator
from ..infrastructure import DomainVersionStore, RunStore, SqliteDatabase
from ..operators import build_operator_library


class LocalRunWorker:
    """Single-host durable worker for the local deployment profile."""

    def __init__(
        self,
        home: Path,
        *,
        recover_interrupted: bool = True,
        include_datajuicer: bool = True,
        allow_model_download: bool = False,
        datajuicer_python: Path | None = None,
        datajuicer_process_bin: Path | None = None,
        datajuicer_timeout_seconds: int = 300,
        remote_asset_timeout_seconds: int = 90,
        vision_model: str = "qwen3.7-plus",
        vision_api_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        vlm_gateway: Callable[..., dict[str, Any]] | None = None,
        worker_concurrency: int = 1,
        run_outcome_handler: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.home = home.expanduser().resolve()
        database = SqliteDatabase(self.home / "control.db")
        self.run_store = RunStore(database)
        self.version_store = DomainVersionStore(database)
        self.run_outcome_handler = run_outcome_handler
        if recover_interrupted:
            self.run_store.recover_interrupted()
        self.executor = DatasetRunExecutor(
            home=self.home,
            run_store=self.run_store,
            version_store=self.version_store,
            operator_runtime=build_operator_library(
                include_datajuicer=include_datajuicer,
                allow_model_download=allow_model_download,
                datajuicer_python=datajuicer_python,
                datajuicer_process_bin=datajuicer_process_bin,
                datajuicer_runtime_root=self.home / "providers" / "datajuicer",
                datajuicer_timeout_seconds=datajuicer_timeout_seconds,
                remote_asset_timeout_seconds=remote_asset_timeout_seconds,
                vision_model=vision_model,
                vision_api_base_url=vision_api_base_url,
                vlm_gateway=vlm_gateway,
            ).runtime,
            quality_evaluator=QualityEvaluator(self.version_store),
            worker_concurrency=worker_concurrency,
        )

    def process_next(self) -> dict[str, Any] | None:
        run = self.run_store.claim_next()
        if run is None:
            if self.run_outcome_handler is None:
                return None
            pending = self.run_store.pending_outcome_notifications()
            if not pending:
                return None
            return (
                pending[0]
                if self._notify_run_outcome(pending[0])
                else None
            )
        try:
            completed = self.executor.execute(run["id"])
        except Exception:
            completed = self.run_store.get(run["id"])
        self._notify_run_outcome(completed)
        return completed

    def _notify_run_outcome(self, completed: dict[str, Any]) -> bool:
        if self.run_outcome_handler is None:
            return True
        try:
            self.run_outcome_handler(completed)
        except Exception as exc:
            self.run_store.add_event(
                completed["id"],
                "run_outcome_notification_failed",
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return False
        self.run_store.add_event(
            completed["id"],
            "run_outcome_notified",
            {"work_order_id": completed["work_order_id"]},
        )
        return True

    def run_forever(self, poll_interval: float = 1.0) -> None:
        while True:
            if self.process_next() is None:
                time.sleep(poll_interval)
