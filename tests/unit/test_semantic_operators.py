from __future__ import annotations

from dataagent.operators.builtin.semantic import (
    AuthenticityDecisionOperator,
    ClassResolutionOperator,
    DatasetPartitionOperator,
)
from dataagent.operators.protocol import OperatorContext, OperatorInput


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


def test_class_resolution_handles_cat_dog_mixed_and_unknown() -> None:
    operator = ClassResolutionOperator()
    parameters = {"mixed_policy": "review", "unknown_policy": "reject"}

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
        _context(), input_data, {"directory_prefix": "classes"}
    )

    assert result.labels["output_relative_path"] == (
        "classes/cat/pet-aaaaaaaaaaaa.jpg"
    )
