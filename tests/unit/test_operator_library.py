from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import sys

import pytest
from PIL import Image

from dataagent.agents.processing.nodes import generate_pipeline_variants
from dataagent.agents.requirement.nodes import generate_task_spec
from dataagent.agents.retrieval.nodes import generate_retrieval_plan
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.domain.common import new_id
from dataagent.domain.operators import (
    ExecutionScope,
    OperatorCategory,
    OperatorStatus,
    RuntimeBackend,
)
from dataagent.domain.pipelines import PipelineNode, PipelineStrategy, PipelineVersion
from dataagent.domain.specs import ConstraintContract, DataSourceSpec, TaskSpecVersion
from dataagent.operators import (
    OperatorLibrary,
    OperatorRegistry,
    OperatorRuntime,
    build_operator_library,
)
from dataagent.operators.catalog_matching import DataJuicerCatalogMatcher
from dataagent.operators.planning import (
    CapabilityGapError,
    OperatorRequirement,
    OperatorSelector,
    decompose_task_capabilities,
    exclude_task_capabilities,
    infer_required_capabilities,
)
from dataagent.operators.protocol import OperatorContext, OperatorInput, OperatorResult
from dataagent.operators.providers import DataJuicerOperatorProvider
from dataagent.operators.providers import DataJuicerProcessExecutor
from dataagent.operators.providers import ProviderDatasetExecuteRequest
from dataagent.operators.providers import ProviderDatasetItem
from dataagent.operators.providers import ProviderExecuteRequest
from dataagent.operators.providers import build_datajuicer_proxy_operators
from dataagent.operators.validation import ParameterValidationError, validate_parameters


def test_parameter_validation_applies_defaults_and_rejects_invalid_values() -> None:
    schema = {
        "type": "object",
        "properties": {
            "threshold": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "default": 0.5,
            }
        },
        "additionalProperties": False,
    }

    assert validate_parameters(schema, {}) == {"threshold": 0.5}
    with pytest.raises(ParameterValidationError, match="must be <= 1"):
        validate_parameters(schema, {"threshold": 2})
    with pytest.raises(ParameterValidationError, match="unknown fields"):
        validate_parameters(schema, {"unexpected": True})


def test_capability_search_is_bilingual_and_does_not_fallback() -> None:
    assert infer_required_capabilities("筛选美学评分高且没有水印的人像") == (
        "aesthetic_score",
        "watermark_detection",
    )
    assert infer_required_capabilities("segment images and match face identity") == (
        "segmentation",
        "face_identity",
    )


def test_disabled_capability_is_removed_and_dependency_chain_is_reconnected() -> None:
    capabilities = decompose_task_capabilities(
        "去掉不真实、不清晰的图片，把猫和狗分开"
    )

    revised = exclude_task_capabilities(capabilities, {"image_quality"})
    by_id = {item.id: item for item in revised}

    assert "image_quality" not in by_id
    assert by_id["authenticity_assessment"].depends_on == ("image_decode",)
    assert by_id["image_classification"].depends_on == (
        "authenticity_assessment",
    )

    selector = OperatorSelector(
        build_operator_library(include_datajuicer=False).registry
    )
    with pytest.raises(CapabilityGapError):
        selector.select(OperatorRequirement(capability="not_a_real_capability"))


def test_native_library_has_a_runnable_reference_for_every_category() -> None:
    library = build_operator_library(include_datajuicer=False)

    assert set(library.registry.categories()) == set(OperatorCategory)
    assert all(count >= 1 for count in library.registry.categories().values())


def test_native_enhancement_creates_a_derivative_without_mutating_source(tmp_path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (32, 24), (80, 100, 120)).save(source)
    original = source.read_bytes()
    library = build_operator_library(include_datajuicer=False)
    context = OperatorContext(
        run_id="development_1",
        work_order_id="work_order_1",
        owner_id="user_1",
        purpose="development",
        shared={"artifact_root": tmp_path / "artifacts"},
    )

    result = library.runtime.execute(
        operator_version_id="builtin.auto_contrast:1",
        context=context,
        input_data=OperatorInput(
            source_path=str(source),
            current_path=str(source),
        ),
        parameters={},
    )

    assert source.read_bytes() == original
    assert Path(result.output_path or "").is_file()
    assert result.artifacts[0].sha256
    with pytest.raises(ParameterValidationError, match="unknown fields"):
        library.runtime.execute(
            operator_version_id="builtin.quality_filter:1",
            context=context,
            input_data=OperatorInput(
                source_path=str(source),
                current_path=str(source),
            ),
            parameters={"not_supported": True},
        )


