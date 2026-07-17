from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ..domain.common import new_id
from ..domain.pipelines import PipelineVersion
from ..domain.specs import TaskSpecVersion
from ..execution import NodePreviewBuilder
from ..graph import build_main_graph
from ..infrastructure import AgentThreadStore, DomainVersionStore, SqliteDatabase
from ..operators import OperatorRegistry, OperatorRuntime
from ..operators.builtin import builtin_image_operators


@dataclass(frozen=True)
class AgentThread:
    work_order_id: str
    thread_id: str
    owner_id: str


class AgentRuntime:
    """Application service exposing one shared graph to Web and TUI clients."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = home.resolve() if home is not None else None
        self._threads: dict[str, AgentThread] = {}
        self._checkpoint_connection: sqlite3.Connection | None = None
        self.thread_store: AgentThreadStore | None = None
        self.version_store: DomainVersionStore | None = None
        if home is None:
            self.checkpointer = InMemorySaver()
        else:
            home = home.resolve()
            home.mkdir(parents=True, exist_ok=True)
            control_path = home / "control.db"
            database = SqliteDatabase(control_path)
            self.thread_store = AgentThreadStore(database)
            self.version_store = DomainVersionStore(database)
            self._checkpoint_connection = sqlite3.connect(
                home / "checkpoints.db", check_same_thread=False
            )
            saver = SqliteSaver(self._checkpoint_connection)
            saver.setup()
            self.checkpointer = saver
        self.graph = build_main_graph(self.checkpointer)
        self.builtin_operators = builtin_image_operators()
        self.operator_registry = OperatorRegistry(
            operator.spec for operator in self.builtin_operators
        )

    def start(
        self,
        *,
        owner_id: str,
        requirement: str,
        data_sources: list[dict[str, Any]],
        work_order_id: str | None = None,
    ) -> dict[str, Any]:
        work_order_id = work_order_id or new_id("work_order")
        try:
            self._lookup(work_order_id)
        except KeyError:
            pass
        else:
            raise ValueError(f"Work order already has an agent thread: {work_order_id}")
        record = AgentThread(
            work_order_id=work_order_id,
            thread_id=new_id("thread"),
            owner_id=owner_id,
        )
        self._remember(record)
        state = {
            "work_order_id": work_order_id,
            "owner_id": owner_id,
            "requirement": requirement,
            "data_sources": data_sources,
            "trace": [],
        }
        result = self.graph.invoke(state, self._config(record))
        self._capture_versions(record, result)
        return self._public_result(record, result)

    def resume(
        self,
        *,
        work_order_id: str,
        owner_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        record = self._get_authorized(work_order_id, owner_id)
        result = self.graph.invoke(Command(resume=decision), self._config(record))
        self._capture_versions(record, result)
        return self._public_result(record, result)

    def state(self, *, work_order_id: str, owner_id: str) -> dict[str, Any]:
        record = self._get_authorized(work_order_id, owner_id)
        snapshot = self.graph.get_state(self._config(record))
        result = dict(snapshot.values)
        if snapshot.interrupts:
            result["__interrupt__"] = snapshot.interrupts
        return self._public_result(record, result)

    def build_node_preview(
        self,
        *,
        work_order_id: str,
        owner_id: str,
        pipeline_version_id: str,
        source_path: str,
    ) -> dict[str, Any]:
        self._get_authorized(work_order_id, owner_id)
        if self.version_store is None or self.home is None:
            raise RuntimeError("Persistent runtime is required for node previews")
        pipeline = PipelineVersion.model_validate(
            self.version_store.get(
                kind="pipeline", entity_id=pipeline_version_id, owner_id=owner_id
            )
        )
        specs = [
            TaskSpecVersion.model_validate(item)
            for item in self.version_store.list_for_owner(kind="task_spec", owner_id=owner_id)
            if item.get("work_order_id") == work_order_id and item.get("confirmed")
        ]
        if not specs:
            raise ValueError("Confirmed TaskSpec not found for work order")
        spec = max(specs, key=lambda item: item.version)
        source = Path(source_path).expanduser().resolve()
        allowed_roots = [
            Path(item.uri).expanduser().resolve()
            for item in spec.data_sources
            if item.type == "local_directory"
        ]
        if not any(source.is_relative_to(root) for root in allowed_roots):
            raise PermissionError("Preview source is outside the work order data sources")
        if not source.is_file():
            raise FileNotFoundError(f"Preview source not found: {source}")
        builder = NodePreviewBuilder(
            OperatorRuntime(self.builtin_operators), self.home / "previews"
        )
        preview = builder.build(
            pipeline=pipeline,
            source_path=source,
            owner_id=owner_id,
            work_order_id=work_order_id,
            run_id=new_id("preview_run"),
        )
        payload = preview.model_dump(mode="json")
        self.version_store.save_if_absent(
            kind="node_preview_set", owner_id=owner_id, payload=payload
        )
        return payload

    def _get_authorized(self, work_order_id: str, owner_id: str) -> AgentThread:
        record = self._lookup(work_order_id)
        if record.owner_id != owner_id:
            raise PermissionError("Work order belongs to another owner")
        return record

    def _remember(self, record: AgentThread) -> None:
        if self.thread_store is None:
            self._threads[record.work_order_id] = record
            return
        self.thread_store.add(
            work_order_id=record.work_order_id,
            thread_id=record.thread_id,
            owner_id=record.owner_id,
        )

    def _lookup(self, work_order_id: str) -> AgentThread:
        if self.thread_store is None:
            try:
                return self._threads[work_order_id]
            except KeyError as exc:
                raise KeyError(f"Work order not found: {work_order_id}") from exc
        item = self.thread_store.get(work_order_id)
        return AgentThread(**item)

    def _capture_versions(self, record: AgentThread, result: dict[str, Any]) -> None:
        if self.version_store is None:
            return
        singletons = {
            "task_spec": "task_spec",
            "retrieval_plan": "retrieval_plan",
            "sampling_plan": "sampling_plan",
        }
        for state_key, kind in singletons.items():
            payload = result.get(state_key)
            if payload:
                self.version_store.save_if_absent(
                    kind=kind, owner_id=record.owner_id, payload=payload
                )
        for payload in result.get("pipeline_variants", ()):
            self.version_store.save_if_absent(
                kind="pipeline", owner_id=record.owner_id, payload=payload
            )

    @staticmethod
    def _config(record: AgentThread) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": record.thread_id}}

    @staticmethod
    def _public_result(record: AgentThread, result: dict[str, Any]) -> dict[str, Any]:
        state = {key: value for key, value in result.items() if key != "__interrupt__"}
        interrupts = []
        for item in result.get("__interrupt__", ()):
            interrupts.append(
                {
                    "id": getattr(item, "id", None),
                    "value": getattr(item, "value", item),
                }
            )
        return {
            "work_order_id": record.work_order_id,
            "thread_id": record.thread_id,
            "state": state,
            "interrupts": interrupts,
        }
