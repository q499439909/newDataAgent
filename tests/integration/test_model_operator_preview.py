from __future__ import annotations

from PIL import Image

from dataagent.domain.operators import OperatorCategory, RuntimeBackend
from dataagent.domain.pipelines import (
    PipelineEdge,
    PipelineNode,
    PipelineStrategy,
    PipelineVersion,
)
from dataagent.execution import NodePreviewBuilder
from dataagent.operators import build_operator_library


def test_mock_model_outputs_survive_the_full_preview_pipeline(tmp_path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 240), (120, 140, 160)).save(source)
    nodes = (
        PipelineNode(
            id="ingest",
            operator_version_id="builtin.decode_check:1",
            name="Decode",
            category=OperatorCategory.INGESTION,
        ),
        PipelineNode(
            id="segment",
            operator_version_id="model.segmentation:1",
            name="Segment",
            category=OperatorCategory.UNDERSTANDING,
            runtime_backend=RuntimeBackend.MOCK,
        ),
        PipelineNode(
            id="filter",
            operator_version_id="builtin.quality_filter:1",
            name="Filter",
            category=OperatorCategory.FILTERING,
            parameters={"confidence_threshold": 0},
        ),
        PipelineNode(
            id="manifest",
            operator_version_id="builtin.manifest:1",
            name="Manifest",
            category=OperatorCategory.OUTPUT,
        ),
    )
    pipeline = PipelineVersion(
        id="pipeline_model_preview",
        family_id="pipeline_model_preview",
        version=1,
        created_by="user_1",
        change_reason="integration test",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id="spec_1",
        nodes=nodes,
        edges=tuple(
            PipelineEdge(source=left.id, target=right.id)
            for left, right in zip(nodes, nodes[1:])
        ),
        created_from="test",
    )
    library = build_operator_library(include_datajuicer=False)

    preview = NodePreviewBuilder(library.runtime, tmp_path / "previews").build(
        pipeline=pipeline,
        source_path=source,
        owner_id="user_1",
        work_order_id="work_order_1",
        run_id="preview_1",
    )

    assert len(preview.items) == 4
    assert preview.items[1].annotations[0].annotation_type == "mask"
    assert preview.items[-1].annotations == preview.items[1].annotations
    assert preview.items[-1].labels_after["model_output_mode"] == "mock"
