from __future__ import annotations

import json
import sys

from PIL import Image

from dataagent.domain.operators import (
    ExecutionScope,
    OperatorCategory,
    RuntimeBackend,
)
from dataagent.domain.plans import CapabilityCoverageStatus
from dataagent.agents.processing.nodes import (
    _classification_contract,
    generate_pipeline_variants,
)
from dataagent.agents.requirement.nodes import generate_task_spec
from dataagent.agents.retrieval.nodes import generate_retrieval_plan
from dataagent.application.work_order_runtime import WorkOrderRuntime
from dataagent.domain.pipelines import PipelineVersion
from dataagent.domain.specs import ConstraintContract, DataSourceSpec, TaskSpecVersion
from dataagent.graph.interrupts import revise_task_spec_version
from dataagent.operators.providers import (
    DataJuicerProcessExecutor,
    DataJuicerOperatorProvider,
    ProviderDatasetExecuteRequest,
    ProviderDatasetItem,
    ProviderOperatorDescriptor,
    build_datajuicer_proxy_operators,
    normalize_provider_descriptor,
)
from dataagent.operators.protocol import OperatorContext, OperatorInput
from dataagent.operators import OperatorLibrary, OperatorRegistry, OperatorRuntime
from dataagent.operators.catalog_matching import DataJuicerCatalogMatcher
from dataagent.operators.catalog_ranking import (
    OperatorCandidateRanker,
    OperatorRankingPolicy,
)
from dataagent.operators.library import build_operator_library
from dataagent.operators.validation import validate_parameters


def test_revising_structured_task_does_not_restore_legacy_keyword_capabilities() -> None:
    spec = TaskSpecVersion(
        id="spec_structured_revision",
        version=1,
        created_by="user_1",
        change_reason="model-planned requirement",
        work_order_id="work_order_structured_revision",
        objective="保留至少检测到三辆车的图片",
        data_sources=(
            DataSourceSpec(type="local_directory", uri="D:/data/images"),
        ),
        constraints=(
            ConstraintContract(
                id="constraint_vehicle_count",
                source_text="至少检测到三辆车",
                scope="asset",
                field="image.vehicle_count",
                operator="gte",
                value=3,
                unit="count",
                required_evidence_type="detected_vehicle_count",
            ),
        ),
        semantic_requirements=("主体是道路场景",),
        output_actions=("filter", "manifest"),
    )

    revised = revise_task_spec_version(
        spec,
        patch={"preferences": {"output_layout": "flat"}},
        actor="user_1",
    )

    assert revised.capability_requirements == ()
    assert revised.required_capabilities == ()
    assert revised.output_actions == spec.output_actions


