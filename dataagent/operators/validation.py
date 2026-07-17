from __future__ import annotations

from typing import Any


class ParameterValidationError(ValueError):
    """Raised when operator parameters do not satisfy their declared schema."""


_TYPE_NAMES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


def _is_expected_type(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    types = _TYPE_NAMES.get(expected)
    if types is None:
        raise ParameterValidationError(f"Unsupported parameter schema type: {expected}")
    return isinstance(value, types)


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> Any:
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_is_expected_type(value, item) for item in expected):
            raise ParameterValidationError(
                f"{path} must have one of the types {expected}; got {type(value).__name__}"
            )
    elif isinstance(expected, str) and not _is_expected_type(value, expected):
        raise ParameterValidationError(
            f"{path} must be {expected}; got {type(value).__name__}"
        )

    if "enum" in schema and value not in schema["enum"]:
        raise ParameterValidationError(f"{path} must be one of {schema['enum']}; got {value!r}")
    if value is not None and "minimum" in schema and value < schema["minimum"]:
        raise ParameterValidationError(f"{path} must be >= {schema['minimum']}; got {value!r}")
    if value is not None and "maximum" in schema and value > schema["maximum"]:
        raise ParameterValidationError(f"{path} must be <= {schema['maximum']}; got {value!r}")
    if isinstance(value, str) and "minLength" in schema and len(value) < schema["minLength"]:
        raise ParameterValidationError(f"{path} is shorter than {schema['minLength']} characters")

    if isinstance(value, dict):
        return _validate_object(value, schema, path)
    if isinstance(value, (list, tuple)) and isinstance(schema.get("items"), dict):
        return [
            _validate_value(item, schema["items"], f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    return value


def _validate_object(
    values: dict[str, Any], schema: dict[str, Any], path: str
) -> dict[str, Any]:
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    missing = sorted(required.difference(values))
    if missing:
        raise ParameterValidationError(f"{path} is missing required fields: {missing}")

    if schema.get("additionalProperties") is False:
        unknown = sorted(set(values).difference(properties))
        if unknown:
            raise ParameterValidationError(f"{path} contains unknown fields: {unknown}")

    normalized: dict[str, Any] = {}
    for key, property_schema in properties.items():
        if key in values:
            normalized[key] = _validate_value(values[key], property_schema, f"{path}.{key}")
        elif "default" in property_schema:
            normalized[key] = property_schema["default"]
    for key, value in values.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def validate_parameters(
    schema: dict[str, Any], parameters: dict[str, Any]
) -> dict[str, Any]:
    """Validate the supported JSON Schema subset used by operator parameters."""

    if not isinstance(parameters, dict):
        raise ParameterValidationError("parameters must be an object")
    if not schema:
        return dict(parameters)
    if schema.get("type", "object") != "object":
        raise ParameterValidationError("operator parameter schema must describe an object")
    return _validate_object(parameters, schema, "parameters")


__all__ = ["ParameterValidationError", "validate_parameters"]
