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
    ) -> None:
        self.home = home.expanduser().resolve()
        database = SqliteDatabase(self.home / "control.db")
        self.run_store = RunStore(database)
        self.version_store = DomainVersionStore(database)
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
            return None
        try:
            return self.executor.execute(run["id"])
        except Exception:
            return self.run_store.get(run["id"])

    def run_forever(self, poll_interval: float = 1.0) -> None:
        while True:
            if self.process_next() is None:
                time.sleep(poll_interval)
