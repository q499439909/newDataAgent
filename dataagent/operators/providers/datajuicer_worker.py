from __future__ import annotations

import inspect
import json
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, get_args, get_origin


def _json_type(annotation: Any, default: Any) -> str | list[str]:
    candidate = annotation if annotation is not inspect.Signature.empty else type(default)
    if isinstance(candidate, str):
        return {
            "bool": "boolean",
            "int": "integer",
            "float": "number",
            "list": "array",
            "tuple": "array",
            "dict": "object",
            "None": "null",
            "NoneType": "null",
        }.get(candidate, "string")
    origin = get_origin(candidate)
    if origin is not None:
        resolved = [_json_type(item, inspect.Signature.empty) for item in get_args(candidate)]
        flattened = [
            item
            for value in resolved
            for item in (value if isinstance(value, list) else [value])
        ]
        return list(dict.fromkeys(flattened))
    if candidate is bool:
        return "boolean"
    if candidate is int:
        return "integer"
    if candidate is float:
        return "number"
    if candidate in (list, tuple):
        return "array"
    if candidate is dict:
        return "object"
    if candidate is type(None):
        return "null"
    return "string"


def _parameter_schema(record: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    signature = record.get("sig")
    descriptions = record.get("param_desc_map", {}) or {}
    if signature is not None:
        for name, parameter in signature.parameters.items():
            if name in {"self", "args", "kwargs"} or parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            schema: dict[str, Any] = {
                "type": _json_type(parameter.annotation, parameter.default),
                "description": str(descriptions.get(name, "")).strip(),
            }
            if parameter.default is inspect.Signature.empty:
                required.append(name)
            elif isinstance(parameter.default, (str, int, float, bool, list, dict)) or (
                parameter.default is None
            ):
                schema["default"] = parameter.default
            properties[name] = schema
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _package_version() -> str:
    for package in ("py-data-juicer", "data-juicer"):
        try:
            return version(package)
        except PackageNotFoundError:
            continue
    return "unavailable"


def _resolve_process_bin() -> str | None:
    found = shutil.which("dj-process")
    if found:
        return found
    executable = Path(sys.executable).resolve()
    for candidate in (
        executable.parent / "dj-process.exe",
        executable.parent / "dj-process",
        executable.parent / "Scripts" / "dj-process.exe",
        executable.parent / "Scripts" / "dj-process",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def health() -> dict[str, Any]:
    package_version = _package_version()
    return {
        "ok": package_version != "unavailable",
        "provider_version": package_version,
        "python": sys.executable,
        "process_bin": _resolve_process_bin(),
    }


def discover() -> dict[str, Any]:
    from data_juicer.tools.op_search import OPSearcher

    records = []
    for item in OPSearcher(include_formatter=False).search():
        records.append(
            {
                "name": str(item.get("name", "")).strip(),
                "desc": str(item.get("desc", "")).strip(),
                "type": str(item.get("type", "")).strip().lower(),
                "tags": [str(tag).strip().lower() for tag in item.get("tags", [])],
                "parameter_schema": _parameter_schema(item),
            }
        )
    return {**health(), "operators": records}


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "health"
    try:
        if action == "health":
            payload = health()
        elif action == "discover":
            payload = discover()
        else:
            raise ValueError(f"Unsupported worker action: {action}")
    except Exception as exc:
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
