from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ..domain.common import new_id
from ..domain.evaluations import QCReport
from ..domain.operators import OperatorSpecVersion, OperatorStatus, RuntimeBackend
from ..domain.pipelines import PipelineVersion
from ..domain.runs import DatasetVersion, RunSnapshot
from ..domain.specs import TaskSpecVersion
from ..execution import NodePreviewBuilder
from ..graph import build_main_graph
from ..infrastructure import (
    AgentThreadStore,
    ConversationStore,
    DomainVersionStore,
    RunStore,
    SqliteDatabase,
)
from ..operators import build_operator_library
from ..operators.protocol import OperatorContext, OperatorInput
from ..operators.providers import ProviderExecuteRequest


@dataclass(frozen=True)
class AgentThread:
    work_order_id: str
    thread_id: str
    owner_id: str


class AgentRuntime:
    """Application service exposing one shared graph to Web and TUI clients."""

    def __init__(
        self,
        home: Path | None = None,
        *,
        include_datajuicer: bool = True,
        allow_model_download: bool = False,
        datajuicer_python: Path | None = None,
        datajuicer_process_bin: Path | None = None,
        datajuicer_timeout_seconds: int = 300,
        allow_datajuicer_candidate_execution: bool = True,
        remote_operator_available: bool = False,
    ) -> None:
        self.home = home.resolve() if home is not None else None
        self._threads: dict[str, AgentThread] = {}
        self._checkpoint_connection: sqlite3.Connection | None = None
        self.thread_store: AgentThreadStore | None = None
        self.version_store: DomainVersionStore | None = None
        self.run_store: RunStore | None = None
        self.conversation_store: ConversationStore | None = None
        if home is None:
            self.checkpointer = InMemorySaver()
        else:
            home = home.resolve()
            home.mkdir(parents=True, exist_ok=True)
            control_path = home / "control.db"
            database = SqliteDatabase(control_path)
            self.thread_store = AgentThreadStore(database)
            self.version_store = DomainVersionStore(database)
            self.run_store = RunStore(database)
            self.conversation_store = ConversationStore(database)
            self._checkpoint_connection = sqlite3.connect(
                home / "checkpoints.db", check_same_thread=False
            )
            saver = SqliteSaver(self._checkpoint_connection)
            saver.setup()
            self.checkpointer = saver
        self.allow_datajuicer_candidate_execution = (
            allow_datajuicer_candidate_execution
        )
        self.operator_library = build_operator_library(
            include_datajuicer=include_datajuicer,
            allow_model_download=allow_model_download,
            datajuicer_python=datajuicer_python,
            datajuicer_process_bin=datajuicer_process_bin,
            datajuicer_runtime_root=(self.home / "providers" / "datajuicer")
            if self.home is not None
            else None,
            datajuicer_timeout_seconds=datajuicer_timeout_seconds,
        )
        self.builtin_operators = self.operator_library.operators
        self.operator_registry = self.operator_library.registry
        self.graph = build_main_graph(
            self.checkpointer,
            operator_library=self.operator_library,
            allow_draft_datajuicer_candidates=(
                self.allow_datajuicer_candidate_execution
            ),
            available_runtime_backends=frozenset(
                {
                    RuntimeBackend.CPU,
                    *(
                        (RuntimeBackend.REMOTE,)
                        if remote_operator_available
                        else ()
                    ),
                }
            ),
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
        source = self._authorized_source(work_order_id, owner_id, source_path)
        builder = NodePreviewBuilder(self.operator_library.runtime, self.home / "previews")
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

    def provider_health(self) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in self.operator_library.providers.health()
        ]

    def provider_operators(
        self,
        *,
        provider_id: str,
        query: str | None = None,
        limit: int = 100,
        operator_type: str | None = None,
        tag: str | None = None,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        provider = self.operator_library.providers.get(provider_id)
        health = provider.health()
        if health.status == "unavailable":
            raise RuntimeError(health.message or f"Provider is unavailable: {provider_id}")
        refresh_catalog = getattr(provider, "refresh_catalog", None)
        descriptors = (
            refresh_catalog()
            if refresh and callable(refresh_catalog)
            else provider.discover()
        )
        if operator_type:
            descriptors = [
                item
                for item in descriptors
                if item.provider_operator_type == operator_type.lower()
            ]
        if tag:
            descriptors = [item for item in descriptors if tag.lower() in item.tags]
        normalized_query = " ".join((query or "").lower().split())
        if normalized_query:
            descriptors = [
                item
                for item in descriptors
                if normalized_query
                in " ".join(
                    (
                        item.provider_operator_ref,
                        item.display_name,
                        item.description,
                        *sorted(item.tags),
                    )
                ).lower()
            ]
        return [
            item.model_dump(mode="json")
            for item in descriptors[: max(1, min(limit, 500))]
        ]

    def execute_provider_operator(
        self,
        *,
        work_order_id: str,
        owner_id: str,
        provider_id: str,
        provider_operator_ref: str,
        source_path: str,
        parameters: dict[str, Any],
        runtime_backend: RuntimeBackend = RuntimeBackend.CPU,
    ) -> dict[str, Any]:
        source = self._authorized_source(work_order_id, owner_id, source_path)
        provider = self.operator_library.providers.get(provider_id)
        result = provider.execute(
            ProviderExecuteRequest(
                provider_operator_ref=provider_operator_ref,
                runtime_backend=runtime_backend,
                context=OperatorContext(
                    run_id=new_id("provider_call"),
                    work_order_id=work_order_id,
                    owner_id=owner_id,
                    purpose="development",
                ),
                input_data=OperatorInput(
                    source_path=str(source),
                    current_path=str(source),
                ),
                parameters=parameters,
            )
        )
        if not result.ok or result.result is None:
            raise RuntimeError(
                f"Provider execution failed ({result.error_type or 'unknown'}): "
                f"{result.message}"
            )
        return result.result.model_dump(mode="json")

    def _authorized_source(
        self, work_order_id: str, owner_id: str, source_path: str
    ) -> Path:
        self._get_authorized(work_order_id, owner_id)
        if self.version_store is None:
            raise RuntimeError("Persistent runtime is required for provider execution")
        specs = [
            TaskSpecVersion.model_validate(item)
            for item in self.version_store.list_for_owner(
                kind="task_spec", owner_id=owner_id
            )
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
            raise PermissionError("Source is outside the work order data sources")
        if not source.is_file():
            raise FileNotFoundError(f"Source asset not found: {source}")
        return source

    def submit_dataset_run(
        self,
        *,
        work_order_id: str,
        owner_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        record = self._get_authorized(work_order_id, owner_id)
        if self.run_store is None or self.version_store is None:
            raise RuntimeError("Persistent runtime is required for dataset runs")
        snapshot = self.graph.get_state(self._config(record))
        if snapshot.interrupts:
            raise ValueError("Agent workflow still requires approval")
        state = dict(snapshot.values)
        if state.get("terminated"):
            raise ValueError("Terminated work orders cannot submit dataset runs")
        pipeline_id = state.get("selected_pipeline_id")
        spec_payload = state.get("task_spec")
        if not pipeline_id or not spec_payload or state.get("next_action") != "submit_dataset_run":
            raise ValueError("Work order is not ready to submit a dataset run")
        pipeline = PipelineVersion.model_validate(
            self.version_store.get(
                kind="pipeline", entity_id=pipeline_id, owner_id=owner_id
            )
        )
        if not pipeline.approved:
            raise ValueError("Selected PipelineVersion is not approved")
        self._validate_production_pipeline(pipeline)
        spec = TaskSpecVersion.model_validate(spec_payload)
        run = self.run_store.create(
            run_id=new_id("run"),
            work_order_id=work_order_id,
            owner_id=owner_id,
            pipeline_version_id=pipeline.id,
            task_spec_version_id=spec.id,
            idempotency_key=idempotency_key,
        )
        return self._run_payload(run)

    def _validate_production_pipeline(self, pipeline: PipelineVersion) -> None:
        self.operator_library.runtime.validate_pipeline(pipeline)
        released = {
            OperatorStatus.PERSONAL_RELEASE,
            OperatorStatus.PUBLIC_RELEASE,
        }
        violations: list[str] = []
        for node in pipeline.nodes:
            spec = self.operator_registry.get(node.operator_version_id)
            candidate_allowed = self._candidate_operator_is_executable(
                spec=spec,
                runtime_backend=node.runtime_backend,
                parameters=node.parameters,
            )
            if spec.status not in released and not candidate_allowed:
                violations.append(f"{spec.id} has status {spec.status}")
            if node.runtime_backend == RuntimeBackend.MOCK:
                violations.append(f"{spec.id} uses the mock runtime")
        if violations:
            raise ValueError(
                "Pipeline is not eligible for production execution: "
                + "; ".join(violations)
            )

    def _candidate_operator_is_executable(
        self,
        *,
        spec: OperatorSpecVersion,
        runtime_backend: RuntimeBackend,
        parameters: dict[str, Any],
    ) -> bool:
        if not self.allow_datajuicer_candidate_execution:
            return False
        if spec.status != OperatorStatus.DRAFT:
            return False
        if spec.provider.provider_id != "datajuicer":
            return False
        if runtime_backend == RuntimeBackend.CPU:
            if not {"cpu", "image"}.issubset(spec.capability_tags):
                return False
            if any(tag in spec.capability_tags for tag in {"gpu", "llm", "model"}):
                return False
        elif runtime_backend == RuntimeBackend.REMOTE:
            if not {"remote", "api", "image"}.issubset(spec.capability_tags):
                return False
        else:
            return False
        try:
            provider = self.operator_library.providers.get("datajuicer")
        except KeyError:
            return False
        if provider.provider_version != spec.provider.provider_version:
            return False
        validation = provider.validate(
            spec.provider.provider_operator_ref,
            parameters,
            runtime_backend,
        )
        return validation.ok

    def get_run(self, *, run_id: str, owner_id: str) -> dict[str, Any]:
        if self.run_store is None:
            raise RuntimeError("Persistent runtime is required for dataset runs")
        return self._run_payload(self.run_store.get(run_id, owner_id))

    def get_run_events(self, *, run_id: str, owner_id: str) -> list[dict[str, Any]]:
        if self.run_store is None:
            raise RuntimeError("Persistent runtime is required for dataset runs")
        return self.run_store.events(run_id, owner_id)

    def list_runs(self, *, work_order_id: str, owner_id: str) -> list[dict[str, Any]]:
        self._get_authorized(work_order_id, owner_id)
        if self.run_store is None:
            raise RuntimeError("Persistent runtime is required for dataset runs")
        return [
            self._run_payload(item)
            for item in self.run_store.list_for_work_order(work_order_id, owner_id)
        ]

    def control_run(self, *, run_id: str, owner_id: str, action: str) -> dict[str, Any]:
        if self.run_store is None:
            raise RuntimeError("Persistent runtime is required for dataset runs")
        handlers = {
            "pause": self.run_store.request_pause,
            "resume": self.run_store.resume,
            "cancel": self.run_store.request_cancel,
        }
        try:
            handler = handlers[action]
        except KeyError as exc:
            raise ValueError(f"Unsupported run action: {action}") from exc
        return self._run_payload(handler(run_id, owner_id))

    def get_dataset(self, *, dataset_version_id: str, owner_id: str) -> dict[str, Any]:
        if self.version_store is None:
            raise RuntimeError("Persistent runtime is required for datasets")
        payload = self.version_store.get(
            kind="dataset", entity_id=dataset_version_id, owner_id=owner_id
        )
        return DatasetVersion.model_validate(payload).model_dump(mode="json")

    def get_qc_report(self, *, qc_report_id: str, owner_id: str) -> dict[str, Any]:
        if self.version_store is None:
            raise RuntimeError("Persistent runtime is required for quality reports")
        payload = self.version_store.get(
            kind="qc_report", entity_id=qc_report_id, owner_id=owner_id
        )
        return QCReport.model_validate(payload).model_dump(mode="json")

    def _run_payload(self, run: dict[str, Any]) -> dict[str, Any]:
        if self.version_store is not None:
            reports = [
                item
                for item in self.version_store.list_for_owner(
                    kind="qc_report", owner_id=run["owner_id"]
                )
                if item.get("run_id") == run["id"]
            ]
            if reports:
                run = {**run, "qc_report_id": reports[-1]["id"]}
        return RunSnapshot.model_validate(run).model_dump(mode="json")

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
        approved_pipeline = result.get("approved_pipeline")
        if approved_pipeline:
            self.version_store.save_if_absent(
                kind="pipeline", owner_id=record.owner_id, payload=approved_pipeline
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
