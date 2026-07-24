from __future__ import annotations

import hashlib
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

from dataagent.domain.operators import RuntimeBackend, RuntimeProfile
from dataagent.domain.pipelines import PipelineNode, PipelineStrategy, PipelineVersion
from dataagent.domain.specs import DataSourceSpec, TaskSpecVersion
from dataagent.evaluation import QualityEvaluator
from dataagent.execution import DatasetRunExecutor
from dataagent.infrastructure import DomainVersionStore, RunStore, SqliteDatabase
from dataagent.operators import OperatorRuntime, build_operator_library
from dataagent.operators.builtin.vlm import NativeRemoteVlmOperator
from dataagent.operators.protocol import OperatorContext, OperatorInput, OperatorResult


class BarrierOperator:
    parallel_safe = True

    def __init__(self, parties: int) -> None:
        base_spec = build_operator_library(
            include_datajuicer=False
        ).registry.get("builtin.manifest:1")
        self.spec = base_spec.model_copy(
            update={
                "id": "test.parallel_barrier:1",
                "family_id": "test.parallel_barrier",
                "display_name": "Parallel barrier",
                "implementation_ref": (
                    "tests.integration.test_parallel_dataset_execution:"
                    "BarrierOperator"
                ),
            }
        )
        self.barrier = threading.Barrier(parties)
        self.thread_ids: set[int] = set()
        self._lock = threading.Lock()

    def execute(
        self,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict,
    ) -> OperatorResult:
        with self._lock:
            self.thread_ids.add(threading.get_ident())
        self.barrier.wait(timeout=1.0)
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=input_data.metrics,
            labels=input_data.labels,
            artifacts=input_data.artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
        )


class TrackingOperator:
    def __init__(
        self,
        *,
        operator_id: str,
        parallel_safe: bool,
        fail_name: str | None = None,
        release: threading.Event | None = None,
        started: threading.Event | None = None,
        expected_started: int = 1,
        remote_concurrency: int | None = None,
    ) -> None:
        base_spec = build_operator_library(
            include_datajuicer=False
        ).registry.get("builtin.manifest:1")
        spec_updates: dict[str, Any] = {
                "id": operator_id,
                "family_id": operator_id.removesuffix(":1"),
                "display_name": "Tracking operator",
                "implementation_ref": (
                    "tests.integration.test_parallel_dataset_execution:"
                    "TrackingOperator"
                ),
        }
        if remote_concurrency is not None:
            spec_updates["supported_runtime_profiles"] = (
                RuntimeProfile(
                    backend=RuntimeBackend.REMOTE,
                    concurrency=remote_concurrency,
                ),
            )
        self.spec = base_spec.model_copy(update=spec_updates)
        self.parallel_safe = parallel_safe
        self.fail_name = fail_name
        self.release = release
        self.started = started
        self.expected_started = expected_started
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def execute(
        self,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict,
    ) -> OperatorResult:
        name = Path(input_data.current_path).name
        with self._lock:
            self.calls.append(name)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.started is not None and len(self.calls) >= self.expected_started:
                self.started.set()
        try:
            if self.release is not None:
                assert self.release.wait(timeout=2.0)
            else:
                time.sleep(0.03)
            if name == self.fail_name:
                raise RuntimeError("intentional asset failure")
            return OperatorResult(
                output_path=input_data.current_path,
                metrics=input_data.metrics,
                labels=input_data.labels,
                artifacts=input_data.artifacts,
                annotations=input_data.annotations,
                embeddings=input_data.embeddings,
            )
        finally:
            with self._lock:
                self.active -= 1


class ConcurrentVlmGateway:
    def __init__(self, parties: int) -> None:
        self.barrier = threading.Barrier(parties)
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["prompt"] == "Select the governed visual tag."
        assert kwargs["image_bytes"]
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            request_id = f"request_{self.calls}"
        try:
            self.barrier.wait(timeout=2.0)
            time.sleep(0.03)
            return {
                "tags": ["match"],
                "confidence": 0.9,
                "reason": "governed test result",
                "request_id": request_id,
                "model": "vision-test",
            }
        finally:
            with self._lock:
                self.active -= 1


