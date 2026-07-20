from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ...domain.operators import (
    ExecutionScope,
    ImplementationSpec,
    ImplementationType,
    OperatorCategory,
    OperatorSpecVersion,
    OperatorStatus,
    ProviderRef,
    RuntimeBackend,
    RuntimeProfile,
)
from ..protocol import OperatorContext, OperatorInput, OperatorResult
from .protocol import (
    OperatorProvider,
    ProviderDatasetExecuteRequest,
    ProviderDatasetItem,
    ProviderExecuteRequest,
    ProviderOperatorDescriptor,
)


@dataclass(frozen=True)
class DataJuicerAdmission:
    ref: str
    display_name: str
    summary: str
    category: OperatorCategory
    secondary_category: str
    tags: frozenset[str]
    parameter_schema: dict[str, Any]
    execution_scope: ExecutionScope = ExecutionScope.ASSET
    compatible_versions: frozenset[str] = frozenset({"1.5.3"})
    dependencies: tuple[str, ...] = ("py-data-juicer==1.5.3",)


DATAJUICER_ADMISSIONS = (
    DataJuicerAdmission(
        ref="image_shape_filter",
        display_name="Data-Juicer Image Shape Filter",
        summary="Filter images by minimum and maximum pixel dimensions.",
        category=OperatorCategory.FILTERING,
        secondary_category="resolution_and_format",
        tags=frozenset({"cpu", "image", "filter", "datajuicer"}),
        parameter_schema={
            "type": "object",
            "properties": {
                "min_width": {"type": "integer", "default": 1},
                "max_width": {"type": "integer", "default": 9223372036854775807},
                "min_height": {"type": "integer", "default": 1},
                "max_height": {"type": "integer", "default": 9223372036854775807},
                "any_or_all": {
                    "type": "string",
                    "enum": ["any", "all"],
                    "default": "any",
                },
            },
            "additionalProperties": False,
        },
    ),
    DataJuicerAdmission(
        ref="image_aspect_ratio_filter",
        display_name="Data-Juicer Image Aspect Ratio Filter",
        summary="Filter images using an admitted width-to-height ratio range.",
        category=OperatorCategory.FILTERING,
        secondary_category="resolution_and_format",
        tags=frozenset({"cpu", "image", "filter", "datajuicer"}),
        parameter_schema={
            "type": "object",
            "properties": {
                "min_ratio": {"type": "number", "default": 0.333},
                "max_ratio": {"type": "number", "default": 3.0},
                "any_or_all": {
                    "type": "string",
                    "enum": ["any", "all"],
                    "default": "any",
                },
            },
            "additionalProperties": False,
        },
    ),
    DataJuicerAdmission(
        ref="image_deduplicator",
        display_name="Data-Juicer Image Deduplicator",
        summary="Deduplicate a complete image dataset using Data-Juicer semantics.",
        category=OperatorCategory.DEDUPLICATION,
        secondary_category="perceptual_duplicate",
        tags=frozenset({"cpu", "image", "deduplication", "datajuicer"}),
        parameter_schema={
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["phash", "dhash", "whash", "ahash"],
                    "default": "phash",
                },
                "consider_text": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        execution_scope=ExecutionScope.DATASET,
        dependencies=(
            "py-data-juicer==1.5.3",
            "imagededup==0.3.3.post2",
        ),
    ),
)