def test_processing_requires_coverage_instead_of_mock_capability_nodes(tmp_path) -> None:
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="segment images",
        data_sources=(DataSourceSpec(type="local_directory", uri=str(tmp_path)),),
        required_capabilities=("segmentation",),
        confirmed=True,
    )

    with pytest.raises(ValueError, match="Capability coverage is required"):
        generate_pipeline_variants(
            {
                "owner_id": "user_1",
                "task_spec": spec.model_dump(mode="json"),
                "trace": [],
            }
        )


def test_mock_model_operator_runs_in_preview_but_not_production() -> None:
    library = build_operator_library(include_datajuicer=False)
    input_data = OperatorInput(source_path="image.jpg", current_path="image.jpg")
    preview_context = OperatorContext(
        run_id="preview_1",
        work_order_id="work_order_1",
        owner_id="user_1",
        purpose="preview",
    )

    result = library.runtime.execute(
        operator_version_id="model.segmentation:1",
        context=preview_context,
        input_data=input_data,
        parameters={},
        runtime_backend=RuntimeBackend.MOCK,
    )

    assert result.annotations[0].annotation_type == "mask"
    assert result.confidence == 0
    production_context = preview_context.model_copy(update={"purpose": "production"})
    with pytest.raises(PermissionError, match="Mock operators cannot execute"):
        library.runtime.execute(
            operator_version_id="model.segmentation:1",
            context=production_context,
            input_data=input_data,
            parameters={},
            runtime_backend=RuntimeBackend.MOCK,
        )


class _FakeSearcher:
    calls = 0

    @staticmethod
    def _operator(threshold: float = 0.5, enabled: bool = True) -> None:
        return None

    def search(self):
        type(self).calls += 1
        return [
            {
                "name": "image_aesthetic_filter",
                "desc": "Scores image aesthetics",
                "type": "filter",
                "tags": ["cpu", "image", "aesthetic"],
                "sig": inspect.signature(self._operator),
                "param_desc_map": {"threshold": "Minimum score"},
            }
        ]


