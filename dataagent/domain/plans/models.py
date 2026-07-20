from __future__ import annotations

from typing import Any

from pydantic import Field

from ..common.models import VersionedModel


class RetrievalPlanVersion(VersionedModel):
    task_spec_version_id: str
    routes: tuple[dict[str, Any], ...]
    target_candidate_count: int = Field(ge=1)
    estimated_cost: float = Field(default=0, ge=0)
    sufficient: bool = False
    operator_candidates: tuple[dict[str, Any], ...] = ()


class CurationPlanVersion(VersionedModel):
    task_spec_version_id: str
    pipeline_version_ids: tuple[str, ...]
    pre_sample_operator_ids: tuple[str, ...] = ()
    post_sample_operator_ids: tuple[str, ...] = ()


class SamplingPlanVersion(VersionedModel):
    task_spec_version_id: str
    quota: dict[str, int] = Field(default_factory=dict)
    priority_weights: dict[str, float] = Field(default_factory=dict)
    diversity_constraints: dict[str, Any] = Field(default_factory=dict)
    random_seed: int = 42