def _digest(provider_version: str, admission: DataJuicerAdmission) -> str:
    payload = {
        "provider": "datajuicer",
        "provider_version": provider_version,
        "ref": admission.ref,
        "schema": admission.parameter_schema,
        "scope": admission.execution_scope,
        "dependencies": admission.dependencies,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class ProviderProxyOperator:
    def __init__(
        self,
        spec: OperatorSpecVersion,
        provider: OperatorProvider,
        *,
        provider_operator_type: str,
    ) -> None:
        self.spec = spec
        self.provider = provider
        self.provider_operator_type = provider_operator_type
        self.supports_dataset_batch = (
            self.spec.execution_scope == ExecutionScope.DATASET
            or (
                provider_operator_type == "filter"
                and "cpu" in self.spec.capability_tags
                and "image" in self.spec.capability_tags
            )
        )

    def execute(
        self,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict[str, Any],
    ) -> OperatorResult:
        node_id = str(context.shared.get("active_node_id", ""))
        prepared = context.shared.get("dataset_operator_results", {}).get(node_id, {})
        if input_data.source_path in prepared:
            return self._merge_prepared(prepared[input_data.source_path], input_data)
        if self.spec.execution_scope == ExecutionScope.DATASET:
            try:
                return prepared[input_data.source_path]
            except KeyError as exc:
                raise RuntimeError(
                    f"Dataset result was not prepared for {self.spec.id}: "
                    f"{input_data.source_path}"
                ) from exc
        response = self.provider.execute(
            ProviderExecuteRequest(
                provider_operator_ref=self.spec.provider.provider_operator_ref,
                runtime_backend=RuntimeBackend(
                    context.shared.get("active_runtime_backend", RuntimeBackend.CPU)
                ),
                context=context,
                input_data=input_data,
                parameters=parameters,
            )
        )
        if not response.ok or response.result is None:
            raise RuntimeError(response.message or response.error_type or "Provider failed")
        return response.result

    @staticmethod
    def _merge_prepared(
        result: OperatorResult,
        input_data: OperatorInput,
    ) -> OperatorResult:
        def merge_unique(current: list, prepared: list) -> list:
            return [*current, *(item for item in prepared if item not in current)]

        return result.model_copy(
            update={
                "metrics": {**input_data.metrics, **result.metrics},
                "labels": {**input_data.labels, **result.labels},
                "artifacts": merge_unique(input_data.artifacts, result.artifacts),
                "annotations": merge_unique(
                    input_data.annotations, result.annotations
                ),
                "embeddings": merge_unique(input_data.embeddings, result.embeddings),
            }
        )

    def prepare_dataset(
        self,
        *,
        node_id: str,
        context: OperatorContext,
        inputs: tuple[OperatorInput, ...],
        parameters: dict[str, Any],
        runtime_backend: RuntimeBackend,
    ) -> None:
        response = self.provider.execute_dataset(
            ProviderDatasetExecuteRequest(
                provider_operator_ref=self.spec.provider.provider_operator_ref,
                runtime_backend=runtime_backend,
                context=context,
                items=tuple(
                    ProviderDatasetItem(asset_id=item.source_path, input_data=item)
                    for item in inputs
                ),
                parameters=parameters,
            )
        )
        if not response.ok:
            raise RuntimeError(response.message or response.error_type or "Provider failed")
        context.shared.setdefault("dataset_operator_results", {})[node_id] = {
            item.asset_id: item.result for item in response.items
        }


def build_datajuicer_proxy_operators(
    provider: OperatorProvider,
    catalog: list[ProviderOperatorDescriptor] | None = None,
) -> tuple[ProviderProxyOperator, ...]:
    admissions = {item.ref: item for item in DATAJUICER_ADMISSIONS}
    catalog_by_ref = {
        item.provider_operator_ref: item for item in (catalog or ())
    }
    for admission in DATAJUICER_ADMISSIONS:
        if provider.provider_version not in admission.compatible_versions:
            continue
        catalog_by_ref.setdefault(
            admission.ref,
            ProviderOperatorDescriptor(
                provider_id=provider.provider_id,
                provider_version=provider.provider_version,
                provider_operator_ref=admission.ref,
                provider_operator_type=(
                    "deduplicator"
                    if admission.category == OperatorCategory.DEDUPLICATION
                    else "filter"
                ),
                display_name=admission.display_name,
                description=admission.summary,
                parameter_schema=admission.parameter_schema,
                tags=admission.tags,
                source_digest=_digest(provider.provider_version, admission),
                suggested_category=admission.category,
                suggested_secondary_category=admission.secondary_category,
                suggested_execution_scope=admission.execution_scope,
                supported_runtime_backends=(RuntimeBackend.CPU,),
            ),
        )
    frozen_descriptors: list[ProviderOperatorDescriptor] = []
    operators: list[ProviderProxyOperator] = []
    for descriptor in catalog_by_ref.values():
        admission = admissions.get(descriptor.provider_operator_ref)
        is_admitted = bool(
            admission
            and provider.provider_version in admission.compatible_versions
        )
        if is_admitted:
            assert admission is not None
            source_digest = _digest(provider.provider_version, admission)
            category = admission.category
            secondary = admission.secondary_category
            tags = admission.tags
            parameter_schema = admission.parameter_schema
            execution_scope = admission.execution_scope
            display_name = admission.display_name
            summary = admission.summary
            dependencies = admission.dependencies
            frozen_descriptors.append(
                descriptor.model_copy(
                    update={
                        "display_name": display_name,
                        "description": summary,
                        "parameter_schema": parameter_schema,
                        "tags": tags,
                        "source_digest": source_digest,
                        "suggested_category": category,
                        "suggested_secondary_category": secondary,
                        "suggested_execution_scope": execution_scope,
                        "supported_runtime_backends": (RuntimeBackend.CPU,),
                    }
                )
            )
        else:
            source_digest = descriptor.source_digest
            category = descriptor.suggested_category or OperatorCategory.UNDERSTANDING
            secondary = descriptor.suggested_secondary_category or "classification"
            tags = frozenset({*descriptor.tags, "datajuicer", "candidate"})
            parameter_schema = descriptor.parameter_schema
            execution_scope = descriptor.suggested_execution_scope
            display_name = descriptor.display_name
            summary = descriptor.description or (
                f"Candidate proxy for Data-Juicer {descriptor.provider_operator_ref}."
            )
            dependencies = (f"py-data-juicer=={provider.provider_version}",)
        runtime_backends = descriptor.supported_runtime_backends or (
            RuntimeBackend.CPU,
        )
        profiles = tuple(
            RuntimeProfile(
                backend=backend,
                memory_mb=2048 if backend == RuntimeBackend.CUDA else 1024,
                gpu_count=1 if backend == RuntimeBackend.CUDA else 0,
            )
            for backend in runtime_backends
        )
        operator_id = f"datajuicer.{descriptor.provider_operator_ref}:1"
        spec = OperatorSpecVersion(
            id=operator_id,
            family_id=operator_id.rsplit(":", 1)[0],
            version=1,
            created_by="system",
            change_reason=(
                "admitted Data-Juicer provider proxy"
                if is_admitted
                else "auto-generated Data-Juicer candidate proxy"
            ),
            display_name=display_name,
            summary=summary,
            description=(
                f"Versioned DataAgent proxy for Data-Juicer "
                f"{descriptor.provider_operator_ref} ({descriptor.provider_operator_type}); "
                "candidate proxies require admission evidence before production use."
            ),
            primary_category=category,
            secondary_category=secondary,
            capability_tags=tags,
            input_schema=("ImageAssetRef" if "image" in tags else "ProviderDatasetRecord"),
            output_schema="ProviderDecision",
            parameter_schema=parameter_schema,
            provider=ProviderRef(
                provider_id=provider.provider_id,
                provider_version=provider.provider_version,
                provider_operator_ref=descriptor.provider_operator_ref,
                source_digest=source_digest,
            ),
            implementation=ImplementationSpec(
                implementation_type=ImplementationType.EXTERNAL_SERVICE,
                entrypoint="dataagent.operators.providers.proxy:ProviderProxyOperator",
                dependency_lock_digest=hashlib.sha256(
                    "\n".join(dependencies).encode("utf-8")
                ).hexdigest(),
            ),
            supported_runtime_profiles=profiles,
            execution_scope=execution_scope,
            implementation_ref="dataagent.operators.providers.proxy:ProviderProxyOperator",
            limitations=(
                "Requires the isolated Data-Juicer provider environment.",
                "Candidate status does not imply production admission."
                if not is_admitted
                else "Released only for the verified provider version.",
            ),
            status=(
                OperatorStatus.PERSONAL_RELEASE
                if is_admitted
                else OperatorStatus.DRAFT
            ),
            owner_id="system",
            visibility="private",
        )
        operators.append(
            ProviderProxyOperator(
                spec,
                provider,
                provider_operator_type=descriptor.provider_operator_type,
            )
        )
    admit = getattr(provider, "admit", None)
    if callable(admit):
        admit(frozen_descriptors)
    return tuple(operators)


__all__ = [
    "DATAJUICER_ADMISSIONS",
    "DataJuicerAdmission",
    "ProviderProxyOperator",
    "build_datajuicer_proxy_operators",
]