def _raw_vlm_descriptor() -> ProviderOperatorDescriptor:
    return ProviderOperatorDescriptor(
        provider_id="datajuicer",
        provider_version="1.5.3",
        provider_operator_ref="image_tagging_vlm_mapper",
        provider_operator_type="mapper",
        display_name="image_tagging_vlm_mapper",
        description="Generate image tags with a VLM.",
        parameter_schema={
            "type": "object",
            "properties": {
                "system_prompt": {"type": ["string", "null"], "default": None},
                "tag_field_name": {"type": "string", "default": "image_tags"},
            },
        },
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

    operators = build_datajuicer_proxy_operators(
        provider,
        [_raw_vlm_descriptor()],
        vision_model="configured-vision-model",
        vision_api_base_url="https://vision.example/v1",
    )
    variants = {
        item.spec.id: item.spec
        for item in operators
        if item.spec.provider.provider_operator_ref == "image_tagging_vlm_mapper"
    }

    assert set(variants) == {
        "datajuicer.image_tagging_vlm_mapper.remote_api:2",
        "datajuicer.image_tagging_vlm_mapper.local_cuda:1",
    }
    remote = variants["datajuicer.image_tagging_vlm_mapper.remote_api:2"]
    local = variants["datajuicer.image_tagging_vlm_mapper.local_cuda:1"]
    assert remote.version == 2
    assert local.version == 1
    assert remote.family_id == local.family_id == (
        "datajuicer.image_tagging_vlm_mapper"
    )
    assert remote.output_schema == local.output_schema == "ImageTagSet"
    assert {profile.backend for profile in remote.supported_runtime_profiles} == {
        RuntimeBackend.REMOTE
    }
    assert remote.parameter_schema["properties"]["is_api_model"] == {
        "type": "boolean",
        "default": True,
        "enum": [True],
    }
    assert remote.parameter_schema["properties"]["api_or_hf_model"] == {
        "type": "string",
        "default": "configured-vision-model",
        "enum": ["configured-vision-model"],
    }
    assert remote.parameter_schema["properties"]["api_endpoint"]["default"] == (
        "/chat/completions"
    )
    assert remote.parameter_schema["properties"]["model_params"]["default"] == {
        "base_url": "https://vision.example/v1"
    }
    assert remote.parameter_schema["properties"]["model_params"]["type"] == "object"
    assert remote.parameter_schema["properties"]["accelerator"]["default"] == "cpu"
    assert "remote" in remote.capability_tags
    assert "gpu" not in remote.capability_tags
    assert {profile.backend for profile in local.supported_runtime_profiles} == {
        RuntimeBackend.CUDA
    }
    assert local.parameter_schema["properties"]["is_api_model"] == {
        "type": "boolean",
        "default": False,
        "enum": [False],
    }
    assert "local_model" in local.capability_tags
    assert "api" not in local.capability_tags


def test_proxy_repairs_empty_container_types_from_cached_discovery_schema() -> None:
    raw = _raw_vlm_descriptor().model_copy(
        update={
            "parameter_schema": {
                "type": "object",
                "properties": {
                    "is_api_model": {"type": "boolean", "default": False},
                    "api_or_hf_model": {"type": "string", "default": "local-model"},
                    "api_endpoint": {
                        "type": ["string", "null"],
                        "default": None,
                    },
                    "model_params": {"type": [], "default": {}},
                    "sampling_params": {"type": [], "default": {}},
                },
                "additionalProperties": False,
            }
        }
    )
    provider = DataJuicerOperatorProvider(provider_version="1.5.3")

    remote = next(
        item.spec
        for item in build_datajuicer_proxy_operators(provider, [raw])
        if item.spec.id == "datajuicer.image_tagging_vlm_mapper.remote_api:2"
    )

    properties = remote.parameter_schema["properties"]
    assert properties["model_params"]["type"] == "object"
    assert properties["sampling_params"]["type"] == "object"
    normalized = validate_parameters(remote.parameter_schema, {})
    assert normalized["model_params"]["base_url"]
    assert normalized["sampling_params"] == {
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    validation = provider.validate(
        "image_tagging_vlm_mapper",
        normalized,
        RuntimeBackend.REMOTE,
    )
    assert validation.ok is True
    assert validation.normalized_parameters["accelerator"] == "cpu"


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

    assert "datajuicer.image_tagging_vlm_mapper.remote_api:2" in by_id
    assert "datajuicer.image_tagging_vlm_mapper.local_cuda:1" in by_id
    assert "semantic" in by_id[
        "datajuicer.image_tagging_vlm_mapper.remote_api:2"
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
        == "datajuicer.image_tagging_vlm_mapper.remote_api:2"
        and item.capability == "image_classification"
    )

    assert {"keyword", "semantic"}.issubset(remote.recall_sources)
    assert remote.keyword_score > 0
    assert remote.semantic_score > 0


def test_ranker_prefers_available_remote_vlm_over_unavailable_cuda() -> None:
    base = build_operator_library(include_datajuicer=False)
    provider = DataJuicerOperatorProvider(provider_version="1.5.3")
    proxies = build_datajuicer_proxy_operators(provider, [_raw_vlm_descriptor()])
    operators = (*base.operators, *proxies)
    registry = OperatorRegistry(item.spec for item in operators)
    recalled = DataJuicerCatalogMatcher(registry).match(
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

    ranked = OperatorCandidateRanker(registry).rank(
        recalled,
        policy=OperatorRankingPolicy(
            available_runtime_backends=frozenset(
                {RuntimeBackend.CPU, RuntimeBackend.REMOTE}
            ),
            allow_draft_candidates=True,
        ),
    )
    remote = next(
        item
        for item in ranked
        if item.operator_version_id
        == "datajuicer.image_tagging_vlm_mapper.remote_api:2"
        and item.capability == "image_classification"
    )
    local = next(
        item
        for item in ranked
        if item.operator_version_id
        == "datajuicer.image_tagging_vlm_mapper.local_cuda:1"
        and item.capability == "image_classification"
    )

    assert remote.executable is True
    assert remote.runtime_score > 0
    assert remote.io_score > 0
    assert remote.cost_tier == "metered"
    assert local.executable is False
    assert local.runtime_score < 0
    assert ranked.index(remote) < ranked.index(local)


def test_remote_vlm_variant_validates_against_normalized_runtime_view() -> None:
    provider = DataJuicerOperatorProvider(provider_version="1.5.3")
    operators = build_datajuicer_proxy_operators(provider, [_raw_vlm_descriptor()])
    remote = next(
        item.spec
        for item in operators
        if item.spec.id == "datajuicer.image_tagging_vlm_mapper.remote_api:2"
    )
    parameters = validate_parameters(remote.parameter_schema, {})

    validation = provider.validate(
        "image_tagging_vlm_mapper",
        parameters,
        RuntimeBackend.REMOTE,
    )

    assert validation.ok is True
    assert validation.normalized_parameters["is_api_model"] is True
    assert validation.normalized_parameters["api_or_hf_model"] == "qwen3.7-plus"
    assert validation.normalized_parameters["api_endpoint"] == "/chat/completions"
    assert validation.normalized_parameters["model_params"] == {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
    }
    assert validation.normalized_parameters["accelerator"] == "cpu"


def test_remote_executor_injects_api_key_without_writing_it_to_recipe(
    tmp_path, monkeypatch
) -> None:
    fake_process = tmp_path / "fake_remote_process.py"
    fake_process.write_text(
        """
import json
import os
import sys
from pathlib import Path

recipe = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
source = Path(recipe["dataset"]["configs"][0]["path"])
row = json.loads(source.read_text(encoding="utf-8"))
row["remote_key_present"] = bool(os.environ.get("OPENAI_API_KEY"))
Path(recipe["export_path"]).write_text(json.dumps(row) + "\\n", encoding="utf-8")
Path(recipe["export_path"]).with_name("output_stats.jsonl").write_text(
    json.dumps({"__dj__meta__": {"image_tags": [["cat"]]}}) + "\\n",
    encoding="utf-8",
)
""".strip(),
        encoding="utf-8",
    )
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 16), (10, 20, 30)).save(source)
    monkeypatch.setenv("BAILIAN_API_KEY", "test-secret")
    executor = DataJuicerProcessExecutor(
        (sys.executable, fake_process),
        runtime_root=tmp_path / "runtime",
        timeout_seconds=10,
    )

    result = executor.execute_dataset(
        ProviderDatasetExecuteRequest(
            provider_operator_ref="image_tagging_vlm_mapper",
            runtime_backend=RuntimeBackend.REMOTE,
            context=OperatorContext(
                run_id="run_remote",
                work_order_id="work_order_1",
                owner_id="user_1",
            ),
            items=(
                ProviderDatasetItem(
                    asset_id="asset_1",
                    input_data=OperatorInput(
                        source_path=str(source),
                        current_path=str(source),
                        labels={
                            "datajuicer_output": {
                                "authenticity_tags": [["authentic"]]
                            }
                        },
                    ),
                ),
            ),
            parameters={"is_api_model": True},
        )
    )

    assert result.ok is True
    output = result.items[0].result.labels["datajuicer_output"]
    assert output["remote_key_present"] is True
    assert output["image_tags"] == ["cat"]
    assert output["image_tags__provider_raw"] == [["cat"]]
    assert output["_dataagent_output_contract"] == "image_tag_set:1"
    assert output["authenticity_tags"] == [["authentic"]]
    recipe_text = next((tmp_path / "runtime").rglob("recipe.yaml")).read_text(
        encoding="utf-8"
    )
    assert "test-secret" not in recipe_text


def test_remote_vlm_rejects_empty_semantic_stats(tmp_path, monkeypatch) -> None:
    fake_process = tmp_path / "fake_empty_vlm.py"
    fake_process.write_text(
        """
import json
import sys
from pathlib import Path

recipe = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
source = Path(recipe["dataset"]["configs"][0]["path"])
row = json.loads(source.read_text(encoding="utf-8"))
output = Path(recipe["export_path"])
output.write_text(json.dumps(row) + "\\n", encoding="utf-8")
output.with_name("output_stats.jsonl").write_text(
    json.dumps({"__dj__meta__": {"image_tags": [[]]}}) + "\\n",
    encoding="utf-8",
)
""".strip(),
        encoding="utf-8",
    )
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 16), (10, 20, 30)).save(source)
    monkeypatch.setenv("BAILIAN_API_KEY", "test-secret")
    executor = DataJuicerProcessExecutor(
        (sys.executable, fake_process),
        runtime_root=tmp_path / "runtime",
        timeout_seconds=10,
    )

    result = executor.execute_dataset(
        ProviderDatasetExecuteRequest(
            provider_operator_ref="image_tagging_vlm_mapper",
            runtime_backend=RuntimeBackend.REMOTE,
            context=OperatorContext(
                run_id="run_empty",
                work_order_id="work_order_1",
                owner_id="user_1",
            ),
            items=(
                ProviderDatasetItem(
                    asset_id="asset_1",
                    input_data=OperatorInput(
                        source_path=str(source),
                        current_path=str(source),
                    ),
                ),
            ),
            parameters={"is_api_model": True, "tag_field_name": "image_tags"},
        )
    )

    assert result.ok is False
    assert result.error_type == "provider_output_contract_violation"
    assert "1/1 assets" in result.message