class _MixedCatalogSearcher:
    def search(self):
        return [
            {
                "name": "image_segment_mapper",
                "desc": "Segments images with a model",
                "type": "mapper",
                "tags": ["gpu", "image", "model"],
                "parameter_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "text_length_filter",
                "desc": "Filters text records by length",
                "type": "filter",
                "tags": ["cpu", "text"],
                "parameter_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        ]


class _PlanningCatalogSearcher:
    def search(self):
        return [
            {
                "name": "image_shape_filter",
                "desc": "Filters images by width and height",
                "type": "filter",
                "tags": ["cpu", "image"],
                "parameter_schema": {
                    "type": "object",
                    "properties": {
                        "min_width": {"type": "integer", "default": 1},
                        "max_width": {"type": "integer", "default": 999999},
                        "min_height": {"type": "integer", "default": 1},
                        "max_height": {"type": "integer", "default": 999999},
                        "any_or_all": {"type": "string", "default": "any"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "image_aspect_ratio_filter",
                "desc": "Filters images by aspect ratio",
                "type": "filter",
                "tags": ["cpu", "image"],
                "parameter_schema": {
                    "type": "object",
                    "properties": {
                        "min_ratio": {"type": "number", "default": 0.0},
                        "max_ratio": {"type": "number", "default": 100.0},
                        "any_or_all": {"type": "string", "default": "any"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "image_size_filter",
                "desc": "Filters images by file size",
                "type": "filter",
                "tags": ["cpu", "image"],
                "parameter_schema": {
                    "type": "object",
                    "properties": {
                        "min_size": {"type": "string", "default": "0"},
                        "max_size": {"type": "string", "default": "1TB"},
                        "any_or_all": {"type": "string", "default": "any"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "image_face_count_filter",
                "desc": "Filters images by face count",
                "type": "filter",
                "tags": ["cpu", "image"],
                "parameter_schema": {
                    "type": "object",
                    "properties": {
                        "cv_classifier": {"type": "string", "default": ""},
                        "min_face_count": {"type": "integer", "default": 1},
                        "max_face_count": {"type": "integer", "default": 1},
                        "any_or_all": {"type": "string", "default": "any"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "image_tagging_vlm_mapper",
                "desc": "Evaluates task-specific visual semantic requirements",
                "type": "mapper",
                "tags": ["api", "gpu", "image", "multimodal", "vllm"],
                "parameter_schema": {
                    "type": "object",
                    "properties": {
                        "system_prompt": {
                            "type": ["string", "null"],
                            "default": None,
                        },
                        "tag_field_name": {
                            "type": "string",
                            "default": "image_tags",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "detect_character_attributes_mapper",
                "desc": "Detect main character clothing color attributes",
                "type": "mapper",
                "tags": ["cuda", "model"],
                "parameter_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "key_value_grouper",
                "desc": "Group records by a configured key value",
                "type": "grouper",
                "tags": ["cpu", "dataset"],
                "parameter_schema": {
                    "type": "object",
                    "properties": {
                        "group_id": {"type": "string", "default": ""},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "image_segment_mapper",
                "desc": "Segments images with a model",
                "type": "mapper",
                "tags": ["gpu", "image", "model"],
                "parameter_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        ]


def _planning_library():
    base = build_operator_library(include_datajuicer=False)
    provider = DataJuicerOperatorProvider(
        searcher_factory=_PlanningCatalogSearcher,
        provider_version="1.5.3",
    )
    proxies = build_datajuicer_proxy_operators(provider, provider.discover())
    operators = (*base.operators, *proxies)
    base.providers.register(provider)
    return OperatorLibrary(
        operators=operators,
        registry=OperatorRegistry(item.spec for item in operators),
        runtime=OperatorRuntime(operators),
        providers=base.providers,
    )


def test_retrieval_uses_structured_capabilities_for_yifu_operator_coverage() -> None:
    library = _planning_library()
    spec = TaskSpecVersion(
        id="spec_yifu",
        version=1,
        created_by="user_1",
        change_reason="confirmed constraints",
        work_order_id="work_order_yifu",
        objective="opaque confirmed task text",
        data_sources=(DataSourceSpec(type="local_directory", uri="D:/data/yifu"),),
        capability_requirements=tuple(
            {
                "id": capability,
                "capability": capability,
                "description": capability,
            }
            for capability in (
                "image_shape",
                "aspect_ratio",
                "file_size",
                "face_count",
            )
        ),
        confirmed=True,
    )

    result = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=library.registry,
        allow_draft_candidates=True,
        available_runtime_backends=frozenset({RuntimeBackend.CPU}),
    )

    coverage = {
        item["capability"]: item for item in result["capability_coverage"]
    }
    assert {
        capability: coverage[capability]["selected_operator_version_id"]
        for capability in coverage
    } == {
        "image_shape": "datajuicer.image_shape_filter:1",
        "aspect_ratio": "datajuicer.image_aspect_ratio_filter:1",
        "file_size": "datajuicer.image_size_filter:1",
        "face_count": "datajuicer.image_face_count_filter:1",
    }


def test_retrieval_derives_operator_candidates_from_generic_constraints() -> None:
    library = _planning_library()
    spec = TaskSpecVersion(
        id="spec_generic_face",
        version=1,
        created_by="user_1",
        change_reason="model-planned requirement draft",
        work_order_id="work_order_generic_face",
        objective="保留两张及以下人脸的图片",
        data_sources=(DataSourceSpec(type="local_directory", uri="D:/data/images"),),
        constraints=(
            ConstraintContract(
                id="constraint_face_count",
                source_text="两张及以下人脸",
                scope="asset",
                field="image.face_count",
                operator="lte",
                value=2,
                unit="count",
                required_evidence_type="detected_face_count",
            ),
        ),
        confirmed=True,
    )

    result = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=library.registry,
        allow_draft_candidates=True,
        available_runtime_backends=frozenset({RuntimeBackend.CPU}),
    )

    assert result["retrieval_plan"]["sufficient"] is True
    assert result["capability_coverage"][0]["capability_id"] == (
        "constraint_face_count"
    )
    assert result["capability_coverage"][0]["selected_operator_version_id"] == (
        "datajuicer.image_face_count_filter:1"
    )

    pipelines = [
        PipelineVersion.model_validate(item)
        for item in generate_pipeline_variants(
            {
                "owner_id": "user_1",
                "task_spec": spec.model_dump(mode="json"),
                "trace": [],
                **result,
            },
            operator_library=library,
        )["pipeline_variants"]
    ]
    balanced = next(
        item for item in pipelines if item.strategy == PipelineStrategy.BALANCED
    )
    face_node = next(
        node
        for node in balanced.nodes
        if node.operator_version_id == "datajuicer.image_face_count_filter:1"
    )
    assert face_node.parameters["max_face_count"] == 2
    assert face_node.parameters["min_face_count"] == 0
    assert {item.constraint_id for item in balanced.constraint_coverage} == {
        "constraint_face_count"
    }


def test_structured_constraints_override_stale_capability_plan_and_keep_system_io() -> None:
    library = _planning_library()
    spec = TaskSpecVersion(
        id="spec_constraint_authority",
        version=2,
        created_by="user_1",
        change_reason="confirmed structured task",
        work_order_id="work_order_constraint_authority",
        objective="Apply the confirmed observable constraints",
        data_sources=(DataSourceSpec(type="local_directory", uri="D:/data/images"),),
        capability_requirements=(
            {
                "id": "image_decode",
                "capability": "image_decode",
                "description": "stale legacy plan that must not hide constraints",
            },
        ),
        constraints=(
            ConstraintContract(
                id="constraint_min_width",
                source_text="width must be at least 64 pixels",
                scope="asset",
                field="image.width",
                operator="gte",
                value=64,
                unit="px",
                required_evidence_type="image_metadata",
            ),
            ConstraintContract(
                id="constraint_max_faces",
                source_text="at most two faces",
                scope="asset",
                field="image.face_count",
                operator="lte",
                value=2,
                unit="count",
                required_evidence_type="detected_face_count",
            ),
            ConstraintContract(
                id="constraint_unique",
                source_text="remove duplicate images",
                scope="dataset",
                field="image.is_duplicate",
                operator="eq",
                value=False,
                unit="boolean",
                required_evidence_type="duplicate_decision",
            ),
        ),
        confirmed=True,
    )

    result = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=library.registry,
        allow_draft_candidates=True,
        available_runtime_backends=frozenset({RuntimeBackend.CPU}),
    )

    coverage = {
        item["capability_id"]: item for item in result["capability_coverage"]
    }
    assert set(coverage) == {
        "constraint_min_width",
        "constraint_max_faces",
        "constraint_unique",
        "system_image_decode",
        "system_manifest",
    }
    assert all(item["status"] == "covered" for item in coverage.values())

    assert all(
        not item["capability"].startswith("constraint:")
        for item in coverage.values()
    )


def test_processing_compiles_complete_ordered_pipeline_from_structured_constraints() -> None:
    library = _planning_library()

    def constraint(
        id: str,
        text: str,
        field: str,
        operator: str,
        value,
        unit: str,
        evidence: str,
        *,
        scope: str = "asset",
    ) -> ConstraintContract:
        return ConstraintContract(
            id=id,
            source_text=text,
            scope=scope,
            field=field,
            operator=operator,
            value=value,
            unit=unit,
            required_evidence_type=evidence,
        )

    spec = TaskSpecVersion(
        id="spec_multi_constraint_images",
        version=1,
        created_by="user_1",
        change_reason="model-planned requirement",
        work_order_id="work_order_multi_constraint_images",
        objective="筛选满足全部已确认条件的图片",
        data_sources=(
            DataSourceSpec(type="local_directory", uri="D:/data/images"),
        ),
        constraints=(
            constraint("C01", "宽度不少于64像素", "image.width", "gte", 64, "px", "image_metadata"),
            constraint("C02", "高度不少于64像素", "image.height", "gte", 64, "px", "image_metadata"),
            constraint("C03", "宽高比不少于0.3", "image.aspect_ratio", "gte", 0.3, "ratio", "image_metadata"),
            constraint("C04", "宽高比不大于3.5", "image.aspect_ratio", "lte", 3.5, "ratio", "image_metadata"),
            constraint("C05", "文件不少于1KB", "asset.file_size", "gte", 1024, "bytes", "source_file_metadata"),
            constraint("C06", "文件不大于20MB", "asset.file_size", "lte", 20 * 1024 * 1024, "bytes", "source_file_metadata"),
            constraint("C07", "人脸数量两张及以下", "image.face_count", "lte", 2, "count", "detected_face_count"),
            constraint("C08", "主体穿黑色衣服", "image.subject_clothing_color", "eq", "black", "label", "visual_semantic_judgment"),
            constraint("C09", "去除重复图片", "image.is_duplicate", "eq", False, "boolean", "duplicate_decision", scope="dataset"),
        ),
        semantic_requirements=("主体可见衣物的主要颜色为黑色",),
        confirmed=True,
    )
    retrieval = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=library.registry,
        allow_draft_candidates=True,
        available_runtime_backends=frozenset(
            {RuntimeBackend.CPU, RuntimeBackend.REMOTE}
        ),
    )

    # The compatibility path must report uncertainty instead of manufacturing
    # a case-specific Pipeline. Production planning uses the model-driven
    # Retrieval and Processing loops.
    assert retrieval["retrieval_plan"]["sufficient"] is False
    assert any(
        item["status"] != "covered"
        for item in retrieval["capability_coverage"]
    )


def test_semantic_attribute_constraint_uses_visual_reasoning_not_object_detection() -> None:
    library = _planning_library()
    spec = TaskSpecVersion(
        id="spec_semantic_attribute",
        version=1,
        created_by="user_1",
        change_reason="model-planned requirement",
        work_order_id="work_order_semantic_attribute",
        objective="保留满足已确认视觉属性的图片",
        data_sources=(
            DataSourceSpec(type="local_directory", uri="D:/data/images"),
        ),
        constraints=(
            ConstraintContract(
                id="constraint_visual_attribute",
                source_text="主体穿黑色衣服",
                scope="asset",
                field="image.main_subject_clothing_color",
                operator="eq",
                value="black",
                unit="color",
                required_evidence_type="clothing_color_classification",
            ),
        ),
        semantic_requirements=("判断主体衣服颜色是否为黑色",),
        confirmed=True,
    )

    result = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=library.registry,
        allow_draft_candidates=True,
        available_runtime_backends=frozenset(
            {RuntimeBackend.CPU, RuntimeBackend.REMOTE}
        ),
    )

    semantic = next(
        item
        for item in result["capability_coverage"]
        if item["capability_id"] == "constraint_visual_attribute"
    )
    assert semantic["capability"] == "visual_semantic_selection"
    assert semantic["status"] == "covered"
    assert semantic["selected_operator_version_id"] == (
        "builtin.visual_semantic_selection:1"
    )
    assert result["retrieval_plan"]["sufficient"] is True


def test_dataset_operation_intent_wins_over_weak_parameter_name_overlap() -> None:
    library = _planning_library()
    spec = TaskSpecVersion(
        id="spec_dataset_dedup",
        version=1,
        created_by="user_1",
        change_reason="model-planned requirement",
        work_order_id="work_order_dataset_dedup",
        objective="去除重复图片",
        data_sources=(
            DataSourceSpec(type="local_directory", uri="D:/data/images"),
        ),
        constraints=(
            ConstraintContract(
                id="constraint_dedup",
                source_text="去除重复图片",
                scope="dataset",
                field="image.duplicate_group_id",
                operator="eq",
                value="keep_unique_representative_only",
                unit="flag",
                required_evidence_type="duplicate_detection",
            ),
        ),
        confirmed=True,
    )

    result = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=library.registry,
        allow_draft_candidates=True,
        available_runtime_backends=frozenset({RuntimeBackend.CPU}),
    )

    dedup = next(
        item
        for item in result["capability_coverage"]
        if item["capability_id"] == "constraint_dedup"
    )
    assert dedup["capability"] == "perceptual_duplicate"
    assert dedup["status"] == "covered"
    assert dedup["selected_operator_version_id"] == "builtin.perceptual_dedup:1"
    assert result["retrieval_plan"]["sufficient"] is True


def test_datajuicer_discovery_is_lazy_cached_and_metadata_only() -> None:
    _FakeSearcher.calls = 0
    provider = DataJuicerOperatorProvider(
        searcher_factory=_FakeSearcher,
        provider_version="test-version",
    )

    descriptor = provider.discover()[0]
    assert provider.discover()[0] == descriptor
    assert _FakeSearcher.calls == 1
    assert descriptor.provider_operator_ref == "image_aesthetic_filter"
    assert descriptor.suggested_category == OperatorCategory.FILTERING
    assert descriptor.parameter_schema["properties"]["threshold"]["default"] == 0.5
    assert provider.validate(
        descriptor.provider_operator_ref, {}, RuntimeBackend.CPU
    ).normalized_parameters == {"threshold": 0.5, "enabled": True}
    invalid = provider.validate(
        descriptor.provider_operator_ref,
        {"unknown": 1},
        RuntimeBackend.CPU,
    )
    assert not invalid.ok


def test_datajuicer_discovery_maps_unparameterized_container_annotations() -> None:
    def sample(model_params: dict = {}, sampling_params: list = []):
        return None

    class ContainerSearcher:
        def search(self):
            return [
                {
                    "name": "container_mapper",
                    "desc": "Container schema test",
                    "type": "mapper",
                    "tags": ["cpu", "image"],
                    "sig": inspect.signature(sample),
                }
            ]

    descriptor = DataJuicerOperatorProvider(
        searcher_factory=ContainerSearcher,
        provider_version="test-version",
    ).discover()[0]

    properties = descriptor.parameter_schema["properties"]
    assert properties["model_params"]["type"] == "object"
    assert properties["sampling_params"]["type"] == "array"


def test_datajuicer_discovery_cache_and_admission_registry_are_separate(tmp_path) -> None:
    _FakeSearcher.calls = 0
    cache_path = tmp_path / "catalog.json"
    provider = DataJuicerOperatorProvider(
        searcher_factory=_FakeSearcher,
        provider_version="test-version",
        catalog_cache_path=cache_path,
    )
    discovered = provider.discover()
    provider.admit(discovered)

    assert cache_path.is_file()
    assert provider.discover() == discovered
    assert provider.admitted() == discovered

    class _BrokenSearcher:
        def search(self):
            raise AssertionError("disk cache should avoid dynamic discovery")

    cached_provider = DataJuicerOperatorProvider(
        searcher_factory=_BrokenSearcher,
        provider_version="test-version",
        catalog_cache_path=cache_path,
    )
    assert cached_provider.discover() == discovered


def test_datajuicer_catalog_exposes_provider_operators_without_dataagent_admission() -> None:
    provider = DataJuicerOperatorProvider(
        searcher_factory=_MixedCatalogSearcher,
        provider_version="1.5.3",
    )
    operators = build_datajuicer_proxy_operators(provider, provider.discover())
    by_ref = {
        operator.spec.provider.provider_operator_ref: operator
        for operator in operators
    }

    assert len(operators) == 5
    assert len(provider.discover()) == 2
    assert len(provider.admitted()) == 3
    assert by_ref["image_shape_filter"].spec.status == OperatorStatus.PERSONAL_RELEASE

    segment = by_ref["image_segment_mapper"]
    assert segment.spec.status == OperatorStatus.PROVIDER_AVAILABLE
    assert segment.spec.execution_scope == ExecutionScope.ASSET
    assert {profile.backend for profile in segment.spec.supported_runtime_profiles} == {
        RuntimeBackend.CUDA
    }
    assert "candidate" in segment.spec.capability_tags

    text_filter = by_ref["text_length_filter"]
    assert text_filter.spec.status == OperatorStatus.PROVIDER_AVAILABLE
    assert text_filter.spec.input_schema == "ProviderDatasetRecord"
    assert not provider.validate("text_length_filter", {}, RuntimeBackend.CPU).ok


def test_task_requirement_matches_executable_and_blocked_datajuicer_candidates() -> None:
    library = _planning_library()
    matches = DataJuicerCatalogMatcher(library.registry).match(
        "筛选人脸数量合适的图片并进行图像分割",
        required_capabilities=("segmentation",),
        allow_draft_candidates=True,
    )
    by_intent = {item.intent: item for item in matches}

    assert "face_count" not in by_intent
    assert by_intent["segmentation"].executable is False
    assert by_intent["segmentation"].runtime_backend == RuntimeBackend.CUDA
    assert "not available" in (by_intent["segmentation"].blocked_reason or "")
    assert by_intent["segmentation"].runtime_resolution is not None
    assert by_intent["segmentation"].runtime_resolution.code == (
        "CUDA_WORKER_UNAVAILABLE"
    )
    assert by_intent["segmentation"].runtime_resolution.reasons == (
        "需要 CUDA",
        "当前 Worker 没有 GPU",
    )
    assert tuple(
        item.label
        for item in by_intent["segmentation"].runtime_resolution.options
    ) == (
        "使用 GPU Worker 执行",
        "使用 Remote API 版本",
        "跳过该步骤",
    )


def test_retrieval_stops_when_only_matched_datajuicer_runtime_is_unavailable() -> None:
    library = _planning_library()
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="segment images",
        data_sources=(DataSourceSpec(type="local_directory", uri="D:/images"),),
        required_capabilities=("segmentation",),
        confirmed=True,
    )

    retrieval = generate_retrieval_plan(
        {
            "owner_id": "user_1",
            "task_spec": spec.model_dump(mode="json"),
            "trace": [],
        },
        operator_registry=library.registry,
        allow_draft_candidates=True,
    )

    assert retrieval["operator_candidates"][0]["operator_version_id"] == (
        "datajuicer.image_segment_mapper:1"
    )
    assert retrieval["operator_candidates"][0]["executable"] is False
    assert retrieval["operator_candidates"][0]["runtime_resolution"]["code"] == (
        "CUDA_WORKER_UNAVAILABLE"
    )
    coverage_candidate = retrieval["capability_coverage"][0]["candidates"][0]
    assert coverage_candidate["provider_operator_ref"] == "image_segment_mapper"
    assert coverage_candidate["runtime_resolution"]["options"][0]["id"] == (
        "use_gpu_worker"
    )
    assert retrieval["retrieval_plan"]["sufficient"] is False


def test_processing_does_not_inject_candidates_without_capability_coverage() -> None:
    library = _planning_library()
    spec = TaskSpecVersion(
        id=new_id("spec"),
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_1",
        objective="筛选人脸数量合适的图片并去重",
        data_sources=(DataSourceSpec(type="local_directory", uri="D:/images"),),
        confirmed=True,
    )
    state = {
        "owner_id": "user_1",
        "task_spec": spec.model_dump(mode="json"),
        "trace": [],
    }
    retrieval = generate_retrieval_plan(
        state,
        operator_registry=library.registry,
        allow_draft_candidates=True,
    )
    state.update(retrieval)
    pipeline = PipelineVersion.model_validate(
        generate_pipeline_variants(state, operator_library=library)[
            "pipeline_variants"
        ][0]
    )

    assert [node.operator_version_id for node in pipeline.nodes] == [
        "builtin.decode_check:1",
        "builtin.quality_filter:1",
        "builtin.perceptual_dedup:1",
        "builtin.manifest:1",
    ]
    assert retrieval["operator_candidates"] == []


def test_prepared_provider_result_preserves_upstream_asset_state() -> None:
    library = _planning_library()
    source = "D:/images/source.png"
    context = OperatorContext(
        run_id="run_1",
        work_order_id="work_order_1",
        owner_id="user_1",
        shared={
            "active_node_id": "face_count",
            "dataset_operator_results": {
                "face_count": {
                    source: OperatorResult(labels={"provider_called": True})
                }
            },
        },
    )

    result = library.runtime.execute(
        operator_version_id="datajuicer.image_face_count_filter:1",
        context=context,
        input_data=OperatorInput(
            source_path=source,
            current_path=source,
            metrics={"width": 640, "quality_score": 0.9},
            labels={"decoded": True},
        ),
        parameters={},
    )

    assert result.metrics == {"width": 640, "quality_score": 0.9}
    assert result.labels == {"decoded": True, "provider_called": True}


def test_provider_available_datajuicer_proxy_runs_isolated_jsonl_process(
    tmp_path,
) -> None:
    fake_process = tmp_path / "fake_dj_process.py"
    fake_process.write_text(
        """
import json
import os
import sys
from pathlib import Path

recipe = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
dataset_path = recipe["dataset"]["configs"][0]["path"]
row = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
parameters = next(iter(recipe["process"][0].values()))
output = Path(recipe["export_path"])
output.parent.mkdir(parents=True, exist_ok=True)
if parameters.get("enabled", True):
    row["fake_score"] = parameters.get("threshold", 0.5)
    row["offline_policy"] = {
        "hf": os.environ.get("HF_HUB_OFFLINE"),
        "uv": os.environ.get("UV_OFFLINE"),
        "pip": os.environ.get("PIP_NO_INDEX"),
    }
    output.write_text(json.dumps(row) + "\\n", encoding="utf-8")
else:
    output.write_text("", encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )
    source = tmp_path / "source.png"
    Image.new("RGB", (32, 24), (80, 100, 120)).save(source)
    executor = DataJuicerProcessExecutor(
        (sys.executable, fake_process),
        runtime_root=tmp_path / "provider-runtime",
        timeout_seconds=10,
    )
    provider = DataJuicerOperatorProvider(
        searcher_factory=_FakeSearcher,
        executor=executor,
        provider_version="test-version",
    )
    request = ProviderExecuteRequest(
        provider_operator_ref="image_aesthetic_filter",
        runtime_backend=RuntimeBackend.CPU,
        context=OperatorContext(
            run_id="run_1",
            work_order_id="work_order_1",
            owner_id="user_1",
            purpose="development",
        ),
        input_data=OperatorInput(
            source_path=str(source),
            current_path=str(source),
        ),
        parameters={"threshold": 0.7},
    )

    kept = provider.execute(request)
    assert kept.ok
    assert kept.result is not None
    assert kept.result.decision == "continue"
    assert kept.result.labels["datajuicer_output"]["fake_score"] == 0.7
    assert kept.result.labels["datajuicer_output"]["offline_policy"] == {
        "hf": "1",
        "uv": "1",
        "pip": "1",
    }
    assert Path(kept.result.artifacts[0].uri).is_file()

    rejected = provider.execute(
        request.model_copy(update={"parameters": {"enabled": False}})
    )
    assert rejected.ok
    assert rejected.result is not None
    assert rejected.result.decision == "reject"
    assert rejected.result.reason_codes == ["DATAJUICER_FILTERED_OUT"]

    proxies = build_datajuicer_proxy_operators(provider, provider.discover())
    proxy = next(
        item
        for item in proxies
        if item.spec.id == "datajuicer.image_aesthetic_filter:1"
    )
    assert proxy.spec.status == OperatorStatus.PROVIDER_AVAILABLE

    proxy_result = OperatorRuntime((proxy,)).execute(
        operator_version_id=proxy.spec.id,
        context=request.context,
        input_data=request.input_data,
        parameters={"threshold": 0.8},
        runtime_backend=RuntimeBackend.CPU,
    )

    assert proxy_result.decision == "continue"
    assert proxy_result.labels["datajuicer_output"]["fake_score"] == 0.8


def test_datajuicer_executor_batches_multiple_images_in_one_process(tmp_path) -> None:
    fake_process = tmp_path / "fake_batch_dj_process.py"
    marker = tmp_path / "invocations.txt"
    fake_process.write_text(
        """
import json
import sys
from pathlib import Path

marker = Path(sys.argv[1])
marker.write_text(marker.read_text() + "1\\n" if marker.exists() else "1\\n")
recipe = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
dataset_path = Path(recipe["dataset"]["configs"][0]["path"])
rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines()]
Path(recipe["export_path"]).write_text(
    "".join(json.dumps(row) + "\\n" for row in rows),
    encoding="utf-8",
)
""".strip(),
        encoding="utf-8",
    )
    sources = []
    for index in range(2):
        source = tmp_path / f"source-{index}.png"
        Image.new("RGB", (32, 24), (80 + index, 100, 120)).save(source)
        sources.append(source)
    executor = DataJuicerProcessExecutor(
        (sys.executable, fake_process, marker),
        runtime_root=tmp_path / "provider-runtime",
        timeout_seconds=10,
    )
    context = OperatorContext(
        run_id="run_batch",
        work_order_id="work_order_1",
        owner_id="user_1",
        purpose="development",
    )

    result = executor.execute_dataset(
        ProviderDatasetExecuteRequest(
            provider_operator_ref="image_shape_filter",
            runtime_backend=RuntimeBackend.CPU,
            context=context,
            items=tuple(
                ProviderDatasetItem(
                    asset_id=f"asset-{index}",
                    input_data=OperatorInput(
                        source_path=str(source),
                        current_path=str(source),
                    ),
                )
                for index, source in enumerate(sources)
            ),
            parameters={},
        )
    )

    assert result.ok
    assert [item.asset_id for item in result.items] == ["asset-0", "asset-1"]
    assert marker.read_text().splitlines() == ["1"]


def test_remote_single_asset_uses_bounded_timeout_and_returns_retryable_failure(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (32, 24), (80, 100, 120)).save(source)
    executor = DataJuicerProcessExecutor(
        ("dj-process",),
        runtime_root=tmp_path / "provider-runtime",
        timeout_seconds=300,
        remote_asset_timeout_seconds=17,
    )
    observed = {}

    def fake_run(command, cwd, environment, cancel_check, *, timeout_seconds):
        observed["timeout_seconds"] = timeout_seconds
        return 124, "", "asset request timed out", "timeout"

    monkeypatch.setattr(executor, "_run", fake_run)
    monkeypatch.setenv("BAILIAN_API_KEY", "test-key")

    result = executor(
        ProviderExecuteRequest(
            provider_operator_ref="image_tagging_vlm_mapper",
            runtime_backend=RuntimeBackend.REMOTE,
            context=OperatorContext(
                run_id="run_timeout",
                work_order_id="work_order_1",
                owner_id="user_1",
            ),
            input_data=OperatorInput(
                source_path=str(source), current_path=str(source)
            ),
            parameters={},
        )
    )

    assert observed["timeout_seconds"] == 17
    assert result.ok is False
    assert result.error_type == "timeout"
    assert "timed out" in result.message


def test_invalid_external_datajuicer_environment_does_not_break_library(tmp_path) -> None:
    library = build_operator_library(
        datajuicer_python=tmp_path / "missing-python.exe",
        datajuicer_runtime_root=tmp_path / "runtime",
    )

    health = library.providers.get("datajuicer").health()
    assert health.status == "unavailable"
    assert "not found" in health.message


def test_production_pipeline_cannot_use_mock_operator() -> None:
    node = PipelineNode(
        id="segment",
        operator_version_id="model.segmentation:1",
        name="Segmentation",
        category=OperatorCategory.UNDERSTANDING,
        runtime_backend=RuntimeBackend.MOCK,
    )
    pipeline = PipelineVersion(
        id="pipeline_mock",
        family_id="pipeline_mock",
        version=1,
        created_by="user_1",
        change_reason="test",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id="spec_1",
        nodes=(node,),
        created_from="test",
        approved=True,
    )

    with pytest.raises(ValueError, match="not eligible for production"):
        AgentRuntime()._validate_production_pipeline(pipeline)


def test_production_pipeline_can_execute_provider_available_cpu_operator() -> None:
    library = _planning_library()
    runtime = AgentRuntime(include_datajuicer=False)
    runtime.operator_library = library
    runtime.operator_registry = library.registry
    runtime.builtin_operators = library.operators
    node = PipelineNode(
        id="face_count",
        operator_version_id="datajuicer.image_face_count_filter:1",
        name="Face Count",
        category=OperatorCategory.FILTERING,
        runtime_backend=RuntimeBackend.CPU,
    )
    pipeline = PipelineVersion(
        id="pipeline_candidate",
        family_id="pipeline_candidate",
        version=1,
        created_by="user_1",
        change_reason="test",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id="spec_1",
        nodes=(node,),
        created_from="test",
        approved=True,
    )

    candidate = library.registry.get(node.operator_version_id)
    assert candidate.status == OperatorStatus.PROVIDER_AVAILABLE
    runtime.allow_datajuicer_candidate_execution = False
    runtime._validate_production_pipeline(pipeline)
