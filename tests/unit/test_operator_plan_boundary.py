from __future__ import annotations

import pytest

from dataagent.agents.retrieval.nodes import (
    generate_retrieval_plan,
    hydrate_operator_plan,
)
from dataagent.domain.specs import (
    ConstraintContract,
    DataSourceSpec,
    TaskSpecVersion,
)
from dataagent.operators import build_operator_library


def _retrieve(*, field: str, operator: str, value, evidence: str) -> dict:
    spec = TaskSpecVersion(
        id=f"spec_{field.replace('.', '_')}",
        version=1,
        created_by="user_1",
        change_reason="generic regression case",
        work_order_id="work_operator_plan",
        objective="Apply one confirmed observable constraint",
        data_sources=(
            DataSourceSpec(type="local_directory", uri="D:/generic"),
        ),
        constraints=(
            ConstraintContract(
                id="constraint_generic",
                source_text=f"{field} {evidence}",
                scope="asset",
                field=field,
                operator=operator,
                value=value,
                unit=(
                    "score"
                    if isinstance(value, float)
                    else "boolean"
                    if isinstance(value, bool)
                    else "value"
                ),
                required_evidence_type=evidence,
            ),
        ),
        confirmed=True,
    )
    return generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=build_operator_library(
            include_datajuicer=False
        ).registry,
    )


@pytest.mark.parametrize(
    ("field", "operator", "value", "evidence"),
    (
        pytest.param(
            "image.quality",
            "gte",
            0.5,
            "quality_score",
            id="different-semantics-quality-threshold",
        ),
        pytest.param(
            "image.is_duplicate",
            "eq",
            False,
            "duplicate_group_membership",
            id="different-semantics-deduplication",
        ),
    ),
)
def test_retrieval_builds_a_self_contained_operator_plan(
    field: str,
    operator: str,
    value,
    evidence: str,
) -> None:
    result = _retrieve(
        field=field,
        operator=operator,
        value=value,
        evidence=evidence,
    )

    assert result["retrieval_plan"]["sufficient"] is True
    assert result["operator_plan"]["confirmed"] is False
    assert result["operator_plan"]["operators"]
    assert all(
        item["parameter_schema"]
        and item["input_schema"]
        and item["output_schema"]
        for item in result["operator_plan"]["operators"]
    )


def test_missing_capability_does_not_offer_an_operator_plan_for_confirmation() -> None:
    result = _retrieve(
        field="sensor.nonexistent_observable",
        operator="eq",
        value=True,
        evidence="unavailable_evidence",
    )

    assert result["retrieval_plan"]["sufficient"] is False
    assert not any(
        item["executable"]
        for item in result["operator_plan"]["operators"]
    )


def test_legacy_shallow_candidates_are_hydrated_without_model_retrieval() -> None:
    library = build_operator_library(include_datajuicer=False)
    result = _retrieve(
        field="image.quality",
        operator="gte",
        value=0.5,
        evidence="quality_score",
    )
    plan = hydrate_operator_plan(
        {
            "owner_id": "user_1",
            "task_spec": TaskSpecVersion(
                id="spec_image_quality",
                version=1,
                created_by="user_1",
                change_reason="legacy state",
                work_order_id="work_hydration",
                objective="Apply an image quality threshold",
                data_sources=(
                    DataSourceSpec(
                        type="local_directory",
                        uri="D:/generic",
                    ),
                ),
                constraints=(
                    ConstraintContract(
                        id="constraint_quality",
                        source_text="image quality score",
                        scope="asset",
                        field="image.quality",
                        operator="gte",
                        value=0.5,
                        unit="score",
                        required_evidence_type="quality_score",
                    ),
                ),
                confirmed=True,
            ).model_dump(mode="json"),
            "retrieval_plan": result["retrieval_plan"],
            "operator_candidates": result["operator_candidates"],
            "capability_coverage": result["capability_coverage"],
        },
        operator_registry=library.registry,
    )

    assert plan.operators
    assert all(item.parameter_schema for item in plan.operators)