def test_retrieval_outputs_capability_coverage_matrix_for_cat_dog_task() -> None:
    base = build_operator_library(include_datajuicer=False)
    provider = DataJuicerOperatorProvider(provider_version="1.5.3")
    proxies = build_datajuicer_proxy_operators(provider, [_raw_vlm_descriptor()])
    operators = (*base.operators, *proxies)
    registry = OperatorRegistry(item.spec for item in operators)
    requirement = "去掉里面不真实、不清晰的图片，把猫和狗的图片分开"
    task_result = generate_task_spec(
        {
            "work_order_id": "work_order_1",
            "owner_id": "user_1",
            "requirement": requirement,
            "data_sources": [
                {
                    "type": "local_directory",
                    "uri": "D:/images",
                    "mapping": {},
                }
            ],
            "trace": [],
        }
    )
    task_spec = {
        **task_result["task_spec"],
        "classification": {
            "mode": "closed_set",
            "labels": [
                {
                    "id": "cat",
                    "display_name": "猫",
                    "aliases": ["猫咪", "小猫"],
                },
                {
                    "id": "dog",
                    "display_name": "狗",
                    "aliases": ["狗狗", "小狗"],
                },
            ],
            "mixed_label": "mixed",
            "unknown_label": "unknown",
        },
    }

    retrieval = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": task_spec,
            "trace": [],
        },
        operator_registry=registry,
        allow_draft_candidates=True,
        available_runtime_backends=frozenset(
            {RuntimeBackend.CPU, RuntimeBackend.REMOTE}
        ),
    )
    coverage = {
        item["capability"]: item for item in retrieval["capability_coverage"]
    }

    assert coverage["image_decode"]["status"] == CapabilityCoverageStatus.COVERED
    assert coverage["image_quality"]["status"] == CapabilityCoverageStatus.COVERED
    assert coverage["image_classification"]["status"] == (
        CapabilityCoverageStatus.COVERED
    )
    assert coverage["image_classification"]["selected_operator_version_id"] == (
        "native.remote_vlm:1"
    )
    classification_candidates = {
        item["operator_version_id"]
        for item in coverage["image_classification"]["candidates"]
    }
    assert "datajuicer.image_tagging_vlm_mapper.remote_api:2" in (
        classification_candidates
    )
    assert coverage["authenticity_assessment"]["status"] == (
        CapabilityCoverageStatus.COVERED
    )
    assert coverage["authenticity_assessment"]["selected_operator_version_id"] == (
        "builtin.authenticity_decision:1"
    )
    assert coverage["class_resolution"]["status"] == CapabilityCoverageStatus.COVERED
    assert coverage["dataset_partition"]["status"] == CapabilityCoverageStatus.COVERED
    assert coverage["manifest"]["status"] == CapabilityCoverageStatus.COVERED
    assert retrieval["retrieval_plan"]["sufficient"] is True

    state = {
        "owner_id": "user_1",
        "task_spec": task_spec,
        "trace": [],
        **retrieval,
    }
    library = OperatorLibrary(
        operators=operators,
        registry=registry,
        runtime=OperatorRuntime(operators),
        providers=base.providers,
    )
    pipelines = [
        PipelineVersion.model_validate(item)
        for item in generate_pipeline_variants(
            state, operator_library=library
        )["pipeline_variants"]
    ]

    assert len(pipelines) == 3
    assert all(
        [node.id for node in pipeline.nodes]
        == [
            "ingest",
            "quality_filter",
            "visual_tagging",
            "authenticity_decision",
            "class_resolution",
            "dataset_partition",
            "manifest",
        ]
        for pipeline in pipelines
    )
    assert all(
        all(node.runtime_backend != RuntimeBackend.MOCK for node in pipeline.nodes)
        for pipeline in pipelines
    )
    policies = {
        pipeline.strategy.value: next(
            node.parameters["uncertain_policy"]
            for node in pipeline.nodes
            if node.id == "authenticity_decision"
        )
        for pipeline in pipelines
    }
    assert policies == {
        "retention_first": "keep",
        "balanced": "review",
        "quality_first": "reject",
    }
    balanced = next(
        pipeline
        for pipeline in pipelines
        if pipeline.strategy.value == "balanced"
    )
    remote_nodes = [
        node
        for node in balanced.nodes
        if node.operator_version_id == "native.remote_vlm:1"
    ]
    assert len(remote_nodes) == 1
    assert all(
        '{"tags":[' in node.parameters["system_prompt"]
        and "strict JSON only" in node.parameters["system_prompt"]
        for node in remote_nodes
    )
    assert remote_nodes[0].parameters["tag_field_name"] == "visual_tags"
    assert "authentic" in remote_nodes[0].parameters["allowed_tags"]
    assert "cat" in remote_nodes[0].parameters["allowed_tags"]
    assert remote_nodes[0].prompt_binding.template_id == "image-task-visual-tagging"
    assert remote_nodes[0].prompt_binding.template_version == 3
    assert "authenticity" in remote_nodes[0].parameters["system_prompt"]
    assert "cat, dog, mixed, unknown" in remote_nodes[0].parameters["system_prompt"]
    assert all(node.prompt_binding.template_sha256 for node in remote_nodes)
    assert all(node.prompt_binding.resolved_sha256 for node in remote_nodes)

    base.providers.register(provider)
    runtime = WorkOrderRuntime(include_datajuicer=False)
    runtime.operator_library = OperatorLibrary(
        operators=operators,
        registry=registry,
        runtime=OperatorRuntime(operators),
        providers=base.providers,
    )
    runtime.operator_registry = registry
    eligibility = runtime.pipeline_execution_eligibility(balanced)
    assert eligibility == {"eligible": True, "violations": []}


