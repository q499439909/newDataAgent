from __future__ import annotations

import pytest
from pydantic import ValidationError

from dataagent.domain.operators import OperatorCategory, OperatorSpecVersion, OperatorStatus
from dataagent.domain.pipelines import PipelineEdge, PipelineNode, PipelineStrategy, PipelineVersion
from dataagent.operators import OperatorRegistry


def make_operator(
    *,
    version_id: str = "operator_decode_v1",
    category: OperatorCategory = OperatorCategory.INGESTION,
    secondary: str = "decoding",
) -> OperatorSpecVersion:
    return OperatorSpecVersion(
        id=version_id,
        family_id="operator_decode",
        version=1,
        created_by="user_1",
        change_reason="initial",
        display_name="图片解码检查",
        summary="检查图片是否能被完整解码",
        description="读取图片并验证文件内容、格式和尺寸元数据。",
        primary_category=category,
        secondary_category=secondary,
        capability_tags=frozenset({"image", "decode"}),
        input_schema="ImageAssetRef",
        output_schema="EnrichedImageAsset",
        implementation_ref="dataagent.imaging:analyze_image",
        status=OperatorStatus.EVALUATED,
        owner_id="user_1",
    )


def test_versioned_domain_models_are_immutable() -> None:
    operator = make_operator()
    with pytest.raises(ValidationError):
        operator.display_name = "changed"  # type: ignore[misc]


def test_registry_filters_by_category_and_tags() -> None:
    ingestion = make_operator()
    filtering = make_operator(
        version_id="operator_filter_v1",
        category=OperatorCategory.FILTERING,
        secondary="image_quality",
    ).model_copy(update={"capability_tags": frozenset({"image", "quality"})})
    registry = OperatorRegistry([ingestion, filtering])

    assert registry.search(category=OperatorCategory.FILTERING) == [filtering]
    assert registry.search(tags={"decode"}) == [ingestion]
    assert registry.categories()[OperatorCategory.INGESTION] == 1


def test_registry_rejects_unknown_secondary_category() -> None:
    operator = make_operator().model_copy(update={"secondary_category": "not-real"})
    with pytest.raises(ValueError, match="Unknown secondary category"):
        OperatorRegistry([operator])


def test_pipeline_branch_creates_new_immutable_version() -> None:
    node = PipelineNode(
        id="decode",
        operator_version_id="operator_decode_v1",
        name="Decode",
        category="INGESTION",
    )
    pipeline = PipelineVersion(
        id="pipeline_v1",
        family_id="pipeline_family",
        version=1,
        created_by="agent",
        change_reason="initial",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id="spec_v1",
        nodes=(node,),
        created_from="processing_agent",
    )
    branch = pipeline.branch(new_id="pipeline_v2", actor="user", reason="adjust threshold")

    assert branch.id == "pipeline_v2"
    assert branch.parent_version_id == pipeline.id
    assert branch.version == 2
    assert pipeline.version == 1


def test_pipeline_rejects_cycles() -> None:
    nodes = tuple(
        PipelineNode(
            id=node_id,
            operator_version_id=f"operator_{node_id}",
            name=node_id,
            category="FILTERING",
        )
        for node_id in ("a", "b")
    )
    with pytest.raises(ValidationError, match="acyclic"):
        PipelineVersion(
            id="pipeline_cycle",
            family_id="pipeline_family",
            version=1,
            created_by="agent",
            change_reason="invalid cycle",
            strategy=PipelineStrategy.BALANCED,
            task_spec_version_id="spec_v1",
            nodes=nodes,
            edges=(PipelineEdge(source="a", target="b"), PipelineEdge(source="b", target="a")),
            created_from="processing_agent",
        )
