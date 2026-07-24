from __future__ import annotations

import json
from pathlib import Path

import pytest

from dataagent.agents.processing.nodes import generate_pipeline_variants
from dataagent.agents.retrieval.nodes import generate_retrieval_plan
from dataagent.domain.operators import OperatorStatus, RuntimeBackend
from dataagent.domain.pipelines import PipelineVersion
from dataagent.domain.specs import DataSourceSpec, TaskCapabilitySpec, TaskSpecVersion
from dataagent.operators import build_operator_library
from dataagent.operators.builtin.vlm import (
    NativeRemoteVlmOperator,
    builtin_vlm_operators,
)
from dataagent.operators.protocol import OperatorContext, OperatorInput


def _visual_selection_spec() -> TaskSpecVersion:
    return TaskSpecVersion(
        id="spec_native_vlm",
        version=1,
        created_by="user_1",
        change_reason="test",
        work_order_id="work_order_native_vlm",
        objective="Select images where the person is wearing black clothing.",
        data_sources=(DataSourceSpec(type="local_directory", uri="D:/images"),),
        output_actions=("filter", "manifest"),
        capability_requirements=(
            TaskCapabilitySpec(
                id="image_decode",
                capability="image_decode",
                description="Decode input images.",
            ),
            TaskCapabilitySpec(
                id="visual_semantic_selection",
                capability="visual_semantic_selection",
                description="Select images matching the task criteria.",
                depends_on=("image_decode",),
            ),
            TaskCapabilitySpec(
                id="manifest",
                capability="manifest",
                description="Record decisions.",
                depends_on=("visual_semantic_selection",),
            ),
        ),
        semantic_requirements=("The person is wearing black clothing.",),
        confirmed=True,
    )


def _parameters() -> dict:
    return {
        "system_prompt": "Judge whether the person wears black clothing.",
        "tag_field_name": "visual_tags",
        "allowed_tags": [
            "semantic_match",
            "semantic_mismatch",
            "semantic_uncertain",
        ],
        "required_tag_groups": [
            [
                "semantic_match",
                "semantic_mismatch",
                "semantic_uncertain",
            ]
        ],
        "model": None,
        "max_tokens": 512,
    }


def test_native_remote_vlm_is_stably_registered_without_credentials() -> None:
    operators = builtin_vlm_operators(None)

    assert len(operators) == 1
    operator = operators[0]
    assert operator.spec.id == "native.remote_vlm:1"
    assert operator.spec.status == OperatorStatus.PERSONAL_RELEASE
    assert operator.spec.secondary_category == "vlm_judgement"
    assert operator.spec.supported_runtime_profiles[0].backend == RuntimeBackend.REMOTE

    with pytest.raises(RuntimeError, match="gateway is not configured"):
        operator.execute(
            OperatorContext(
                run_id="run_1",
                work_order_id="work_order_1",
                owner_id="user_1",
            ),
            OperatorInput(source_path="missing.jpg", current_path="missing.jpg"),
            _parameters(),
        )


def test_native_remote_vlm_enforces_contract_and_writes_evidence(
    tmp_path: Path,
) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image-bytes")
    calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    def gateway(**kwargs):
        calls.append(kwargs)
        return {
            "tags": ["semantic_match"],
            "confidence": 0.92,
            "reason": "The visible clothing is black.",
            "_dataagent_gateway": {
                "model": "qwen3.7-plus",
                "request_id": "request_1",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }

    operator = NativeRemoteVlmOperator(gateway)
    result = operator.execute(
        OperatorContext(
            run_id="run_1",
            work_order_id="work_order_1",
            owner_id="user_1",
            shared={
                "active_node_id": "visual_tagging",
                "artifact_root": tmp_path / "artifacts",
                "event_sink": lambda event_type, details: events.append(
                    (event_type, details)
                ),
            },
        ),
        OperatorInput(
            source_path=str(image),
            current_path=str(image),
            metrics={"sha256": "a" * 64},
        ),
        _parameters(),
    )

    assert calls[0]["prompt"] == _parameters()["system_prompt"]
    assert result.labels["datajuicer_output"]["visual_tags"] == [
        "semantic_match"
    ]
    assert result.labels["native_vlm_evidence"]["request_id"] == "request_1"
    assert result.model_version_id == "qwen3.7-plus"
    assert result.confidence == 0.92
    assert result.artifacts[-1].media_type == "application/json"
    evidence = json.loads(
        Path(result.artifacts[-1].uri).read_text(encoding="utf-8")
    )
    assert evidence["prompt_sha256"]
    assert evidence["response"]["tags"] == ["semantic_match"]
    assert [event[0] for event in events] == [
        "native_vlm_call_started",
        "native_vlm_call_completed",
    ]


def test_native_remote_vlm_rejects_tags_outside_governed_contract(
    tmp_path: Path,
) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image-bytes")
    operator = NativeRemoteVlmOperator(lambda **_: {"tags": ["black_clothing"]})

    with pytest.raises(RuntimeError, match="outside the governed contract"):
        operator.execute(
            OperatorContext(
                run_id="run_1",
                work_order_id="work_order_1",
                owner_id="user_1",
            ),
            OperatorInput(source_path=str(image), current_path=str(image)),
            _parameters(),
        )


def test_native_remote_vlm_requires_non_empty_governed_contract(
    tmp_path: Path,
) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image-bytes")
    operator = NativeRemoteVlmOperator(lambda **_: {"tags": ["anything"]})

    with pytest.raises(RuntimeError, match="non-empty tag contract"):
        operator.execute(
            OperatorContext(
                run_id="run_1",
                work_order_id="work_order_1",
                owner_id="user_1",
            ),
            OperatorInput(source_path=str(image), current_path=str(image)),
            {
                **_parameters(),
                "allowed_tags": [],
                "required_tag_groups": [],
            },
        )


def test_native_remote_vlm_is_default_visual_provider_and_binds_task_prompt() -> None:
    library = build_operator_library(
        include_datajuicer=False,
        vlm_gateway=lambda **_: {"tags": ["semantic_match"]},
    )
    spec = _visual_selection_spec()
    state = {
        "owner_id": "user_1",
        "task_spec": spec.model_dump(mode="json"),
        "trace": [],
    }
    retrieval = generate_retrieval_plan(
        state,
        operator_registry=library.registry,
        available_runtime_backends=frozenset(
            {RuntimeBackend.CPU, RuntimeBackend.REMOTE}
        ),
    )
    state.update(retrieval)

    candidates = {
        item["operator_version_id"]: item
        for item in retrieval["operator_candidates"]
    }
    assert candidates["native.remote_vlm:1"]["executable"] is True

    pipeline = PipelineVersion.model_validate(
        generate_pipeline_variants(state, operator_library=library)[
            "pipeline_variants"
        ][0]
    )
    visual_node = next(node for node in pipeline.nodes if node.id == "visual_tagging")

    assert visual_node.operator_version_id == "native.remote_vlm:1"
    assert visual_node.prompt_binding is not None
    assert visual_node.prompt_binding.template_id == "image-semantic-selection"
    assert "black clothing" in visual_node.parameters["system_prompt"]
    assert visual_node.parameters["allowed_tags"] == [
        "semantic_match",
        "semantic_mismatch",
        "semantic_uncertain",
    ]
