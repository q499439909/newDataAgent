import pytest

from dataagent.agents.processing.nodes import select_representative_pipelines
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.domain.operators import OperatorCategory
from dataagent.domain.pipelines import PipelineNode, PipelineStrategy, PipelineVersion


def test_pipeline_missing_required_constraint_coverage_is_not_production_eligible() -> None:
    pipeline = PipelineVersion(
        id="pipeline_incomplete_yifu",
        family_id="pipeline_yifu",
        version=1,
        created_by="user_1",
        change_reason="golden failure",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id="spec_yifu",
        nodes=(
            PipelineNode(
                id="decode",
                operator_version_id="builtin.decode_check:1",
                name="Decode",
                category=OperatorCategory.FILTERING,
                required=True,
            ),
        ),
        created_from="processing_agent",
        required_constraint_ids=("C01", "C02", "C07"),
        constraint_coverage=(
            {
                "constraint_id": "C01",
                "node_id": "decode",
                "operator_version_id": "builtin.decode_check:1",
                "evidence_type": "image_width_px",
            },
            {
                "constraint_id": "C02",
                "node_id": "decode",
                "operator_version_id": "builtin.decode_check:1",
                "evidence_type": "image_height_px",
            },
        ),
    )

    eligibility = AgentRuntime(include_datajuicer=False).pipeline_execution_eligibility(
        pipeline
    )

    assert eligibility["eligible"] is False
    assert eligibility["violations"] == [
        "Pipeline is missing required constraint coverage: C07"
    ]


def test_processing_does_not_offer_incomplete_pipelines_for_user_approval() -> None:
    incomplete = PipelineVersion(
        id="pipeline_incomplete",
        family_id="pipeline_incomplete",
        version=1,
        created_by="user_1",
        change_reason="incomplete compilation",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id="spec_1",
        nodes=(
            PipelineNode(
                id="decode",
                operator_version_id="builtin.decode_check:1",
                name="Decode",
                category=OperatorCategory.FILTERING,
                required=True,
            ),
        ),
        created_from="processing_agent",
        required_constraint_ids=("constraint_width",),
    )
    variants = [
        incomplete.model_copy(
            update={
                "id": f"pipeline_{strategy.value}",
                "strategy": strategy,
            }
        ).model_dump(mode="json")
        for strategy in PipelineStrategy
    ]

    with pytest.raises(
        ValueError,
        match="cannot be offered for approval.*constraint_width",
    ):
        select_representative_pipelines(
            {"pipeline_variants": variants, "trace": []}
        )
