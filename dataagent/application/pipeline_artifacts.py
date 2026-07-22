from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from ..domain.pipelines import PipelineVersion


PIPELINE_SCHEMA_VERSION = "dataagent.pipeline/v1"


class PipelineArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["dataagent.pipeline/v1"] = PIPELINE_SCHEMA_VERSION
    canonical_sha256: str
    pipeline: PipelineVersion


def _canonical_payload(pipeline: PipelineVersion) -> bytes:
    payload = pipeline.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def pipeline_sha256(pipeline: PipelineVersion) -> str:
    return hashlib.sha256(_canonical_payload(pipeline)).hexdigest()


def serialize_pipeline_yaml(pipeline: PipelineVersion) -> str:
    artifact = PipelineArtifact(
        canonical_sha256=pipeline_sha256(pipeline),
        pipeline=pipeline,
    )
    return yaml.safe_dump(
        artifact.model_dump(mode="json"),
        allow_unicode=True,
        sort_keys=False,
    )


def parse_pipeline_yaml(content: str) -> PipelineVersion:
    raw: Any = yaml.safe_load(content)
    artifact = PipelineArtifact.model_validate(raw)
    actual = pipeline_sha256(artifact.pipeline)
    if actual != artifact.canonical_sha256:
        raise ValueError(
            "Pipeline artifact checksum mismatch: "
            f"expected {artifact.canonical_sha256}, got {actual}"
        )
    return artifact.pipeline


def export_pipeline_yaml(
    pipeline: PipelineVersion,
    destination: Path,
    *,
    overwrite: bool = False,
) -> Path:
    path = destination.expanduser().resolve()
    if path.exists() and not overwrite:
        raise FileExistsError(f"Pipeline artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(serialize_pipeline_yaml(pipeline), encoding="utf-8")
    temporary.replace(path)
    return path


def import_pipeline_yaml(source: Path) -> PipelineVersion:
    path = source.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Pipeline artifact not found: {path}")
    return parse_pipeline_yaml(path.read_text(encoding="utf-8"))


__all__ = [
    "PIPELINE_SCHEMA_VERSION",
    "PipelineArtifact",
    "export_pipeline_yaml",
    "import_pipeline_yaml",
    "parse_pipeline_yaml",
    "pipeline_sha256",
    "serialize_pipeline_yaml",
]
