from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import sys

import pytest
from PIL import Image

from dataagent.agents.processing.nodes import generate_pipeline_variants
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
from dataagent.domain.specs import DataSourceSpec, TaskSpecVersion
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


def test_processing_compiles_required_model_capabilities_to_mock_nodes(tmp_path) -> None:
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

    variants = generate_pipeline_variants(
        {"owner_id": "user_1", "task_spec": spec.model_dump(mode="json"), "trace": []}
    )["pipeline_variants"]
    pipeline = PipelineVersion.model_validate(variants[0])

    assert pipeline.nodes[1].id == "understand_segmentation"
    assert pipeline.nodes[1].runtime_backend == RuntimeBackend.MOCK
    assert pipeline.nodes[1].category == OperatorCategory.UNDERSTANDING


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
        schema = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        return [
            {
                "name": "image_face_count_filter",
                "desc": "Filters images by face count",
                "type": "filter",
                "tags": ["cpu", "image"],
                "parameter_schema": schema,
            },
            {
                "name": "image_segment_mapper",
                "desc": "Segments images with a model",
                "type": "mapper",
                "tags": ["gpu", "image", "model"],
                "parameter_schema": schema,
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


def test_datajuicer_catalog_generates_draft_candidates_without_publishing_them() -> None:
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
    assert segment.spec.status == OperatorStatus.DRAFT
    assert segment.spec.execution_scope == ExecutionScope.ASSET
    assert {profile.backend for profile in segment.spec.supported_runtime_profiles} == {
        RuntimeBackend.CUDA
    }
    assert "candidate" in segment.spec.capability_tags

    text_filter = by_ref["text_length_filter"]
    assert text_filter.spec.status == OperatorStatus.DRAFT
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

    assert by_intent["face_count"].provider_operator_ref == "image_face_count_filter"
    assert by_intent["face_count"].executable is True
    assert by_intent["face_count"].runtime_backend == RuntimeBackend.CPU
    assert by_intent["segmentation"].executable is False
    assert by_intent["segmentation"].runtime_backend == RuntimeBackend.CUDA
    assert "not available" in (by_intent["segmentation"].blocked_reason or "")


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
    assert retrieval["retrieval_plan"]["sufficient"] is False


def test_retrieval_candidates_compile_to_callable_datajuicer_pipeline_nodes() -> None:
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
        "datajuicer.image_deduplicator:1",
        "datajuicer.image_face_count_filter:1",
        "builtin.quality_filter:1",
        "builtin.manifest:1",
    ]
    assert retrieval["operator_candidates"][0]["executable"] is True


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


def test_datajuicer_executor_runs_isolated_jsonl_process(tmp_path) -> None:
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


def test_production_pipeline_can_execute_matched_cpu_image_candidate() -> None:
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

    runtime._validate_production_pipeline(pipeline)
    runtime.allow_datajuicer_candidate_execution = False
    with pytest.raises(ValueError, match="status DRAFT"):
        runtime._validate_production_pipeline(pipeline)
