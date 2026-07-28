from PIL import Image

from dataagent.operators import build_operator_library
from dataagent.operators.protocol import OperatorContext, OperatorInput, OperatorResult
from dataagent.imaging import analyze_image


def test_image_analysis_emits_normalized_size_and_ratio_evidence(tmp_path) -> None:
    source = tmp_path / "asset.png"
    Image.new("RGB", (64, 32), color="black").save(source)

    metrics = analyze_image(source)

    assert metrics.width == 64
    assert metrics.height == 32
    assert metrics.aspect_ratio == 2.0
    assert metrics.file_size_bytes == source.stat().st_size


def test_perceptual_dedup_prepares_deterministic_group_evidence(tmp_path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (64, 64), color="black").save(first)
    second.write_bytes(first.read_bytes())
    library = build_operator_library(include_datajuicer=False)
    context = OperatorContext(
        run_id="run_1",
        work_order_id="work_order_1",
        owner_id="user_1",
    )
    inputs = tuple(
        OperatorInput(source_path=str(path), current_path=str(path))
        for path in (first, second)
    )

    prepared = library.runtime.prepare_dataset_node(
        operator_version_id="builtin.perceptual_dedup:1",
        node_id="deduplicate",
        context=context,
        inputs=inputs,
        parameters={"distance_threshold": 0},
    )
    first_result, second_result = (
        library.runtime.execute(
            operator_version_id="builtin.perceptual_dedup:1",
            context=context.model_copy(
                update={
                    "shared": {
                        **context.shared,
                        "active_node_id": "deduplicate",
                    }
                }
            ),
            input_data=item,
            parameters={"distance_threshold": 0},
        )
        for item in inputs
    )

    assert prepared is True
    assert first_result.decision == "continue"
    assert second_result.decision == "reject"
    assert first_result.labels["duplicate_group_id"] == (
        second_result.labels["duplicate_group_id"]
    )
    assert second_result.labels["canonical_asset_path"] == str(first)


def test_perceptual_dedup_ignores_assets_rejected_by_prepared_upstream_nodes(
    tmp_path,
) -> None:
    rejected_first = tmp_path / "rejected-first.png"
    surviving_second = tmp_path / "surviving-second.png"
    Image.new("RGB", (64, 64), color="black").save(rejected_first)
    surviving_second.write_bytes(rejected_first.read_bytes())
    inputs = tuple(
        OperatorInput(source_path=str(path), current_path=str(path))
        for path in (rejected_first, surviving_second)
    )
    context = OperatorContext(
        run_id="run_1",
        work_order_id="work_order_1",
        owner_id="user_1",
        shared={
            "dataset_operator_results": {
                "upstream_filter": {
                    str(rejected_first): OperatorResult(
                        output_path=str(rejected_first),
                        decision="reject",
                        reason_codes=["UPSTREAM_REJECT"],
                    ),
                    str(surviving_second): OperatorResult(
                        output_path=str(surviving_second),
                        decision="continue",
                    ),
                }
            }
        },
    )
    library = build_operator_library(include_datajuicer=False)

    library.runtime.prepare_dataset_node(
        operator_version_id="builtin.perceptual_dedup:1",
        node_id="deduplicate",
        context=context,
        inputs=inputs,
        parameters={"distance_threshold": 0},
    )
    result = library.runtime.execute(
        operator_version_id="builtin.perceptual_dedup:1",
        context=context.model_copy(
            update={
                "shared": {
                    **context.shared,
                    "active_node_id": "deduplicate",
                }
            }
        ),
        input_data=inputs[1],
        parameters={"distance_threshold": 0},
    )

    assert result.decision == "continue"
    assert result.labels["canonical_asset_path"] == str(surviving_second)