def test_visual_semantic_selection_compiles_remote_vlm_and_policy_node() -> None:
    base = build_operator_library(include_datajuicer=False)
    provider = DataJuicerOperatorProvider(provider_version="1.5.3")
    proxies = build_datajuicer_proxy_operators(provider, [_raw_vlm_descriptor()])
    operators = (*base.operators, *proxies)
    registry = OperatorRegistry(item.spec for item in operators)
    library = OperatorLibrary(
        operators=operators,
        registry=registry,
        runtime=OperatorRuntime(operators),
        providers=base.providers,
    )
    initial = TaskSpecVersion(
        id="spec_black_clothing",
        version=1,
        created_by="user_1",
        change_reason="model-planned requirement",
        work_order_id="work_order_black_clothing",
        objective="筛选出主体穿黑色衣服的图片",
        data_sources=(
            DataSourceSpec(type="local_directory", uri="D:/images"),
        ),
        constraints=(
            ConstraintContract(
                id="constraint_black_clothing",
                source_text="主体穿黑色衣服",
                scope="asset",
                field="image.subject_clothing_color",
                operator="eq",
                value="black",
                unit="label",
                required_evidence_type="visual_semantic_judgment",
            ),
        ),
        semantic_requirements=("主体可见衣物的主要颜色为黑色",),
    )
    revised = revise_task_spec_version(
        initial,
        patch={
            "classification": {
                "mode": "closed_set",
                "labels": [
                    {
                        "id": "black_clothing",
                        "display_name": "穿了黑色衣服",
                        "aliases": ["穿黑色衣服", "黑色衣物"],
                    },
                    {
                        "id": "not_black_clothing",
                        "display_name": "未穿黑色衣服",
                        "aliases": ["没穿黑色衣服", "非黑色衣物"],
                    },
                ],
                "mixed_label": "mixed",
                "unknown_label": "unknown",
            },
            "exclusion_requirements": ["排除不属于上述任一类别的图片"],
        },
        actor="user_1",
    )

    assert revised.capability_requirements == ()
    assert revised.output_actions == ("filter", "manifest")

    retrieval = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": revised.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=registry,
        allow_draft_candidates=True,
        available_runtime_backends=frozenset(
            {RuntimeBackend.CPU, RuntimeBackend.REMOTE}
        ),
    )
    coverage = {
        item["capability"]: item for item in retrieval["capability_coverage"]
    }
    assert coverage["visual_semantic_selection"]["status"] == (
        CapabilityCoverageStatus.COVERED
    )
    assert coverage["visual_semantic_selection"][
        "selected_operator_version_id"
    ] == "builtin.visual_semantic_selection:1"

    pipelines = [
        PipelineVersion.model_validate(item)
        for item in generate_pipeline_variants(
            {
                "owner_id": "user_1",
                "task_spec": revised.model_dump(mode="json"),
                "trace": [],
                **retrieval,
            },
            operator_library=library,
        )["pipeline_variants"]
    ]

    assert all(
        [node.id for node in pipeline.nodes]
        == ["ingest", "visual_tagging", "visual_semantic_selection", "manifest"]
        for pipeline in pipelines
    )
    visual_node = pipelines[0].nodes[1]
    assert visual_node.operator_version_id == "native.remote_vlm:1"
    assert visual_node.prompt_binding is not None
    assert visual_node.prompt_binding.template_id == "image-semantic-selection"
    assert visual_node.prompt_binding.template_version == 2
    assert "筛选出里面穿了黑色衣服的图片" not in visual_node.parameters[
        "system_prompt"
    ]
    assert "主体可见衣物的主要颜色为黑色" in visual_node.parameters["system_prompt"]
    assert '"black_clothing"' in visual_node.parameters["system_prompt"]
    assert "Required visual conditions: none" not in visual_node.parameters[
        "system_prompt"
    ]

    base.providers.register(provider)
    runtime = WorkOrderRuntime(include_datajuicer=False)
    runtime.operator_library = library
    runtime.operator_registry = registry
    assert runtime.pipeline_execution_eligibility(pipelines[0]) == {
        "eligible": True,
        "violations": [],
    }
    legacy_nodes = tuple(
        node.model_copy(
            update={
                "prompt_binding": node.prompt_binding.model_copy(
                    update={"template_version": 1}
                )
            }
        )
        if node.prompt_binding is not None
        else node
        for node in pipelines[0].nodes
    )
    legacy = pipelines[0].model_copy(update={"nodes": legacy_nodes})

    eligibility = runtime.pipeline_execution_eligibility(legacy)

    assert eligibility["eligible"] is False
    assert "retained for audit only" in eligibility["violations"][0]


def test_missing_classification_spec_does_not_invent_task_labels() -> None:
    spec = TaskSpecVersion(
        id="spec_visual_filter",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_visual_filter",
        objective="Select images containing people wearing black clothing.",
        data_sources=(DataSourceSpec(type="local_directory", uri="D:/images"),),
        confirmed=True,
    )

    assert _classification_contract(spec) == {
        "labels": [],
        "mixed_label": None,
        "unknown_label": None,
    }
