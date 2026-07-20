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
from dataagent.operators import OperatorLibrary, OperatorRegistry, OperatorRuntime
from dataagent.operators.catalog_matching import DataJuicerCatalogMatcher
from dataagent.operators.library import build_operator_library


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


def test_vlm_descriptor_expands_to_remote_and_local_versioned_variants() -> None:
    provider = DataJuicerOperatorProvider(provider_version="1.5.3")

    operators = build_datajuicer_proxy_operators(provider, [_raw_vlm_descriptor()])
    variants = {
        item.spec.id: item.spec
        for item in operators
        if item.spec.provider.provider_operator_ref == "image_tagging_vlm_mapper"
    }

    assert set(variants) == {
        "datajuicer.image_tagging_vlm_mapper.remote_api:1",
        "datajuicer.image_tagging_vlm_mapper.local_cuda:1",
    }
    remote = variants["datajuicer.image_tagging_vlm_mapper.remote_api:1"]
    local = variants["datajuicer.image_tagging_vlm_mapper.local_cuda:1"]
    assert remote.family_id == local.family_id == (
        "datajuicer.image_tagging_vlm_mapper"
    )
    assert remote.output_schema == local.output_schema == "ImageTagSet"
    assert {profile.backend for profile in remote.supported_runtime_profiles} == {
        RuntimeBackend.REMOTE
    }
    assert remote.parameter_schema["properties"]["is_api_model"] == {
        "default": True,
        "enum": [True],
    }
    assert remote.parameter_schema["properties"]["api_or_hf_model"] == {
        "default": "qwen3.7-plus",
        "enum": ["qwen3.7-plus"],
    }
    assert "remote" in remote.capability_tags
    assert "gpu" not in remote.capability_tags
    assert {profile.backend for profile in local.supported_runtime_profiles} == {
        RuntimeBackend.CUDA
    }
    assert local.parameter_schema["properties"]["is_api_model"] == {
        "default": False,
        "enum": [False],
    }
    assert "local_model" in local.capability_tags
    assert "api" not in local.capability_tags


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


def test_hybrid_recall_finds_vlm_variants_without_hardcoded_provider_ref() -> None:
    base = build_operator_library(include_datajuicer=False)
    provider = DataJuicerOperatorProvider(provider_version="1.5.3")
    proxies = build_datajuicer_proxy_operators(provider, [_raw_vlm_descriptor()])
    operators = (*base.operators, *proxies)
    library = OperatorLibrary(
        operators=operators,
        registry=OperatorRegistry(item.spec for item in operators),
        runtime=OperatorRuntime(operators),
        providers=base.providers,
    )

    matches = DataJuicerCatalogMatcher(library.registry).match(
        "把猫和狗的图片分开",
        capability_requirements=(
            {
                "id": "image_classification",
                "capability": "image_classification",
                "description": "Classify cats and dogs.",
                "depends_on": (),
            },
        ),
    )
    by_id = {item.operator_version_id: item for item in matches}

    assert "datajuicer.image_tagging_vlm_mapper.remote_api:1" in by_id
    assert "datajuicer.image_tagging_vlm_mapper.local_cuda:1" in by_id
    assert "semantic" in by_id[
        "datajuicer.image_tagging_vlm_mapper.remote_api:1"
    ].recall_sources


def test_hybrid_recall_records_rule_keyword_and_semantic_evidence() -> None:
    base = build_operator_library(include_datajuicer=False)
    provider = DataJuicerOperatorProvider(provider_version="1.5.3")
    proxies = build_datajuicer_proxy_operators(provider, [_raw_vlm_descriptor()])
    operators = (*base.operators, *proxies)
    registry = OperatorRegistry(item.spec for item in operators)

    matches = DataJuicerCatalogMatcher(registry).match(
        "使用 image_tagging_vlm_mapper 给图片分类",
        capability_requirements=(
            {
                "id": "image_classification",
                "capability": "image_classification",
                "description": "Classify images.",
                "depends_on": (),
            },
        ),
    )
    remote = next(
        item
        for item in matches
        if item.operator_version_id
        == "datajuicer.image_tagging_vlm_mapper.remote_api:1"
        and item.capability == "image_classification"
    )

    assert {"keyword", "semantic"}.issubset(remote.recall_sources)
    assert remote.keyword_score > 0
    assert remote.semantic_score > 0
