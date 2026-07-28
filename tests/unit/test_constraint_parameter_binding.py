from __future__ import annotations

from dataagent.agents.processing.constraint_binding import bind_constraint_parameters
from dataagent.domain.specs import ConstraintContract


def _constraint(field: str, operator: str, value: int):
    return ConstraintContract(
        id=f"constraint_{field.rsplit('.', 1)[-1]}_{operator}",
        source_text=f"{field} {operator} {value}",
        scope="asset",
        field=field,
        operator=operator,
        value=value,
        unit="count",
        required_evidence_type=f"detected_{field.rsplit('.', 1)[-1]}",
    )


def test_binds_an_unseen_count_constraint_from_operator_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "min_vehicle_count": {"type": "integer", "default": 0},
            "max_vehicle_count": {"type": "integer", "default": 999},
        },
        "additionalProperties": False,
    }

    parameters, covered = bind_constraint_parameters(
        schema,
        (_constraint("image.vehicle_count", "gte", 3),),
    )

    assert parameters == {"min_vehicle_count": 3}
    assert covered == ("constraint_vehicle_count_gte",)


def test_inclusive_face_boundary_is_not_changed_or_inferred_from_filename() -> None:
    schema = {
        "type": "object",
        "properties": {
            "min_face_count": {"type": "integer", "default": 0},
            "max_face_count": {"type": "integer", "default": 999},
            "any_or_all": {
                "type": "string",
                "enum": ["any", "all"],
                "default": "any",
            },
        },
        "additionalProperties": False,
    }

    parameters, covered = bind_constraint_parameters(
        schema,
        (_constraint("image.face_count", "lte", 2),),
    )

    assert parameters == {
        "min_face_count": 0,
        "max_face_count": 2,
        "any_or_all": "all",
    }
    assert covered == ("constraint_face_count_lte",)


def test_strict_integer_boundary_is_converted_by_comparator_semantics() -> None:
    schema = {
        "type": "object",
        "properties": {
            "max_face_count": {"type": "integer", "default": 999},
        },
        "additionalProperties": False,
    }

    parameters, _ = bind_constraint_parameters(
        schema,
        (_constraint("image.face_count", "lt", 2),),
    )

    assert parameters == {"max_face_count": 1}