def _prepared_executor(
    tmp_path: Path,
    operator: Any,
    *,
    worker_concurrency: int,
    image_count: int = 2,
    parameters: dict[str, Any] | None = None,
    runtime_backend: RuntimeBackend = RuntimeBackend.CPU,
) -> tuple[DatasetRunExecutor, RunStore]:
    source = tmp_path / "source"
    source.mkdir()
    names = ("first.png", "second.png", "third.png", "fourth.png")
    colors = ("black", "white", "red", "blue")
    for name, color in zip(names[:image_count], colors[:image_count], strict=True):
        Image.new("RGB", (32, 32), color).save(source / name)

    home = tmp_path / "runtime"
    database = SqliteDatabase(home / "control.db")
    run_store = RunStore(database)
    version_store = DomainVersionStore(database)
    task_spec = TaskSpecVersion(
        id="spec_parallel",
        version=1,
        created_by="user_1",
        change_reason="parallel execution test",
        work_order_id="work_order_parallel",
        objective="Process both images.",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(source)),),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id="pipeline_parallel",
        family_id="pipeline_parallel",
        version=1,
        created_by="user_1",
        change_reason="parallel execution test",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=task_spec.id,
        nodes=(
            PipelineNode(
                id="parallel_barrier",
                operator_version_id=operator.spec.id,
                name="Parallel barrier",
                category=operator.spec.primary_category.value,
                parameters=parameters or {},
                runtime_backend=runtime_backend,
            ),
        ),
        created_from="test",
        approved=True,
    )
    version_store.save_if_absent(
        kind="task_spec",
        owner_id="user_1",
        payload=task_spec.model_dump(mode="json"),
    )
    version_store.save_if_absent(
        kind="pipeline",
        owner_id="user_1",
        payload=pipeline.model_dump(mode="json"),
    )
    run_store.create(
        run_id="run_parallel",
        work_order_id=task_spec.work_order_id,
        owner_id="user_1",
        pipeline_version_id=pipeline.id,
        task_spec_version_id=task_spec.id,
        idempotency_key="parallel-execution",
    )
    assert run_store.claim_next()["id"] == "run_parallel"
    executor = DatasetRunExecutor(
        home=home,
        run_store=run_store,
        version_store=version_store,
        operator_runtime=OperatorRuntime((operator,)),
        quality_evaluator=QualityEvaluator(version_store),
        worker_concurrency=worker_concurrency,
    )
    return executor, run_store


def test_parallel_safe_assets_execute_concurrently_and_publish_dataset(
    tmp_path: Path,
) -> None:
    operator = BarrierOperator(parties=2)
    executor, run_store = _prepared_executor(
        tmp_path,
        operator,
        worker_concurrency=2,
    )

    completed = executor.execute("run_parallel")

    assert completed["status"] == "SUCCEEDED"
    assert completed["progress"] == 2
    assert completed["kept"] == 2
    assert completed["failed"] == 0
    assert len(operator.thread_ids) == 2
    assert [item["sequence"] for item in run_store.items("run_parallel")] == [0, 1]


def test_parallel_resume_uses_sequence_checkpoint_instead_of_list_position(
    tmp_path: Path,
) -> None:
    operator = BarrierOperator(parties=1)
    executor, run_store = _prepared_executor(
        tmp_path,
        operator,
        worker_concurrency=2,
    )
    sources = sorted((tmp_path / "source").glob("*.png"))
    plan = [
        {
            "sequence": sequence,
            "source_uri": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "output_relative_path": source.name,
        }
        for sequence, source in enumerate(sources)
    ]
    run_store.initialize_plan("run_parallel", plan)
    checkpoint_source = sources[1]
    checkpoint_output = (
        tmp_path / "runtime" / "runs" / "run_parallel" / "files" / checkpoint_source.name
    )
    checkpoint_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(checkpoint_source, checkpoint_output)
    checkpoint_sha = hashlib.sha256(checkpoint_output.read_bytes()).hexdigest()
    run_store.add_item(
        "run_parallel",
        {
            "sequence": 1,
            "source_uri": str(checkpoint_source),
            "source_sha256": checkpoint_sha,
            "output_relative_path": checkpoint_source.name,
            "output_sha256": checkpoint_sha,
            "decision": "keep",
            "reason_codes": [],
            "metrics": {},
            "labels": {},
        },
    )

    completed = executor.execute("run_parallel")

    assert completed["status"] == "SUCCEEDED"
    assert completed["progress"] == 2
    assert [item["sequence"] for item in run_store.items("run_parallel")] == [0, 1]


