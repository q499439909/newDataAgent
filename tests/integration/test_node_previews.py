from __future__ import annotations

from pathlib import Path

from PIL import Image

from dataagent.agents.processing.nodes import generate_pipeline_variants
from dataagent.domain.common import new_id
from dataagent.domain.pipelines import PipelineVersion
from dataagent.domain.specs import DataSourceSpec, TaskSpecVersion
from dataagent.execution import NodePreviewBuilder
from dataagent.operators import OperatorRegistry, OperatorRuntime
from dataagent.operators.builtin import builtin_image_operators


def make_checkerboard(path: Path) -> None:
    image = Image.new("RGB", (640, 480), "white")
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            if (x // 8 + y // 8) % 2:
                pixels[x, y] = (0, 0, 0)
    image.save(path)


def test_builtin_operators_are_categorized_and_generate_real_previews(tmp_path) -> None:
    operators = builtin_image_operators()
    registry = OperatorRegistry(operator.spec for operator in operators)
    assert sum(registry.categories().values()) == 4

    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="filter clear images",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(tmp_path)),),
        confirmed=True,
    )
    state = {
        "owner_id": "user_1",
        "task_spec": spec.model_dump(mode="json"),
        "trace": [],
    }
    variants = generate_pipeline_variants(state)["pipeline_variants"]
    pipeline = PipelineVersion.model_validate(
        next(item for item in variants if item["strategy"] == "retention_first")
    )
    source = tmp_path / "checkerboard.png"
    make_checkerboard(source)

    builder = NodePreviewBuilder(
        OperatorRuntime(operators), preview_root=tmp_path / "previews"
    )
    preview_set = builder.build(
        pipeline=pipeline,
        source_path=source,
        owner_id="user_1",
        work_order_id="work_order_1",
        run_id="run_1",
    )

    assert preview_set.pipeline_version_id == pipeline.id
    assert [item.node_id for item in preview_set.items] == [
        "ingest",
        "filter",
        "deduplicate",
        "manifest",
    ]
    assert preview_set.items[0].metrics_after["width"] == 640
    assert preview_set.items[-1].decision == "keep"
    for item in preview_set.items:
        assert Path(item.input_preview_uri).is_file()
        assert Path(item.output_preview_uri or "").is_file()
