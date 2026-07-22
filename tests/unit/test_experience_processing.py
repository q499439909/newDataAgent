from __future__ import annotations

from dataagent.agents.processing.nodes import generate_pipeline_variants
from dataagent.domain.specs import DataSourceSpec, TaskSpecVersion
from dataagent.experiences import PipelineExperienceMatch
from dataagent.operators import build_operator_library


class _ExperienceRetriever:
    def search(self, *args, **kwargs):
        return (
            PipelineExperienceMatch(
                experience_id="experience_1",
                pipeline_version_id="historical_pipeline_1",
                strategy="balanced",
                score=92,
                structural_score=1,
                semantic_score=0.8,
                quality_score=1,
                reasons=("test",),
            ),
        )


def test_processing_recompiles_new_versions_with_experience_provenance() -> None:
    spec = TaskSpecVersion(
        id="spec_1",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_1",
        objective="Filter image data",
        data_sources=(DataSourceSpec(type="local_directory", uri="D:/images"),),
    )

    result = generate_pipeline_variants(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_library=build_operator_library(include_datajuicer=False),
        experience_retriever=_ExperienceRetriever(),
    )

    assert result["pipeline_experience_matches"][0]["experience_id"] == "experience_1"
    assert all(
        pipeline["template_experience_id"] == "experience_1"
        for pipeline in result["pipeline_variants"]
    )
    assert all(
        pipeline["id"] != "historical_pipeline_1"
        for pipeline in result["pipeline_variants"]
    )
