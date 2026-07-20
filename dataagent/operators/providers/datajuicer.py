from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, get_args, get_origin

from ...domain.operators import ExecutionScope, OperatorCategory, RuntimeBackend
from ..validation import ParameterValidationError, validate_parameters
from .protocol import (
    ProviderDatasetExecuteRequest,
    ProviderDatasetExecuteResult,
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
        simple = {
            "bool": "boolean",
            "int": "integer",
            "float": "number",
            "list": "array",
            "tuple": "array",
            "dict": "object",
            "None": "null",
            "NoneType": "null",
        }.get(candidate)
        if simple is not None:
            return simple
        normalized = candidate.lower().replace("typing.", "").replace(" ", "")
        if normalized.startswith(("dict", "mapping", "mutablemapping")):
            return "object"
        if normalized.startswith(("list", "tuple", "set", "sequence")):
            return "array"
        return "string"
    origin = get_origin(candidate)
    if origin is not None:
        if origin is dict:
            return "object"
        if origin in {list, tuple, set, frozenset}:
            return "array"
        resolved = [_json_type(item, inspect.Signature.empty) for item in get_args(candidate)]
        flattened = [
            item
            for value in resolved
            for item in (value if isinstance(value, list) else [value])
        ]
        return list(dict.fromkeys(flattened)) or _json_type(origin, default)
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
    if op_type == "selector":
        secondary = "random_sampling" if "random" in lowered else "stratified_sampling"
        return OperatorCategory.SAMPLING, secondary
    if op_type in {"aggregator", "grouper"}:
        return OperatorCategory.EVALUATION, "distribution_profile"
    if op_type == "pipeline":
        return OperatorCategory.TRANSFORMATION, "annotation_conversion"
    return OperatorCategory.UNDERSTANDING, "classification"


def _execution_scope(op_type: str) -> ExecutionScope:
    if op_type in {"deduplicator", "selector", "aggregator", "grouper", "pipeline"}:
        return ExecutionScope.DATASET
    return ExecutionScope.ASSET


def _runtime_backends(tags: frozenset[str]) -> tuple[RuntimeBackend, ...]:
    backends: list[RuntimeBackend] = []
    if "cpu" in tags:
        backends.append(RuntimeBackend.CPU)
    if "gpu" in tags:
        backends.append(RuntimeBackend.CUDA)
    return tuple(backends)


def _runtime_schema_type(value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "null" if value is None else None


def _runtime_validation_schema(
    schema: dict[str, Any], parameters: dict[str, Any]
) -> dict[str, Any]:
    repaired = deepcopy(schema)
    for name, property_schema in repaired.get("properties", {}).items():
        if property_schema.get("type") != []:
            continue
        value = parameters.get(name, property_schema.get("default"))
        inferred = _runtime_schema_type(value)
        if inferred is not None:
            property_schema["type"] = inferred
    return repaired


class DataJuicerOperatorProvider:
    provider_id = "datajuicer"

    def __init__(
        self,
        *,
        searcher_factory: Callable[[], Any] | None = None,
        executor: Callable[[ProviderExecuteRequest], ProviderExecuteResult] | None = None,
        provider_version: str | None = None,
        allow_model_download: bool = False,
        availability_error: str | None = None,
        catalog_cache_path: Path | None = None,
    ) -> None:
        self._searcher_factory = searcher_factory
        self._executor = executor
        self.provider_version = provider_version or self._installed_version()
        self.allow_model_download = allow_model_download
        self.availability_error = availability_error
        self.catalog_cache_path = (
            catalog_cache_path.expanduser().resolve() if catalog_cache_path else None
        )
        self._catalog_descriptors: dict[str, ProviderOperatorDescriptor] | None = None
        self._normalized_descriptors: dict[str, ProviderOperatorDescriptor] = {}
        self._admitted_descriptors: dict[str, ProviderOperatorDescriptor] = {}

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
        if self._catalog_descriptors is None:
            self._catalog_descriptors = self._load_catalog_cache()
        if self._catalog_descriptors is None:
            self._catalog_descriptors = self._search_catalog()
            self._save_catalog_cache(self._catalog_descriptors.values())
        return list(self._catalog_descriptors.values())

    def refresh_catalog(self) -> list[ProviderOperatorDescriptor]:
        self._catalog_descriptors = self._search_catalog()
        self._save_catalog_cache(self._catalog_descriptors.values())
        return list(self._catalog_descriptors.values())

    def _search_catalog(self) -> dict[str, ProviderOperatorDescriptor]:
        records = self._searcher().search()
        return {
            descriptor.provider_operator_ref: descriptor
            for descriptor in (self._descriptor(record) for record in records)
        }

    def describe(self, provider_operator_ref: str) -> ProviderOperatorDescriptor:
        try:
            catalog = {
                item.provider_operator_ref: item for item in self.discover()
            }
        except Exception:
            catalog = {}
        try:
            return catalog[provider_operator_ref]
        except KeyError as exc:
            try:
                return self._admitted_descriptors[provider_operator_ref]
            except KeyError:
                raise KeyError(
                    f"Data-Juicer operator not found: {provider_operator_ref}"
                ) from exc

    def validate(
        self,
        provider_operator_ref: str,
        parameters: dict[str, Any],
        runtime_backend: RuntimeBackend,
    ) -> ProviderValidationResult:
        descriptor = self._admitted_descriptors.get(
            provider_operator_ref
        ) or self._normalized_descriptors.get(provider_operator_ref)
        if descriptor is None:
            descriptor = self.describe(provider_operator_ref)
        errors: list[str] = []
        if runtime_backend == RuntimeBackend.REMOTE:
            if "api" not in descriptor.tags:
                errors.append("The operator is not declared as a remote API operator")
            if parameters.get("is_api_model") is not True:
                errors.append("Remote Data-Juicer execution requires is_api_model=true")
            if not parameters.get("api_endpoint"):
                errors.append("Remote Data-Juicer execution requires api_endpoint")
        elif runtime_backend == RuntimeBackend.CPU:
            if "cpu" not in descriptor.tags:
                errors.append("The operator is not declared as CPU-compatible")
            if not self.allow_model_download and any(
                tag in descriptor.tags for tag in {"gpu", "llm", "model"}
            ):
                errors.append("Model-backed Data-Juicer operators are disabled in offline mode")
        else:
            errors.append(
                f"Data-Juicer executor does not provide {runtime_backend.value} workers"
            )
        if "image" not in descriptor.tags:
            errors.append("The current DataAgent executor accepts image operators only")
        if errors:
            return ProviderValidationResult(ok=False, errors=tuple(errors))
        schema_parameters = dict(parameters)
        runtime_parameters: dict[str, Any] = {}
        schema_properties = descriptor.parameter_schema.get("properties", {})
        if "accelerator" in schema_parameters and "accelerator" not in schema_properties:
            accelerator = schema_parameters.pop("accelerator")
            expected = "cpu" if runtime_backend in {
                RuntimeBackend.CPU,
                RuntimeBackend.REMOTE,
            } else runtime_backend.value
            if accelerator != expected:
                return ProviderValidationResult(
                    ok=False,
                    errors=(
                        f"Runtime control accelerator must be {expected!r}; "
                        f"got {accelerator!r}",
                    ),
                )
            runtime_parameters["accelerator"] = accelerator
        try:
            normalized = validate_parameters(
                _runtime_validation_schema(
                    descriptor.parameter_schema,
                    schema_parameters,
                ),
                schema_parameters,
            )
        except ParameterValidationError as exc:
            return ProviderValidationResult(ok=False, errors=(str(exc),))
        normalized.update(runtime_parameters)
        return ProviderValidationResult(ok=True, normalized_parameters=normalized)

    def execute(self, request: ProviderExecuteRequest) -> ProviderExecuteResult:
        if self._executor is None:
            return ProviderExecuteResult(
                ok=False,
                error_type="provider_execution_unconfigured",
                message="Data-Juicer execution requires an isolated provider worker",
            )
        validation = self.validate(
            request.provider_operator_ref,
            request.parameters,
            request.runtime_backend,
        )
        if not validation.ok:
            return ProviderExecuteResult(
                ok=False,
                error_type="provider_validation_failed",
                message="; ".join(validation.errors),
            )
        normalized_request = request.model_copy(
            update={"parameters": validation.normalized_parameters}
        )
        return self._executor(normalized_request)

    def execute_dataset(
        self, request: ProviderDatasetExecuteRequest
    ) -> ProviderDatasetExecuteResult:
        if self._executor is None or not hasattr(self._executor, "execute_dataset"):
            return ProviderDatasetExecuteResult(
                ok=False,
                error_type="provider_execution_unconfigured",
                message="Data-Juicer dataset execution requires an isolated provider worker",
            )
        validation = self.validate(
            request.provider_operator_ref,
            request.parameters,
            request.runtime_backend,
        )
        if not validation.ok:
            return ProviderDatasetExecuteResult(
                ok=False,
                error_type="provider_validation_failed",
                message="; ".join(validation.errors),
            )
        normalized = request.model_copy(
            update={"parameters": validation.normalized_parameters}
        )
        return self._executor.execute_dataset(normalized)

    def admit(self, descriptors: list[ProviderOperatorDescriptor]) -> None:
        """Store frozen release descriptors without changing discovery catalog results."""
        self._admitted_descriptors.update(
            {item.provider_operator_ref: item for item in descriptors}
        )

    def register_normalized(
        self, descriptors: list[ProviderOperatorDescriptor]
    ) -> None:
        """Register an in-memory execution view without changing discovery or admission."""
        self._normalized_descriptors.update(
            {item.provider_operator_ref: item for item in descriptors}
        )

    def admitted(self) -> list[ProviderOperatorDescriptor]:
        return list(self._admitted_descriptors.values())

    def _load_catalog_cache(self) -> dict[str, ProviderOperatorDescriptor] | None:
        path = self.catalog_cache_path
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("provider_version") != self.provider_version:
                return None
            descriptors = [
                ProviderOperatorDescriptor.model_validate(item)
                for item in payload.get("operators", [])
            ]
        except (OSError, ValueError, TypeError):
            return None
        return {item.provider_operator_ref: item for item in descriptors}

    def _save_catalog_cache(self, descriptors: Any) -> None:
        path = self.catalog_cache_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "operators": [item.model_dump(mode="json") for item in descriptors],
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    def health(self) -> ProviderHealth:
        if self.availability_error:
            return ProviderHealth(
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                status=ProviderHealthStatus.UNAVAILABLE,
                message=self.availability_error,
            )
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
        tags = frozenset(
            str(tag).strip().lower()
            for tag in record.get("tags", [])
            if str(tag).strip()
        )
        supplied_schema = record.get("parameter_schema")
        if isinstance(supplied_schema, dict):
            parameter_schema = supplied_schema
        else:
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
                    elif (
                        isinstance(param.default, (str, int, float, bool, list, dict))
                        or param.default is None
                    ):
                        schema["default"] = param.default
                    properties[param_name] = schema
            parameter_schema = {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
        category, secondary = _suggest_category(op_type, name)
        digest_source = json.dumps(
            {
                "provider_version": self.provider_version,
                "name": name,
                "type": op_type,
                "description": description,
                "parameter_schema": parameter_schema,
                "tags": sorted(tags),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        return ProviderOperatorDescriptor(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            provider_operator_ref=name,
            provider_operator_type=op_type,
            display_name=name,
            description=description,
            parameter_schema=parameter_schema,
            tags=tags,
            source_digest=hashlib.sha256(digest_source.encode("utf-8")).hexdigest(),
            suggested_category=category,
            suggested_secondary_category=secondary,
            suggested_execution_scope=_execution_scope(op_type),
            supported_runtime_backends=_runtime_backends(tags),
        )


__all__ = ["DataJuicerOperatorProvider"]
