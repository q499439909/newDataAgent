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
    def __init__(self, spec: OperatorSpecVersion, provider: OperatorProvider) -> None:
        self.spec = spec
        self.provider = provider

    def execute(
        self,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict[str, Any],
    ) -> OperatorResult:
        if self.spec.execution_scope == ExecutionScope.DATASET:
            node_id = str(context.shared.get("active_node_id", ""))
            prepared = context.shared.get("dataset_operator_results", {}).get(node_id, {})
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
                runtime_backend=RuntimeBackend.CPU,
                context=context,
                input_data=input_data,
                parameters=parameters,
            )
        )
        if not response.ok or response.result is None:
            raise RuntimeError(response.message or response.error_type or "Provider failed")
        return response.result

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
) -> tuple[ProviderProxyOperator, ...]:
    descriptors: list[ProviderOperatorDescriptor] = []
    operators: list[ProviderProxyOperator] = []
    for admission in DATAJUICER_ADMISSIONS:
        if provider.provider_version not in admission.compatible_versions:
            continue
        source_digest = _digest(provider.provider_version, admission)
        descriptor = ProviderOperatorDescriptor(
            provider_id=provider.provider_id,
            provider_version=provider.provider_version,
            provider_operator_ref=admission.ref,
            display_name=admission.display_name,
            description=admission.summary,
            parameter_schema=admission.parameter_schema,
            tags=admission.tags,
            source_digest=source_digest,
            suggested_category=admission.category,
            suggested_secondary_category=admission.secondary_category,
        )
        descriptors.append(descriptor)
        operator_id = f"datajuicer.{admission.ref}:1"
        spec = OperatorSpecVersion(
            id=operator_id,
            family_id=operator_id.rsplit(":", 1)[0],
            version=1,
            created_by="system",
            change_reason="admitted Data-Juicer provider proxy",
            display_name=admission.display_name,
            summary=admission.summary,
            description=(
                f"Versioned DataAgent proxy for Data-Juicer {admission.ref}; "
                "execution is isolated and source images remain immutable."
            ),
            primary_category=admission.category,
            secondary_category=admission.secondary_category,
            capability_tags=admission.tags,
            input_schema="ImageAssetRef",
            output_schema="ProviderDecision",
            parameter_schema=admission.parameter_schema,
            provider=ProviderRef(
                provider_id=provider.provider_id,
                provider_version=provider.provider_version,
                provider_operator_ref=admission.ref,
                source_digest=source_digest,
            ),
            implementation=ImplementationSpec(
                implementation_type=ImplementationType.EXTERNAL_SERVICE,
                entrypoint="dataagent.operators.providers.proxy:ProviderProxyOperator",
                dependency_lock_digest=hashlib.sha256(
                    "\n".join(admission.dependencies).encode("utf-8")
                ).hexdigest(),
            ),
            supported_runtime_profiles=(
                RuntimeProfile(backend=RuntimeBackend.CPU, memory_mb=1024),
            ),
            execution_scope=admission.execution_scope,
            implementation_ref="dataagent.operators.providers.proxy:ProviderProxyOperator",
            limitations=("Requires the isolated Data-Juicer provider environment.",),
            status=OperatorStatus.PERSONAL_RELEASE,
            owner_id="system",
            visibility="private",
        )
        operators.append(ProviderProxyOperator(spec, provider))
    admit = getattr(provider, "admit", None)
    if callable(admit):
        admit(descriptors)
    return tuple(operators)


__all__ = [
    "DATAJUICER_ADMISSIONS",
    "DataJuicerAdmission",
    "ProviderProxyOperator",
    "build_datajuicer_proxy_operators",
]
