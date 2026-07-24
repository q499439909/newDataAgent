from __future__ import annotations

import pytest

from dataagent.operators import OperatorRuntime
from dataagent.operators.builtin.semantic import (
    AuthenticityDecisionOperator,
    ClassResolutionOperator,
    DatasetPartitionOperator,
    VisualSemanticSelectionOperator,
)
from dataagent.operators.protocol import OperatorContext, OperatorInput
from dataagent.operators.validation import ParameterValidationError


def _context() -> OperatorContext:
    return OperatorContext(
        run_id="run_1",
        work_order_id="work_order_1",
        owner_id="user_1",
    )


def _input(tags: list[str]) -> OperatorInput:
    return OperatorInput(
        source_path="D:/images/pet.jpg",
        current_path="D:/images/pet.jpg",
        metrics={"sha256": "a" * 64},
        labels={"datajuicer_output": {"image_tags": tags}},
    )


def test_authenticity_decision_rejects_synthetic_and_governs_uncertain() -> None:
    operator = AuthenticityDecisionOperator()

    synthetic = operator.execute(
        _context(), _input(["synthetic", "cat"]), {"uncertain_policy": "keep"}
    )
    uncertain = operator.execute(
        _context(), _input(["cat"]), {"uncertain_policy": "review"}
    )

    assert synthetic.decision == "reject"
    assert synthetic.labels["authenticity"] == "synthetic"
    assert uncertain.decision == "continue"
    assert uncertain.labels["authenticity_review_required"] is True


def test_visual_semantic_selection_rejects_mismatch_and_governs_uncertain() -> None:
    operator = VisualSemanticSelectionOperator()

    mismatch = operator.execute(
        _context(),
        _input(["semantic_mismatch"]),
        {"uncertain_policy": "review"},
    )
    uncertain = operator.execute(
        _context(),
        _input([]),
        {"uncertain_policy": "review"},
    )

    assert mismatch.decision == "reject"
    assert mismatch.reason_codes == ["VISUAL_SEMANTIC_CRITERIA_NOT_MET"]
    assert uncertain.decision == "continue"
    assert uncertain.labels["visual_semantic_review_required"] is True


def test_class_resolution_handles_cat_dog_mixed_and_unknown() -> None:
    operator = ClassResolutionOperator()
    parameters = {
        "mixed_policy": "review",
        "unknown_policy": "reject",
        "labels": [
            {"id": "cat", "aliases": ["cat", "kitten", "feline"]},
            {"id": "dog", "aliases": ["dog", "puppy", "canine"]},
        ],
    }

    cat = operator.execute(_context(), _input(["cat"]), parameters)
    mixed = operator.execute(_context(), _input(["cat", "dog"]), parameters)
    unknown = operator.execute(_context(), _input(["rabbit"]), parameters)

    assert cat.labels["resolved_class"] == "cat"
    assert mixed.labels["resolved_class"] == "mixed"
    assert mixed.labels["class_review_required"] is True
    assert unknown.decision == "reject"


def test_dataset_partition_emits_collision_resistant_relative_path() -> None:
    operator = DatasetPartitionOperator()
    input_data = _input(["cat"]).model_copy(
        update={"labels": {"resolved_class": "cat"}}
    )

    result = operator.execute(
        _context(),
        input_data,
        {
            "directory_prefix": "classes",
            "allowed_labels": ["cat", "dog"],
        },
    )

    assert result.labels["output_relative_path"] == (
        "classes/cat/pet-aaaaaaaaaaaa.jpg"
    )


def test_class_resolution_and_partition_support_task_specific_labels() -> None:
    resolver = ClassResolutionOperator()
    parameters = {
        "mixed_policy": "review",
        "unknown_policy": "reject",
        "labels": [
            {"id": "pig", "aliases": ["pig", "piglet"]},
            {"id": "dog", "aliases": ["dog", "puppy"]},
        ],
        "mixed_label": "both",
        "unknown_label": "unclassified",
    }

    pig = resolver.execute(_context(), _input(["piglet"]), parameters)
    mixed = resolver.execute(_context(), _input(["pig", "puppy"]), parameters)

    assert pig.labels["resolved_class"] == "pig"
    assert mixed.labels["resolved_class"] == "both"

    partitioned = DatasetPartitionOperator().execute(
        _context(),
        _input(["pig"]).model_copy(update={"labels": pig.labels}),
        {
            "directory_prefix": "classes",
            "allowed_labels": ["pig", "dog"],
            "mixed_label": "both",
            "unknown_label": "unclassified",
        },
    )
    assert partitioned.labels["output_relative_path"] == (
        "classes/pig/pet-aaaaaaaaaaaa.jpg"
    )


def test_class_resolution_requires_task_specific_labels() -> None:
    runtime = OperatorRuntime((ClassResolutionOperator(),))

    with pytest.raises(ParameterValidationError, match="labels"):
        runtime.execute(
            operator_version_id="builtin.class_resolution:1",
            context=_context(),
            input_data=_input(["cat"]),
            parameters={
                "mixed_policy": "review",
                "unknown_policy": "reject",
            },
        )


def test_dataset_partition_requires_task_specific_allowed_labels() -> None:
    runtime = OperatorRuntime((DatasetPartitionOperator(),))

    with pytest.raises(ParameterValidationError, match="allowed_labels"):
        runtime.execute(
            operator_version_id="builtin.dataset_partition:1",
            context=_context(),
            input_data=_input(["pig"]).model_copy(
                update={"labels": {"resolved_class": "pig"}}
            ),
            parameters={"directory_prefix": "classes"},
        )
