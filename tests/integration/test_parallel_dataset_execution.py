from __future__ import annotations

import hashlib
import shutil
import threading
from pathlib import Path

from PIL import Image

from dataagent.domain.pipelines import PipelineNode, PipelineStrategy, PipelineVersion
from dataagent.domain.specs import DataSourceSpec, TaskSpecVersion
from dataagent.evaluation import QualityEvaluator
from dataagent.execution import DatasetRunExecutor
from dataagent.infrastructure import DomainVersionStore, RunStore, SqliteDatabase
from dataagent.operators import OperatorRuntime, build_operator_library
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


def _prepared_executor(
    tmp_path: Path,
    operator: BarrierOperator,
    *,
    worker_concurrency: int,
) -> tuple[DatasetRunExecutor, RunStore]:
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (32, 32), "black").save(source / "first.png")
    Image.new("RGB", (32, 32), "white").save(source / "second.png")

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
                parameters={},
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
