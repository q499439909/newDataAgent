from __future__ import annotations

import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ..domain.common import new_id
from ..domain.evaluations import QCReport
from ..domain.operators import OperatorSpecVersion, OperatorStatus, RuntimeBackend
from ..domain.pipelines import PipelineStrategy, PipelineVersion
from ..domain.runs import DatasetVersion, RunSnapshot
from ..domain.specs import TaskSpecVersion
from ..execution import NodePreviewBuilder
from ..graph import build_main_graph
from ..graph.interrupts import revise_task_spec_version
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
from ..operators.validation import ParameterValidationError, validate_parameters


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
        remote_asset_timeout_seconds: int = 90,
        allow_datajuicer_candidate_execution: bool = True,
        remote_operator_available: bool = False,
        vision_model: str = "qwen3.7-plus",
        vision_api_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
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
            remote_asset_timeout_seconds=remote_asset_timeout_seconds,
            vision_model=vision_model,
            vision_api_base_url=vision_api_base_url,
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
        snapshot = self.graph.get_state(self._config(record))
        if (
            decision.get("approved")
            and snapshot.interrupts
            and getattr(snapshot.interrupts[0], "value", {}).get("kind")
            == "pipeline_approval"
        ):
            representatives = [
                PipelineVersion.model_validate(item)
                for item in snapshot.values.get("representative_pipelines", [])
            ]
            selected_id = decision.get("pipeline_id")
            selected = next(
                (
                    item
                    for item in representatives
                    if item.id == selected_id
                    or (
                        selected_id is None
                        and item.strategy.value == "balanced"
                    )
                ),
                None,
            )
            if selected is None:
                raise ValueError("Selected pipeline is not available for approval")
            eligibility = self.pipeline_execution_eligibility(selected)
            if not eligibility["eligible"]:
                raise ValueError(
                    "Pipeline cannot be approved for execution: "
                    + "; ".join(eligibility["violations"])
                )
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

    def pipeline_version(
        self, *, pipeline_version_id: str, owner_id: str
    ) -> dict[str, Any]:
        if self.version_store is None:
            raise RuntimeError("Persistent runtime is required for Pipeline lookup")
        return self.version_store.get(
            kind="pipeline",
            entity_id=pipeline_version_id,
            owner_id=owner_id,
        )

    def pipeline_version_eligibility(
        self, *, pipeline_version_id: str, owner_id: str
    ) -> dict[str, Any]:
        pipeline = PipelineVersion.model_validate(
            self.pipeline_version(
                pipeline_version_id=pipeline_version_id,
                owner_id=owner_id,
            )
        )
        return self.pipeline_execution_eligibility(pipeline)

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

    def retry_failed_assets(
        self,
        *,
        previous_run_id: str,
        owner_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if self.run_store is None:
            raise RuntimeError("Persistent runtime is required for dataset runs")
        previous = self.run_store.get(previous_run_id, owner_id)
        failed_sequences = {
            int(item["sequence"])
            for item in self.run_store.items(previous_run_id)
            if item["decision"] == "failed"
        }
        if not failed_sequences:
            raise ValueError("The selected Run has no failed assets to retry")
        new_run = self.run_store.create(
            run_id=new_id("run"),
            work_order_id=previous["work_order_id"],
            owner_id=owner_id,
            pipeline_version_id=previous["pipeline_version_id"],
            task_spec_version_id=previous["task_spec_version_id"],
            idempotency_key=idempotency_key,
        )
        previous_plan = self.run_store.plan(previous_run_id)
        retry_plan = [
            {**item, "sequence": retry_sequence}
            for retry_sequence, item in enumerate(
                item
                for item in previous_plan
                if int(item["sequence"]) in failed_sequences
            )
        ]
        self.run_store.initialize_plan(new_run["id"], retry_plan)
        self.run_store.add_event(
            new_run["id"],
            "failed_assets_retry_scheduled",
            {
                "previous_run_id": previous_run_id,
                "asset_count": len(retry_plan),
                "source_uris": [item["source_uri"] for item in retry_plan],
            },
        )
        return self._run_payload(self.run_store.get(new_run["id"], owner_id))

    def reselect_pipeline(
        self,
        *,
        work_order_id: str,
        owner_id: str,
        strategy: str,
    ) -> dict[str, Any]:
        record = self._get_authorized(work_order_id, owner_id)
        if self.version_store is None or self.run_store is None:
            raise RuntimeError("Persistent runtime is required for pipeline reselection")
        active = [
            item
            for item in self.run_store.list_for_work_order(work_order_id, owner_id)
            if item["status"]
            in {"QUEUED", "RUNNING", "PAUSING", "CANCELLING", "EVALUATING"}
        ]
        if active:
            raise ValueError("Cannot change Pipeline while a Run is active")
        snapshot = self.graph.get_state(self._config(record))
        state = dict(snapshot.values)
        try:
            requested = PipelineStrategy(strategy)
        except ValueError as exc:
            raise ValueError(f"Unknown pipeline strategy: {strategy}") from exc
        representatives = [
            PipelineVersion.model_validate(item)
            for item in state.get("representative_pipelines", [])
        ]
        selected = next(
            (item for item in representatives if item.strategy == requested),
            None,
        )
        if selected is None:
            raise ValueError(f"Pipeline strategy is not available: {strategy}")
        eligibility = self.pipeline_execution_eligibility(selected)
        if not eligibility["eligible"]:
            raise ValueError(
                "Pipeline cannot be selected for execution: "
                + "; ".join(eligibility["violations"])
            )
        family_versions = [
            PipelineVersion.model_validate(item)
            for item in self.version_store.list_for_owner(
                kind="pipeline", owner_id=owner_id
            )
            if item.get("family_id") == selected.family_id
        ]
        approved_pipeline = selected.model_copy(
            update={
                "id": new_id("pipeline_version"),
                "version": max((item.version for item in family_versions), default=0) + 1,
                "parent_version_id": selected.id,
                "created_by": owner_id,
                "change_reason": "Pipeline reselected by user",
                "approved": True,
                "run_id": None,
                "metrics": {},
            }
        )
        payload = approved_pipeline.model_dump(mode="json")
        self.version_store.save_if_absent(
            kind="pipeline", owner_id=owner_id, payload=payload
        )
        self.graph.update_state(
            self._config(record),
            {
                "approved_pipeline": payload,
                "selected_pipeline_id": approved_pipeline.id,
                "pipeline_approval": {
                    "approved": True,
                    "pipeline_id": approved_pipeline.id,
                    "strategy": requested.value,
                    "channel": "conversation_reselection",
                },
                "next_action": "submit_dataset_run",
                "terminated": False,
                "trace": [
                    *state.get("trace", []),
                    "hitl:pipeline_reselected",
                ],
            },
            as_node="strategy_agent",
        )
        return self.state(work_order_id=work_order_id, owner_id=owner_id)

    def recompile_pipeline_candidates(
        self,
        *,
        work_order_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        record = self._get_authorized(work_order_id, owner_id)
        if self.run_store is None or self.version_store is None:
            raise RuntimeError("Persistent runtime is required for Pipeline recompilation")
        active = [
            item
            for item in self.run_store.list_for_work_order(work_order_id, owner_id)
            if item["status"]
            in {"QUEUED", "RUNNING", "PAUSING", "CANCELLING", "EVALUATING"}
        ]
        if active:
            raise ValueError("Cannot recompile Pipeline while a Run is active")
        snapshot = self.graph.get_state(self._config(record))
        if snapshot.interrupts:
            raise ValueError("Resolve the current workflow approval before recompiling")
        state = dict(snapshot.values)
        spec = TaskSpecVersion.model_validate(state.get("task_spec"))
        if not spec.confirmed:
            raise ValueError("TaskSpec must be confirmed before recompiling Pipeline")
        self.graph.update_state(
            self._config(record),
            {
                "retrieval_plan": {},
                "candidate_sufficient": False,
                "operator_candidates": [],
                "capability_coverage": [],
                "capability_resolution": {},
                "capability_resolution_attempt": 0,
                "runtime_backend_overrides": [],
                "pipeline_variants": [],
                "representative_pipelines": [],
                "approved_pipeline": {},
                "selected_pipeline_id": "",
                "pipeline_approval": {},
                "sampling_plan": {},
                "current_agent": "retrieval",
                "next_action": "run_retrieval_agent",
                "terminated": False,
                "trace": [
                    *state.get("trace", []),
                    "hitl:pipeline_recompile_requested",
                ],
            },
            as_node="confirm_task_spec",
        )
        result = self.graph.invoke(None, self._config(record))
        self._capture_versions(record, result)
        return self._public_result(record, result)

    def revise_task_spec(
        self,
        *,
        work_order_id: str,
        owner_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        record = self._get_authorized(work_order_id, owner_id)
        if self.version_store is None or self.run_store is None:
            raise RuntimeError("Persistent runtime is required for TaskSpec revision")
        active = [
            item
            for item in self.run_store.list_for_work_order(work_order_id, owner_id)
            if item["status"]
            in {"QUEUED", "RUNNING", "PAUSING", "CANCELLING", "EVALUATING"}
        ]
        if active:
            raise ValueError("Cannot revise TaskSpec while a Run is active")
        snapshot = self.graph.get_state(self._config(record))
        state = dict(snapshot.values)
        current = TaskSpecVersion.model_validate(state.get("task_spec"))
        revised = revise_task_spec_version(
            current,
            patch=patch,
            actor=owner_id,
        )
        revised_payload = revised.model_dump(mode="json")
        self.graph.update_state(
            self._config(record),
            {
                "requirement": revised.objective,
                "task_spec": revised_payload,
                "task_spec_confirmed": False,
                "task_spec_approval": {},
                "retrieval_plan": {},
                "candidate_sufficient": False,
                "operator_candidates": [],
                "capability_coverage": [],
                "capability_resolution": {},
                "capability_resolution_attempt": 0,
                "runtime_backend_overrides": [],
                "pipeline_variants": [],
                "representative_pipelines": [],
                "approved_pipeline": {},
                "selected_pipeline_id": "",
                "pipeline_approval": {},
                "sampling_plan": {},
                "current_agent": "requirement",
                "next_action": "confirm_task_spec",
                "terminated": False,
                "trace": [
                    *state.get("trace", []),
                    "hitl:confirmed_task_spec_reopened",
                ],
            },
            as_node="confirm_task_spec",
        )
        result = self.graph.invoke(None, self._config(record))
        self._capture_versions(record, result)
        return self._public_result(record, result)

    def _validate_production_pipeline(self, pipeline: PipelineVersion) -> None:
        eligibility = self.pipeline_execution_eligibility(pipeline)
        if not eligibility["eligible"]:
            raise ValueError(
                "Pipeline is not eligible for production execution: "
                + "; ".join(eligibility["violations"])
            )

    def pipeline_execution_eligibility(
        self, pipeline: PipelineVersion
    ) -> dict[str, Any]:
        released = {
            OperatorStatus.PERSONAL_RELEASE,
            OperatorStatus.PUBLIC_RELEASE,
        }
        violations: list[str] = []
        try:
            self.operator_library.runtime.validate_pipeline(pipeline)
        except (KeyError, ValueError) as exc:
            violations.append(str(exc))
        for node in pipeline.nodes:
            try:
                spec = self.operator_registry.get(node.operator_version_id)
            except KeyError as exc:
                violations.append(str(exc))
                continue
            try:
                normalized_parameters = validate_parameters(
                    spec.parameter_schema,
                    node.parameters,
                )
            except ParameterValidationError as exc:
                violations.append(f"{spec.id} has invalid parameters: {exc}")
                continue
            candidate_allowed, candidate_reason = self._candidate_operator_eligibility(
                spec=spec,
                runtime_backend=node.runtime_backend,
                parameters=normalized_parameters,
            )
            if spec.status not in released and not candidate_allowed:
                violations.append(
                    f"{spec.id} has status {spec.status}: {candidate_reason}"
                )
            if node.runtime_backend == RuntimeBackend.MOCK:
                violations.append(f"{spec.id} uses the mock runtime")
        unique_violations = list(dict.fromkeys(violations))
        return {
            "eligible": not unique_violations,
            "violations": unique_violations,
        }

    def _candidate_operator_eligibility(
        self,
        *,
        spec: OperatorSpecVersion,
        runtime_backend: RuntimeBackend,
        parameters: dict[str, Any],
    ) -> tuple[bool, str]:
        if not self.allow_datajuicer_candidate_execution:
            return False, "draft candidate execution is disabled"
        if spec.status != OperatorStatus.DRAFT:
            return False, "operator is not a draft candidate"
        if spec.provider.provider_id != "datajuicer":
            return False, "operator is not a governed Data-Juicer candidate"
        if runtime_backend == RuntimeBackend.CPU:
            if not {"cpu", "image"}.issubset(spec.capability_tags):
                return False, "CPU image capability tags are missing"
            if any(tag in spec.capability_tags for tag in {"gpu", "llm", "model"}):
                return False, "model-backed candidates require a governed non-CPU runtime"
        elif runtime_backend == RuntimeBackend.REMOTE:
            if not {"remote", "api", "image"}.issubset(spec.capability_tags):
                return False, "remote API image capability tags are missing"
        else:
            return False, f"runtime backend {runtime_backend} is not candidate-eligible"
        try:
            provider = self.operator_library.providers.get("datajuicer")
        except KeyError:
            return False, "Data-Juicer provider is unavailable"
        if provider.provider_version != spec.provider.provider_version:
            return False, "provider version does not match the operator version"
        validation = provider.validate(
            spec.provider.provider_operator_ref,
            parameters,
            runtime_backend,
        )
        if not validation.ok:
            return False, "provider validation failed: " + "; ".join(validation.errors)
        return True, "candidate policy and provider validation passed"

    def get_run(self, *, run_id: str, owner_id: str) -> dict[str, Any]:
        if self.run_store is None:
            raise RuntimeError("Persistent runtime is required for dataset runs")
        return self._run_payload(self.run_store.get(run_id, owner_id))

    def get_run_events(self, *, run_id: str, owner_id: str) -> list[dict[str, Any]]:
        if self.run_store is None:
            raise RuntimeError("Persistent runtime is required for dataset runs")
        return self.run_store.events(run_id, owner_id)

    def get_run_node_results(
        self, *, run_id: str, owner_id: str
    ) -> list[dict[str, Any]]:
        if self.run_store is None:
            raise RuntimeError("Persistent runtime is required for dataset runs")
        return self.run_store.node_results(run_id, owner_id)

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

    def _public_result(self, record: AgentThread, result: dict[str, Any]) -> dict[str, Any]:
        state = {key: value for key, value in result.items() if key != "__interrupt__"}
        representatives = {
            item["id"]: item for item in state.get("representative_pipelines", [])
        }
        interrupts = []
        for item in result.get("__interrupt__", ()):
            value = deepcopy(getattr(item, "value", item))
            if isinstance(value, dict) and value.get("kind") == "pipeline_approval":
                for pipeline_payload in value.get("pipelines", []):
                    pipeline = PipelineVersion.model_validate(
                        representatives.get(pipeline_payload.get("id"), pipeline_payload)
                    )
                    pipeline_payload["execution_eligibility"] = (
                        self.pipeline_execution_eligibility(pipeline)
                    )
                    for node in pipeline_payload.get("nodes", []):
                        try:
                            spec = self.operator_registry.get(
                                node["operator_version_id"]
                            )
                        except KeyError:
                            node["operator_status"] = "UNAVAILABLE"
                        else:
                            node["operator_status"] = spec.status.value
            interrupts.append(
                {
                    "id": getattr(item, "id", None),
                    "value": value,
                }
            )
        return {
            "work_order_id": record.work_order_id,
            "thread_id": record.thread_id,
            "state": state,
            "interrupts": interrupts,
        }
