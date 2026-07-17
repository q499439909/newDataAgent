from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..execution import DatasetRunExecutor
from ..infrastructure import DomainVersionStore, RunStore, SqliteDatabase
from ..operators import OperatorRuntime
from ..operators.builtin import builtin_image_operators


class LocalRunWorker:
    """Single-host durable worker for the local deployment profile."""

    def __init__(self, home: Path, *, recover_interrupted: bool = True) -> None:
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
            operator_runtime=OperatorRuntime(builtin_image_operators()),
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
