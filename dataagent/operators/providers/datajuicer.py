from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Any, get_args, get_origin

from ...domain.operators import OperatorCategory, RuntimeBackend
from ..validation import ParameterValidationError, validate_parameters
from .protocol import (
    ProviderExecuteRequest,
    ProviderExecuteResult,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderOperatorDescriptor,
    ProviderValidationResult,
)


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


def _suggest_category(op_type: str, name: str) -> tuple[OperatorCategory, str]:
    lowered = name.lower()
    if "deduplicator" in op_type or "dedup" in lowered:
        return OperatorCategory.DEDUPLICATION, "semantic_duplicate"
    if op_type == "filter":
        return OperatorCategory.FILTERING, "semantic_rule"
    if any(token in lowered for token in ("segment", "detect", "tagging", "caption")):
        secondary = "segmentation" if "segment" in lowered else "object_detection"
        return OperatorCategory.UNDERSTANDING, secondary
    if op_type == "mapper":
        return OperatorCategory.TRANSFORMATION, "annotation_conversion"
    return OperatorCategory.UNDERSTANDING, "classification"


class DataJuicerOperatorProvider:
    provider_id = "datajuicer"

    def __init__(
        self,
        *,
        searcher_factory: Callable[[], Any] | None = None,
        executor: Callable[[ProviderExecuteRequest], ProviderExecuteResult] | None = None,
        provider_version: str | None = None,
    ) -> None:
        self._searcher_factory = searcher_factory
        self._executor = executor
        self.provider_version = provider_version or self._installed_version()
        self._descriptors: dict[str, ProviderOperatorDescriptor] | None = None

    @staticmethod
    def _installed_version() -> str:
        for package in ("py-data-juicer", "data-juicer"):
            try:
                return version(package)
            except PackageNotFoundError:
                continue
        return "unavailable"

    def _searcher(self) -> Any:
        if self._searcher_factory is not None:
            return self._searcher_factory()
        from data_juicer.tools.op_search import OPSearcher

        return OPSearcher(include_formatter=False)

    def discover(self) -> list[ProviderOperatorDescriptor]:
        if self._descriptors is None:
            searcher = self._searcher()
            records = searcher.search()
            self._descriptors = {
                descriptor.provider_operator_ref: descriptor
                for descriptor in (self._descriptor(record) for record in records)
            }
        return list(self._descriptors.values())

    def describe(self, provider_operator_ref: str) -> ProviderOperatorDescriptor:
        if self._descriptors is None:
            self.discover()
        assert self._descriptors is not None
        try:
            return self._descriptors[provider_operator_ref]
        except KeyError as exc:
            raise KeyError(f"Data-Juicer operator not found: {provider_operator_ref}") from exc

    def validate(
        self,
        provider_operator_ref: str,
        parameters: dict[str, Any],
        runtime_backend: RuntimeBackend,
    ) -> ProviderValidationResult:
        descriptor = self.describe(provider_operator_ref)
        try:
            normalized = validate_parameters(descriptor.parameter_schema, parameters)
        except ParameterValidationError as exc:
            return ProviderValidationResult(ok=False, errors=(str(exc),))
        return ProviderValidationResult(ok=True, normalized_parameters=normalized)

    def execute(self, request: ProviderExecuteRequest) -> ProviderExecuteResult:
        if self._executor is None:
            return ProviderExecuteResult(
                ok=False,
                error_type="provider_execution_unconfigured",
                message="Data-Juicer execution requires an isolated provider worker",
            )
        return self._executor(request)

    def health(self) -> ProviderHealth:
        if self._searcher_factory is None and self.provider_version == "unavailable":
            return ProviderHealth(
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                status=ProviderHealthStatus.UNAVAILABLE,
                message="py-data-juicer is not installed in this environment",
            )
        return ProviderHealth(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            status=(
                ProviderHealthStatus.AVAILABLE
                if self._executor is not None
                else ProviderHealthStatus.DEGRADED
            ),
            message="Metadata discovery is available; execution worker is not configured"
            if self._executor is None
            else "",
        )

    def _descriptor(self, record: dict[str, Any]) -> ProviderOperatorDescriptor:
        name = str(record.get("name", "")).strip()
        description = str(record.get("desc", "")).strip()
        op_type = str(record.get("type", "")).strip().lower()
        tags = frozenset(str(tag).strip().lower() for tag in record.get("tags", []) if str(tag).strip())
        properties: dict[str, Any] = {}
        required: list[str] = []
        signature = record.get("sig")
        if signature is not None:
            descriptions = record.get("param_desc_map", {}) or {}
            for param_name, param in signature.parameters.items():
                if param_name in {"self", "args", "kwargs"} or param.kind in {
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                }:
                    continue
                schema: dict[str, Any] = {
                    "type": _json_type(param.annotation, param.default),
                    "description": str(descriptions.get(param_name, "")).strip(),
                }
                if param.default is inspect.Signature.empty:
                    required.append(param_name)
                elif isinstance(param.default, (str, int, float, bool, list, dict)) or param.default is None:
                    schema["default"] = param.default
                properties[param_name] = schema
        parameter_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        category, secondary = _suggest_category(op_type, name)
        digest_source = f"{self.provider_version}|{name}|{op_type}|{description}|{parameter_schema}"
        return ProviderOperatorDescriptor(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            provider_operator_ref=name,
            display_name=name,
            description=description,
            parameter_schema=parameter_schema,
            tags=tags,
            source_digest=hashlib.sha256(digest_source.encode("utf-8")).hexdigest(),
            suggested_category=category,
            suggested_secondary_category=secondary,
        )


__all__ = ["DataJuicerOperatorProvider"]
