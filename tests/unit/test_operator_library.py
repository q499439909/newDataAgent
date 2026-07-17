from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import sys

import pytest
from PIL import Image

from dataagent.agents.processing.nodes import generate_pipeline_variants
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.domain.common import new_id
from dataagent.domain.operators import OperatorCategory, RuntimeBackend
from dataagent.domain.pipelines import PipelineNode, PipelineStrategy, PipelineVersion
from dataagent.domain.specs import DataSourceSpec, TaskSpecVersion
from dataagent.operators import build_operator_library
from dataagent.operators.planning import (
    CapabilityGapError,
    OperatorRequirement,
    OperatorSelector,
    infer_required_capabilities,
)
from dataagent.operators.protocol import OperatorContext, OperatorInput
from dataagent.operators.providers import DataJuicerOperatorProvider
from dataagent.operators.providers import DataJuicerProcessExecutor
from dataagent.operators.providers import ProviderExecuteRequest
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
