from __future__ import annotations

import re
from typing import Any, Iterable

from .models import ConstraintContract


_IGNORED_TOKENS = frozenset(
    {
        "applied",
        "asset",
        "assets",
        "audio",
        "bytes",
        "data",
        "dataset",
        "image",
        "policy",
        "primary",
        "px",
        "subject",
        "video",
    }
)


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in re.findall(
            r"[a-z0-9]+",
            value.lower().replace(".", "_").replace("-", "_"),
        )
        if token not in _IGNORED_TOKENS
    )


def _parameter_role(name: str) -> str:
    if name.startswith("min_"):
        return "lower"
    if name.startswith("max_"):
        return "upper"
    return "exact"


def _required_role(operator: str) -> str:
    if operator in {"gt", "gte"}:
        return "lower"
    if operator in {"lt", "lte"}:
        return "upper"
    return "exact"


def _encoded_value(
    constraint: ConstraintContract,
    property_schema: dict[str, Any],
) -> Any:
    value = constraint.value
    if (
        constraint.operator in {"lt", "gt"}
        and property_schema.get("type") == "integer"
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        value = value - 1 if constraint.operator == "lt" else value + 1
    if property_schema.get("type") == "string" and isinstance(value, int):
        if constraint.unit.lower() in {"byte", "bytes"}:
            for divisor, suffix in (
                (1024 * 1024 * 1024, "GB"),
                (1024 * 1024, "MB"),
                (1024, "KB"),
            ):
                if value % divisor == 0:
                    return f"{value // divisor}{suffix}"
    return value


def bind_constraint_parameters(
    parameter_schema: dict[str, Any],
    constraints: Iterable[ConstraintContract],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Bind observable constraints to an operator's declared parameter schema.

    Matching is based on parameter roles and names, so a newly registered
    operator such as ``min_vehicle_count`` works without adding a business rule.
    """

    properties = parameter_schema.get("properties", {})
    parameters: dict[str, Any] = {}
    covered: list[str] = []
    for constraint in constraints:
        target_tokens = _tokens(constraint.field)
        role = _required_role(constraint.operator)
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for name, raw_schema in properties.items():
            property_schema = raw_schema if isinstance(raw_schema, dict) else {}
            parameter_tokens = _tokens(
                re.sub(r"^(?:min|max)_", "", str(name))
            )
            overlap = target_tokens.intersection(parameter_tokens)
            if not overlap:
                continue
            parameter_role = _parameter_role(str(name))
            role_score = 100 if parameter_role == role else 0
            if role != "exact" and parameter_role == "exact":
                role_score = 10
            if role == "exact" and parameter_role != "exact":
                continue
            ranked.append(
                (
                    role_score + 10 * len(overlap),
                    str(name),
                    property_schema,
                )
            )
        if not ranked:
            continue
        score, name, property_schema = max(
            ranked,
            key=lambda item: (item[0], item[1]),
        )
        if score < 100:
            continue
        parameters[name] = _encoded_value(constraint, property_schema)
        if (
            role == "upper"
            and constraint.unit.lower() == "count"
            and name.startswith("max_")
        ):
            lower_name = "min_" + name.removeprefix("max_")
            if lower_name in properties:
                lower_schema = properties.get(lower_name, {})
                lower_bound = (
                    lower_schema.get("minimum", 0)
                    if isinstance(lower_schema, dict)
                    else 0
                )
                parameters.setdefault(lower_name, max(0, int(lower_bound)))
        covered.append(constraint.id)

    if covered and "any_or_all" in properties:
        parameters["any_or_all"] = "all"
    return parameters, tuple(covered)


__all__ = ["bind_constraint_parameters"]
