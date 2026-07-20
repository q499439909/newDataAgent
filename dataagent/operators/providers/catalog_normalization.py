from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ...domain.operators import ExecutionScope, OperatorCategory
from .protocol import ProviderOperatorDescriptor


@dataclass(frozen=True)
class ProviderCatalogOverlay:
    id: str
    provider_id: str
    provider_operator_ref: str
    compatible_versions: frozenset[str]
    add_tags: frozenset[str] = frozenset()
    category: OperatorCategory | None = None
    secondary_category: str | None = None
    execution_scope: ExecutionScope | None = None

    def applies_to(self, descriptor: ProviderOperatorDescriptor) -> bool:
        return (
            descriptor.provider_id == self.provider_id
            and descriptor.provider_operator_ref == self.provider_operator_ref
            and descriptor.provider_version in self.compatible_versions
        )


@dataclass(frozen=True)
class NormalizedProviderDescriptor:
    raw: ProviderOperatorDescriptor
    descriptor: ProviderOperatorDescriptor
    overlay_ids: tuple[str, ...]
    normalization_digest: str


DATAJUICER_CATALOG_OVERLAYS = (
    ProviderCatalogOverlay(
        id="datajuicer.image_tagging_vlm_mapper.metadata:1",
        provider_id="datajuicer",
        provider_operator_ref="image_tagging_vlm_mapper",
        compatible_versions=frozenset({"1.5.3"}),
        add_tags=frozenset(
            {
                "image",
                "image_classification",
                "image_tagging",
                "visual_understanding",
            }
        ),
        category=OperatorCategory.UNDERSTANDING,
        secondary_category="classification",
        execution_scope=ExecutionScope.ASSET,
    ),
)


def _normalization_digest(
    descriptor: ProviderOperatorDescriptor,
    overlays: tuple[ProviderCatalogOverlay, ...],
) -> str:
    payload = {
        "provider_id": descriptor.provider_id,
        "provider_version": descriptor.provider_version,
        "provider_operator_ref": descriptor.provider_operator_ref,
        "raw_source_digest": descriptor.source_digest,
        "overlays": [
            {
                "id": overlay.id,
                "add_tags": sorted(overlay.add_tags),
                "category": overlay.category,
                "secondary_category": overlay.secondary_category,
                "execution_scope": overlay.execution_scope,
            }
            for overlay in overlays
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def normalize_provider_descriptor(
    descriptor: ProviderOperatorDescriptor,
    *,
    overlays: tuple[ProviderCatalogOverlay, ...] = DATAJUICER_CATALOG_OVERLAYS,
) -> NormalizedProviderDescriptor:
    applicable = tuple(overlay for overlay in overlays if overlay.applies_to(descriptor))
    tags = set(descriptor.tags)
    category = descriptor.suggested_category
    secondary = descriptor.suggested_secondary_category
    execution_scope = descriptor.suggested_execution_scope
    for overlay in applicable:
        tags.update(overlay.add_tags)
        category = overlay.category or category
        secondary = overlay.secondary_category or secondary
        execution_scope = overlay.execution_scope or execution_scope
    normalized = descriptor.model_copy(
        update={
            "tags": frozenset(tags),
            "suggested_category": category,
            "suggested_secondary_category": secondary,
            "suggested_execution_scope": execution_scope,
        }
    )
    return NormalizedProviderDescriptor(
        raw=descriptor,
        descriptor=normalized,
        overlay_ids=tuple(overlay.id for overlay in applicable),
        normalization_digest=_normalization_digest(descriptor, applicable),
    )


def normalize_provider_catalog(
    descriptors: list[ProviderOperatorDescriptor] | tuple[ProviderOperatorDescriptor, ...],
    *,
    overlays: tuple[ProviderCatalogOverlay, ...] = DATAJUICER_CATALOG_OVERLAYS,
) -> tuple[NormalizedProviderDescriptor, ...]:
    return tuple(
        normalize_provider_descriptor(descriptor, overlays=overlays)
        for descriptor in descriptors
    )


__all__ = [
    "DATAJUICER_CATALOG_OVERLAYS",
    "NormalizedProviderDescriptor",
    "ProviderCatalogOverlay",
    "normalize_provider_catalog",
    "normalize_provider_descriptor",
]
