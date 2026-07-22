from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dataagent.application.pipeline_artifacts import (
    export_pipeline_yaml,
    import_pipeline_yaml,
    parse_pipeline_yaml,
    pipeline_sha256,
    serialize_pipeline_yaml,
)
from dataagent.domain.pipelines import (
    PipelineNode,
    PipelineStrategy,
    PipelineVersion,
)


def _pipeline() -> PipelineVersion:
    return PipelineVersion(
        id="pipeline_version_1",
        family_id="pipeline_balanced",
        version=1,
        created_by="user_1",
        change_reason="test artifact",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id="spec_1",
        nodes=(
            PipelineNode(
                id="decode",
                operator_version_id="builtin.decode_check:1",
                name="Decode",
                category="ingestion",
                parameters={},
            ),
        ),
        created_from="processing_agent",
        approved=True,
    )


def test_pipeline_yaml_round_trip_uses_existing_domain_model(tmp_path: Path) -> None:
    pipeline = _pipeline()
    destination = tmp_path / "pipeline.yaml"

    exported = export_pipeline_yaml(pipeline, destination)
    restored = import_pipeline_yaml(exported)

    assert restored == pipeline
    payload = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "dataagent.pipeline/v1"
    assert payload["canonical_sha256"] == pipeline_sha256(pipeline)


def test_pipeline_yaml_rejects_tampering() -> None:
    payload = yaml.safe_load(serialize_pipeline_yaml(_pipeline()))
    payload["pipeline"]["strategy"] = "quality_first"

    with pytest.raises(ValueError, match="checksum mismatch"):
        parse_pipeline_yaml(yaml.safe_dump(payload))


def test_pipeline_export_does_not_overwrite_by_default(tmp_path: Path) -> None:
    destination = tmp_path / "pipeline.yaml"
    export_pipeline_yaml(_pipeline(), destination)

    with pytest.raises(FileExistsError, match="already exists"):
        export_pipeline_yaml(_pipeline(), destination)