def test_operator_without_parallel_safety_falls_back_to_serial_execution(
    tmp_path: Path,
) -> None:
    operator = TrackingOperator(
        operator_id="test.serial_only:1",
        parallel_safe=False,
    )
    executor, run_store = _prepared_executor(
        tmp_path,
        operator,
        worker_concurrency=4,
        image_count=4,
    )

    completed = executor.execute("run_parallel")

    assert completed["status"] == "SUCCEEDED"
    assert completed["progress"] == 4
    assert operator.max_active == 1
    fallback_events = [
        event
        for event in run_store.events("run_parallel")
        if event["event_type"] == "asset_parallelism_disabled"
    ]
    assert fallback_events[0]["details"]["reason"] == "operator_not_parallel_safe"
    assert fallback_events[0]["details"]["operator_version_id"] == operator.spec.id


def test_parallel_asset_failure_does_not_abort_successful_assets(
    tmp_path: Path,
) -> None:
    operator = TrackingOperator(
        operator_id="test.selective_failure:1",
        parallel_safe=True,
        fail_name="second.png",
    )
    executor, run_store = _prepared_executor(
        tmp_path,
        operator,
        worker_concurrency=2,
    )

    completed = executor.execute("run_parallel")

    assert completed["status"] == "PARTIAL"
    assert completed["progress"] == 2
    assert completed["kept"] == 1
    assert completed["failed"] == 1
    items = run_store.items("run_parallel")
    assert [item["decision"] for item in items] == ["keep", "failed"]
    failed_result = next(
        result
        for result in run_store.node_results("run_parallel")
        if result["source_uri"].endswith("second.png")
    )
    assert failed_result["status"] == "failed"
    assert failed_result["error"] == "intentional asset failure"


def test_parallel_cancel_stops_dispatching_new_assets(tmp_path: Path) -> None:
    release = threading.Event()
    started = threading.Event()
    operator = TrackingOperator(
        operator_id="test.cancellable_parallel:1",
        parallel_safe=True,
        release=release,
        started=started,
        expected_started=2,
    )
    executor, run_store = _prepared_executor(
        tmp_path,
        operator,
        worker_concurrency=2,
        image_count=4,
    )
    result: dict[str, Any] = {}

    thread = threading.Thread(
        target=lambda: result.update(executor.execute("run_parallel")),
    )
    thread.start()
    assert started.wait(timeout=2.0)
    run_store.request_cancel("run_parallel", "user_1")
    release.set()
    thread.join(timeout=3.0)

    assert not thread.is_alive()
    assert result["status"] == "CANCELLED"
    assert len(operator.calls) == 2
    assert run_store.get("run_parallel")["progress"] == 2


def test_remote_operator_profile_caps_worker_concurrency(tmp_path: Path) -> None:
    operator = TrackingOperator(
        operator_id="test.remote_limit:1",
        parallel_safe=True,
        remote_concurrency=2,
    )
    executor, run_store = _prepared_executor(
        tmp_path,
        operator,
        worker_concurrency=8,
        image_count=4,
        runtime_backend=RuntimeBackend.REMOTE,
    )

    completed = executor.execute("run_parallel")

    assert completed["status"] == "SUCCEEDED"
    assert operator.max_active == 2
    selected = next(
        event
        for event in run_store.events("run_parallel")
        if event["event_type"] == "asset_parallelism_selected"
    )
    assert selected["details"]["requested_concurrency"] == 8
    assert selected["details"]["effective_concurrency"] == 2


def test_native_remote_vlm_runs_full_asset_contract_concurrently(
    tmp_path: Path,
) -> None:
    gateway = ConcurrentVlmGateway(parties=4)
    operator = NativeRemoteVlmOperator(gateway)
    executor, run_store = _prepared_executor(
        tmp_path,
        operator,
        worker_concurrency=4,
        image_count=4,
        runtime_backend=RuntimeBackend.REMOTE,
        parameters={
            "system_prompt": "Select the governed visual tag.",
            "tag_field_name": "visual_tags",
            "allowed_tags": ["match", "mismatch"],
            "required_tag_groups": [["match", "mismatch"]],
            "model": "vision-test",
        },
    )

    completed = executor.execute("run_parallel")

    assert completed["status"] == "SUCCEEDED"
    assert completed["progress"] == 4
    assert gateway.calls == 4
    assert gateway.max_active == 4
    evidence_files = list(
        (
            tmp_path
            / "runtime"
            / "datasets"
            / "dataset_parallel"
            / "artifacts"
            / "native-vlm"
            / "parallel_barrier"
        ).glob("*.json")
    )
    assert len(evidence_files) == 4
