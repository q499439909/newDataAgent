from __future__ import annotations

import json

from dataagent.domain.operators import (
    ExecutionScope,
    OperatorCategory,
    RuntimeBackend,
)
from dataagent.operators.providers import (
    DataJuicerOperatorProvider,
    ProviderOperatorDescriptor,
    build_datajuicer_proxy_operators,
    normalize_provider_descriptor,
)


def _raw_vlm_descriptor() -> ProviderOperatorDescriptor:
    return ProviderOperatorDescriptor(
        provider_id="datajuicer",
        provider_version="1.5.3",
        provider_operator_ref="image_tagging_vlm_mapper",
        provider_operator_type="mapper",
        display_name="image_tagging_vlm_mapper",
        description="Generate image tags with a VLM.",
        parameter_schema={"type": "object", "properties": {}},
        tags=frozenset({"api", "gpu", "multimodal", "vllm"}),
        source_digest="raw-source-digest",
        suggested_category=OperatorCategory.UNDERSTANDING,
        suggested_secondary_category="object_detection",
        suggested_execution_scope=ExecutionScope.ASSET,
        supported_runtime_backends=(RuntimeBackend.CUDA,),
    )


def test_catalog_overlay_normalizes_without_mutating_raw_descriptor() -> None:
    raw = _raw_vlm_descriptor()

    result = normalize_provider_descriptor(raw)

    assert "image" not in raw.tags
    assert raw.suggested_secondary_category == "object_detection"
    assert result.raw is raw
    assert result.descriptor.source_digest == raw.source_digest
    assert result.descriptor.tags >= {
        "image",
        "image_classification",
        "image_tagging",
        "visual_understanding",
    }
    assert result.descriptor.suggested_secondary_category == "classification"
    assert result.descriptor.supported_runtime_backends == (RuntimeBackend.CUDA,)
    assert result.overlay_ids == (
        "datajuicer.image_tagging_vlm_mapper.metadata:1",
    )
    assert len(result.normalization_digest) == 64


def test_proxy_records_normalization_provenance() -> None:
    provider = DataJuicerOperatorProvider(provider_version="1.5.3")

    operators = build_datajuicer_proxy_operators(provider, [_raw_vlm_descriptor()])
    proxy = next(
        item
        for item in operators
        if item.spec.provider.provider_operator_ref == "image_tagging_vlm_mapper"
    )

    assert proxy.spec.input_schema == "ImageAssetRef"
    assert "image" in proxy.spec.capability_tags
    provenance = proxy.spec.resource_requirements["catalog_normalization"]
    assert provenance["raw_source_digest"] == "raw-source-digest"
    assert provenance["overlay_ids"] == [
        "datajuicer.image_tagging_vlm_mapper.metadata:1"
    ]


def test_normalization_does_not_rewrite_discovery_cache(tmp_path) -> None:
    cache_path = tmp_path / "catalog.json"

    class Searcher:
        def search(self):
            return [
                {
                    "name": "image_tagging_vlm_mapper",
                    "desc": "Generate image tags with a VLM.",
                    "type": "mapper",
                    "tags": ["api", "gpu", "multimodal", "vllm"],
                    "parameter_schema": {"type": "object", "properties": {}},
                }
            ]

    provider = DataJuicerOperatorProvider(
        searcher_factory=Searcher,
        provider_version="1.5.3",
        catalog_cache_path=cache_path,
    )
    raw = provider.discover()[0]
    normalized = normalize_provider_descriptor(raw)
    persisted = json.loads(cache_path.read_text(encoding="utf-8"))["operators"][0]

    assert "image" not in raw.tags
    assert "image" in normalized.descriptor.tags
    assert "image" not in persisted["tags"]
