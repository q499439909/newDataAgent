from __future__ import annotations

import inspect
import hashlib
import json
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, get_args, get_origin


def _split_annotation_union(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character == "[":
            depth += 1
        elif character == "]":
            depth = max(0, depth - 1)
        elif character in {"|", ","} and depth == 0:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return [item for item in parts if item]


def _string_annotation_types(value: str) -> list[str]:
    normalized = value.lower().replace("typing.", "").replace(" ", "")
    if normalized.startswith("optional[") and normalized.endswith("]"):
        inner = normalized[len("optional[") : -1]
        return list(dict.fromkeys([*_string_annotation_types(inner), "null"]))
    if normalized.startswith("union[") and normalized.endswith("]"):
        normalized = normalized[len("union[") : -1]
    union = _split_annotation_union(normalized)
    if len(union) > 1:
        return list(
            dict.fromkeys(
                item_type
                for item in union
                for item_type in _string_annotation_types(item)
            )
        )
    if normalized in {"none", "nonetype", "null"}:
        return ["null"]
    if normalized.startswith(("dict", "mapping", "mutablemapping")):
        return ["object"]
    if normalized.startswith(("list", "tuple", "set", "sequence")):
        return ["array"]
    if normalized in {"bool", "boolean"}:
        return ["boolean"]
    if normalized in {"int", "integer"}:
        return ["integer"]
    if normalized in {"float", "number"}:
        return ["number"]
    if normalized.endswith("bool"):
        return ["boolean"]
    if normalized.endswith("int"):
        return ["integer"]
    if normalized.endswith(("float", "decimal")):
        return ["number"]
    return ["string"]


def _json_type(annotation: Any, default: Any) -> str | list[str]:
    candidate = annotation if annotation is not inspect.Signature.empty else type(default)
    if isinstance(candidate, str):
        resolved = _string_annotation_types(candidate)
        if default is None and "null" not in resolved:
            resolved.append("null")
        return resolved[0] if len(resolved) == 1 else resolved
    origin = get_origin(candidate)
    if origin is not None:
        if origin is dict:
            resolved = ["object"]
        elif origin in {list, tuple, set, frozenset}:
            resolved = ["array"]
        else:
            nested = [
                _json_type(item, inspect.Signature.empty)
                for item in get_args(candidate)
            ]
            resolved = [
                item
                for value in nested
                for item in (value if isinstance(value, list) else [value])
            ]
        if default is None and "null" not in resolved:
            resolved.append("null")
        return list(dict.fromkeys(resolved))
    if candidate is bool:
        return ["boolean", "null"] if default is None else "boolean"
    if candidate is int:
        return ["integer", "null"] if default is None else "integer"
    if candidate is float:
        return ["number", "null"] if default is None else "number"
    if candidate in (list, tuple):
        return "array"
    if candidate is dict:
        return "object"
    if candidate is type(None):
        return "null"
    if candidate is str and default is None:
        return ["string", "null"]
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


def _package_identity() -> dict[str, Any]:
    from importlib.metadata import distribution

    for package in ("py-data-juicer", "data-juicer"):
        try:
            installed = distribution(package)
        except PackageNotFoundError:
            continue
        metadata = installed.metadata
        license_files = []
        for entry in installed.files or ():
            lowered = str(entry).lower()
            if not any(name in lowered for name in ("license", "notice", "copying")):
                continue
            resolved = Path(installed.locate_file(entry)).resolve()
            if resolved.is_file():
                license_files.append(
                    {
                        "path": str(entry),
                        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
                    }
                )
        return {
            "distribution": package,
            "name": metadata.get("Name") or package,
            "version": installed.version,
            "license": metadata.get("License-Expression") or metadata.get("License") or "",
            "home_page": metadata.get("Home-page") or "",
            "provides_extras": sorted(metadata.get_all("Provides-Extra") or []),
            "license_files": license_files,
        }
    return {}


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
        "package_identity": _package_identity(),
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
