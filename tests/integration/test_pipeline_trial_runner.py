import json
from pathlib import Path

import pytest
from PIL import Image

from dataagent.domain.operators import (
    ImplementationSpec,
    ImplementationType,
    OperatorCategory,
    OperatorSpecVersion,
    OperatorStatus,
    ProviderRef,
    RuntimeBackend,
    RuntimeProfile,
)
from dataagent.domain.pipelines import (
    ConstraintCoverage,
    PipelineEdge,
    PipelineNode,
    PipelineStrategy,
    PipelineVersion,
)
from dataagent.domain.specs import (
    ConstraintContract,
    DataSourceSpec,
    TaskSpecVersion,
)
from dataagent.execution.pipeline_trial import (
    ConstraintTrialStatus,
    PipelineTrialRequest,
    PipelineTrialRunner,
    PipelineTrialStatus,
)
from dataagent.operators import build_operator_library
from dataagent.operators.protocol import OperatorContext, OperatorInput, OperatorResult


class StructuredObservationOperator:
    spec = OperatorSpecVersion(
        id="test.structured_observation:1",
        family_id="test.structured_observation",
        version=1,
        created_by="test",
        change_reason="test boundary adapter",
        display_name="Structured observation",
        summary="Reads structured observations from a test asset.",
        description=(
            "Test adapter for exercising the public Operator and trial interfaces "
            "without task-specific production behavior."
        ),
        primary_category=OperatorCategory.UNDERSTANDING,
        secondary_category="classification",
        capability_tags=frozenset({"structured_observation"}),
        input_schema="AssetRef",
        output_schema="ObservedAsset",
        parameter_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        provider=ProviderRef(
            provider_id="test",
            provider_version="1",
            provider_operator_ref="structured_observation",
        ),
        implementation=ImplementationSpec(
            implementation_type=ImplementationType.CODE,
            entrypoint="tests.integration:StructuredObservationOperator",
        ),
        supported_runtime_profiles=(
            RuntimeProfile(backend=RuntimeBackend.CPU),
        ),
        implementation_ref="tests.integration:StructuredObservationOperator",
        status=OperatorStatus.EVALUATED,
        owner_id="test",
        visibility="private",
    )

    def execute(
        self,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict,
    ) -> OperatorResult:
        observations = json.loads(
            Path(input_data.current_path).read_text(encoding="utf-8")
        )
        return OperatorResult(
            output_path=input_data.current_path,
            metrics=observations,
            decision="continue",
        )


class SemanticJudgmentOperator:
    spec = OperatorSpecVersion(
        id="test.semantic_judgment:1",
        family_id="test.semantic_judgment",
        version=1,
        created_by="test",
        change_reason="test boundary adapter",
        display_name="Semantic judgment",
        summary="Produces governed semantic judgment evidence.",
        description="Test adapter for the visual semantic Evidence contract.",
        primary_category=OperatorCategory.UNDERSTANDING,
        secondary_category="vlm_judgement",
        capability_tags=frozenset({"visual_semantic_selection"}),
        input_schema="AssetRef",
        output_schema="ImageTagSet",
        parameter_schema={
            "type": "object",
            "properties": {
                "judgment": {
                    "type": "string",
                    "enum": ["match", "mismatch"],
                }
            },
            "required": ["judgment"],
            "additionalProperties": False,
        },
        provider=ProviderRef(
            provider_id="test",
            provider_version="1",
            provider_operator_ref="semantic_judgment",
        ),
        implementation=ImplementationSpec(
            implementation_type=ImplementationType.CODE,
            entrypoint="tests.integration:SemanticJudgmentOperator",
        ),
        supported_runtime_profiles=(
            RuntimeProfile(backend=RuntimeBackend.CPU),
        ),
        implementation_ref="tests.integration:SemanticJudgmentOperator",
        status=OperatorStatus.EVALUATED,
        owner_id="test",
        visibility="private",
    )

    def execute(
        self,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict,
    ) -> OperatorResult:
        judgment = parameters["judgment"]
        return OperatorResult(
            output_path=input_data.current_path,
            labels={
                "datajuicer_output": {
                    "visual_tags": [f"semantic-{judgment}"],
                    "_dataagent_output_contract": "image_tag_set:1",
                }
            },
            decision="reject" if judgment == "mismatch" else "continue",
        )


class MutatingTrialOperator:
    spec = OperatorSpecVersion(
        id="test.mutating_trial_operator:1",
        family_id="test.mutating_trial_operator",
        version=1,
        created_by="test",
        change_reason="test boundary adapter",
        display_name="Mutating trial operator",
        summary="Mutates the trial working copy.",
        description="Test adapter proving that trials isolate source assets.",
        primary_category=OperatorCategory.TRANSFORMATION,
        secondary_category="format_conversion",
        capability_tags=frozenset({"trial_mutation"}),
        input_schema="AssetRef",
        output_schema="AssetRef",
        parameter_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        provider=ProviderRef(
            provider_id="test",
            provider_version="1",
            provider_operator_ref="mutating_trial_operator",
        ),
        implementation=ImplementationSpec(
            implementation_type=ImplementationType.CODE,
            entrypoint="tests.integration:MutatingTrialOperator",
        ),
        supported_runtime_profiles=(
            RuntimeProfile(backend=RuntimeBackend.CPU),
        ),
        implementation_ref="tests.integration:MutatingTrialOperator",
        status=OperatorStatus.EVALUATED,
        owner_id="test",
        visibility="private",
    )

    def execute(
        self,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict,
    ) -> OperatorResult:
        Path(input_data.current_path).write_text("mutated", encoding="utf-8")
        return OperatorResult(
            output_path=input_data.current_path,
            decision="continue",
        )


def test_trial_executes_real_assets_and_reports_constraint_failure(tmp_path) -> None:
    accepted = tmp_path / "accepted.png"
    rejected = tmp_path / "rejected.png"
    Image.new("RGB", (80, 80), color="white").save(accepted)
    Image.new("RGB", (32, 80), color="white").save(rejected)
    constraint = ConstraintContract(
        id="constraint_min_width",
        source_text="width is at least 64 pixels",
        scope="asset",
        field="image.width",
        operator="gte",
        value=64,
        unit="pixel",
        required_evidence_type="image_metadata",
    )
    task_spec = TaskSpecVersion(
        id="task_spec_1",
        version=1,
        created_by="user_1",
        change_reason="confirmed trial requirement",
        work_order_id="work_order_1",
        objective="keep assets meeting the confirmed width constraint",
        data_sources=(
            DataSourceSpec(type="local_directory", uri=str(tmp_path)),
        ),
        constraints=(constraint,),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id="pipeline_1",
        family_id="pipeline_balanced",
        version=1,
        created_by="processing_agent",
        change_reason="trial candidate",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=task_spec.id,
        nodes=(
            PipelineNode(
                id="inspect_metadata",
                operator_version_id="builtin.decode_check:1",
                name="inspect metadata",
                category="INGESTION",
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
        ),
        created_from="processing_agent",
        required_constraint_ids=(constraint.id,),
        constraint_coverage=(
            ConstraintCoverage(
                constraint_id=constraint.id,
                node_id="inspect_metadata",
                operator_version_id="builtin.decode_check:1",
                evidence_type="image_metadata",
            ),
        ),
    )

    observation = PipelineTrialRunner(
        build_operator_library(include_datajuicer=False)
    ).run(
        PipelineTrialRequest(
            task_spec=task_spec,
            pipeline=pipeline,
            sample_paths=(str(accepted), str(rejected)),
            max_assets=2,
        )
    )

    assert observation.status == PipelineTrialStatus.FAILED
    assert observation.asset_count == 2
    assert [
        (item.asset_path, item.status, item.observed_value)
        for item in observation.constraint_results
    ] == [
        (str(accepted), ConstraintTrialStatus.PASSED, 80),
        (str(rejected), ConstraintTrialStatus.FAILED, 32),
    ]


def test_trial_discovers_a_bounded_sample_from_confirmed_data_sources(
    tmp_path,
) -> None:
    for index, width in enumerate((80, 81, 82), start=1):
        Image.new("RGB", (width, 80), color="white").save(
            tmp_path / f"asset-{index}.png"
        )
    constraint = ConstraintContract(
        id="constraint_min_width",
        source_text="width is at least 64 pixels",
        scope="asset",
        field="image.width",
        operator="gte",
        value=64,
        unit="pixel",
        required_evidence_type="image_metadata",
    )
    task_spec = TaskSpecVersion(
        id="task_spec_discovery",
        version=1,
        created_by="user_1",
        change_reason="confirmed trial requirement",
        work_order_id="work_order_1",
        objective="keep assets meeting the confirmed width constraint",
        data_sources=(
            DataSourceSpec(type="local_directory", uri=str(tmp_path)),
        ),
        constraints=(constraint,),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id="pipeline_discovery",
        family_id="pipeline_balanced",
        version=1,
        created_by="processing_agent",
        change_reason="trial candidate",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=task_spec.id,
        nodes=(
            PipelineNode(
                id="inspect_metadata",
                operator_version_id="builtin.decode_check:1",
                name="inspect metadata",
                category="INGESTION",
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
        ),
        created_from="processing_agent",
        required_constraint_ids=(constraint.id,),
        constraint_coverage=(
            ConstraintCoverage(
                constraint_id=constraint.id,
                node_id="inspect_metadata",
                operator_version_id="builtin.decode_check:1",
                evidence_type="image_metadata",
            ),
        ),
    )

    observation = PipelineTrialRunner(
        build_operator_library(include_datajuicer=False)
    ).run(
        PipelineTrialRequest(
            task_spec=task_spec,
            pipeline=pipeline,
            max_assets=2,
        )
    )

    assert observation.status == PipelineTrialStatus.PASSED
    assert observation.asset_count == 2
    assert [item.observed_value for item in observation.constraint_results] == [
        80,
        81,
    ]


def test_trial_rejects_successful_execution_without_required_evidence(
    tmp_path,
) -> None:
    source = tmp_path / "asset.png"
    Image.new("RGB", (80, 80), color="white").save(source)
    constraint = ConstraintContract(
        id="constraint_observed_count",
        source_text="observed count is at most 2",
        scope="asset",
        field="asset.observed_count",
        operator="lte",
        value=2,
        unit="count",
        required_evidence_type="detected_object_count",
    )
    task_spec = TaskSpecVersion(
        id="task_spec_missing_evidence",
        version=1,
        created_by="user_1",
        change_reason="confirmed trial requirement",
        work_order_id="work_order_1",
        objective="keep assets with an acceptable observed count",
        data_sources=(
            DataSourceSpec(type="local_directory", uri=str(tmp_path)),
        ),
        constraints=(constraint,),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id="pipeline_missing_evidence",
        family_id="pipeline_balanced",
        version=1,
        created_by="processing_agent",
        change_reason="trial candidate",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=task_spec.id,
        nodes=(
            PipelineNode(
                id="write_manifest",
                operator_version_id="builtin.manifest:1",
                name="write manifest",
                category="OUTPUT",
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
        ),
        created_from="processing_agent",
        required_constraint_ids=(constraint.id,),
        constraint_coverage=(
            ConstraintCoverage(
                constraint_id=constraint.id,
                node_id="write_manifest",
                operator_version_id="builtin.manifest:1",
                evidence_type="detected_object_count",
            ),
        ),
    )

    observation = PipelineTrialRunner(
        build_operator_library(include_datajuicer=False)
    ).run(
        PipelineTrialRequest(
            task_spec=task_spec,
            pipeline=pipeline,
            sample_paths=(str(source),),
        )
    )

    assert observation.status == PipelineTrialStatus.FAILED
    assert observation.constraint_results[0].status == (
        ConstraintTrialStatus.MISSING_EVIDENCE
    )
    assert observation.constraint_results[0].failure_code == "MISSING_EVIDENCE"


@pytest.mark.parametrize(
    ("field", "operator", "expected", "observed"),
    [
        ("image.vehicle_count", "lte", 3, 2),
        ("document.character_count", "gte", 10, 12),
    ],
)
def test_trial_generalizes_to_unseen_observable_fields(
    tmp_path,
    field,
    operator,
    expected,
    observed,
) -> None:
    source = tmp_path / "asset.json"
    source.write_text(
        json.dumps({field.rsplit(".", 1)[-1]: observed}),
        encoding="utf-8",
    )
    constraint = ConstraintContract(
        id="constraint_unseen_observable",
        source_text="confirmed observable threshold",
        scope="asset",
        field=field,
        operator=operator,
        value=expected,
        unit="count",
        required_evidence_type="structured_observation",
    )
    task_spec = TaskSpecVersion(
        id="task_spec_unseen_observable",
        version=1,
        created_by="user_1",
        change_reason="confirmed generalized trial requirement",
        work_order_id="work_order_1",
        objective="evaluate an unseen observable",
        data_sources=(
            DataSourceSpec(type="local_directory", uri=str(tmp_path)),
        ),
        constraints=(constraint,),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id="pipeline_unseen_observable",
        family_id="pipeline_balanced",
        version=1,
        created_by="processing_agent",
        change_reason="generalized trial candidate",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=task_spec.id,
        nodes=(
            PipelineNode(
                id="observe",
                operator_version_id=StructuredObservationOperator.spec.id,
                name="observe structured value",
                category="UNDERSTANDING",
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
        ),
        created_from="processing_agent",
        required_constraint_ids=(constraint.id,),
        constraint_coverage=(
            ConstraintCoverage(
                constraint_id=constraint.id,
                node_id="observe",
                operator_version_id=StructuredObservationOperator.spec.id,
                evidence_type="structured_observation",
            ),
        ),
    )
    library = build_operator_library(include_datajuicer=False)
    library.runtime.register(StructuredObservationOperator())

    observation = PipelineTrialRunner(library).run(
        PipelineTrialRequest(
            task_spec=task_spec,
            pipeline=pipeline,
            sample_paths=(str(source),),
        )
    )

    assert observation.status == PipelineTrialStatus.PASSED
    assert observation.constraint_results[0].status == (
        ConstraintTrialStatus.PASSED
    )
    assert observation.constraint_results[0].observed_value == observed


def test_trial_validates_dataset_level_duplicate_group_evidence(tmp_path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (80, 80), color="white").save(first)
    second.write_bytes(first.read_bytes())
    constraint = ConstraintContract(
        id="constraint_unique_representative",
        source_text="keep one representative per duplicate group",
        scope="dataset",
        field="dataset.unique_representative_per_duplicate_group",
        operator="eq",
        value=True,
        unit="flag",
        required_evidence_type="duplicate_group_membership",
    )
    duplicate_count_constraint = ConstraintContract(
        id="constraint_duplicate_count",
        source_text="no duplicate images remain",
        scope="dataset",
        field="image.duplicate_count",
        operator="eq",
        value=0,
        unit="count",
        required_evidence_type="duplicate_detection",
    )
    task_spec = TaskSpecVersion(
        id="task_spec_dataset_constraint",
        version=1,
        created_by="user_1",
        change_reason="confirmed dataset-level requirement",
        work_order_id="work_order_1",
        objective="keep one representative per duplicate group",
        data_sources=(
            DataSourceSpec(type="local_directory", uri=str(tmp_path)),
        ),
        constraints=(constraint, duplicate_count_constraint),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id="pipeline_dataset_constraint",
        family_id="pipeline_balanced",
        version=1,
        created_by="processing_agent",
        change_reason="dataset trial candidate",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=task_spec.id,
        nodes=(
            PipelineNode(
                id="inspect_metadata",
                operator_version_id="builtin.decode_check:1",
                name="inspect metadata",
                category="INGESTION",
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
            PipelineNode(
                id="deduplicate",
                operator_version_id="builtin.perceptual_dedup:1",
                name="deduplicate",
                category="DEDUPLICATION",
                parameters={"distance_threshold": 0},
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
        ),
        edges=(
            PipelineEdge(source="inspect_metadata", target="deduplicate"),
        ),
        created_from="processing_agent",
        required_constraint_ids=(constraint.id, duplicate_count_constraint.id),
        constraint_coverage=(
            ConstraintCoverage(
                constraint_id=constraint.id,
                node_id="deduplicate",
                operator_version_id="builtin.perceptual_dedup:1",
                evidence_type="duplicate_group_membership",
            ),
            ConstraintCoverage(
                constraint_id=duplicate_count_constraint.id,
                node_id="deduplicate",
                operator_version_id="builtin.perceptual_dedup:1",
                evidence_type="duplicate_detection",
            ),
        ),
    )

    observation = PipelineTrialRunner(
        build_operator_library(include_datajuicer=False)
    ).run(
        PipelineTrialRequest(
            task_spec=task_spec,
            pipeline=pipeline,
            sample_paths=(str(first), str(second)),
        )
    )

    assert observation.status == PipelineTrialStatus.PASSED
    assert observation.constraint_results[0].asset_path is None
    assert observation.constraint_results[0].status == ConstraintTrialStatus.PASSED
    assert observation.constraint_results[0].observed_value is True
    assert observation.constraint_results[1].status == ConstraintTrialStatus.PASSED
    assert observation.constraint_results[1].observed_value == 0


def test_trial_accepts_a_constraint_operator_rejecting_a_violating_asset(
    tmp_path,
) -> None:
    source = tmp_path / "low-quality.png"
    Image.new("RGB", (80, 80), color="white").save(source)
    constraint = ConstraintContract(
        id="constraint_min_quality",
        source_text="quality score is at least 0.99",
        scope="asset",
        field="image.quality_score",
        operator="gte",
        value=0.99,
        unit="score",
        required_evidence_type="quality_score",
    )
    task_spec = TaskSpecVersion(
        id="task_spec_filter_enforcement",
        version=1,
        created_by="user_1",
        change_reason="confirmed filter requirement",
        work_order_id="work_order_1",
        objective="filter assets below the quality threshold",
        data_sources=(
            DataSourceSpec(type="local_directory", uri=str(tmp_path)),
        ),
        constraints=(constraint,),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id="pipeline_filter_enforcement",
        family_id="pipeline_balanced",
        version=1,
        created_by="processing_agent",
        change_reason="filter trial candidate",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=task_spec.id,
        nodes=(
            PipelineNode(
                id="inspect_metadata",
                operator_version_id="builtin.decode_check:1",
                name="inspect metadata",
                category="INGESTION",
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
            PipelineNode(
                id="filter_quality",
                operator_version_id="builtin.quality_filter:1",
                name="filter quality",
                category="FILTERING",
                parameters={"confidence_threshold": 0.99},
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
        ),
        edges=(
            PipelineEdge(source="inspect_metadata", target="filter_quality"),
        ),
        created_from="processing_agent",
        required_constraint_ids=(constraint.id,),
        constraint_coverage=(
            ConstraintCoverage(
                constraint_id=constraint.id,
                node_id="filter_quality",
                operator_version_id="builtin.quality_filter:1",
                evidence_type="quality_score",
            ),
        ),
    )

    observation = PipelineTrialRunner(
        build_operator_library(include_datajuicer=False)
    ).run(
        PipelineTrialRequest(
            task_spec=task_spec,
            pipeline=pipeline,
            sample_paths=(str(source),),
        )
    )

    assert observation.status == PipelineTrialStatus.PASSED
    assert observation.constraint_results[0].status == ConstraintTrialStatus.PASSED
    assert observation.constraint_results[0].observed_value < 0.99


@pytest.mark.parametrize("judgment", ["match", "mismatch"])
def test_trial_interprets_governed_semantic_judgment_evidence(
    tmp_path,
    judgment,
) -> None:
    source = tmp_path / "asset.png"
    Image.new("RGB", (80, 80), color="white").save(source)
    constraint = ConstraintContract(
        id="constraint_semantic_target",
        source_text="asset matches the confirmed semantic target",
        scope="asset",
        field="asset.semantic_target",
        operator="eq",
        value="confirmed-target",
        unit="category",
        required_evidence_type="visual_semantic_judgment",
    )
    task_spec = TaskSpecVersion(
        id="task_spec_semantic_judgment",
        version=1,
        created_by="user_1",
        change_reason="confirmed semantic requirement",
        work_order_id="work_order_1",
        objective="filter by an arbitrary semantic target",
        data_sources=(
            DataSourceSpec(type="local_directory", uri=str(tmp_path)),
        ),
        constraints=(constraint,),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id=f"pipeline_semantic_{judgment}",
        family_id="pipeline_balanced",
        version=1,
        created_by="processing_agent",
        change_reason="semantic trial candidate",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=task_spec.id,
        nodes=(
            PipelineNode(
                id="judge_semantics",
                operator_version_id=SemanticJudgmentOperator.spec.id,
                name="judge semantics",
                category="UNDERSTANDING",
                parameters={"judgment": judgment},
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
        ),
        created_from="processing_agent",
        required_constraint_ids=(constraint.id,),
        constraint_coverage=(
            ConstraintCoverage(
                constraint_id=constraint.id,
                node_id="judge_semantics",
                operator_version_id=SemanticJudgmentOperator.spec.id,
                evidence_type="visual_semantic_judgment",
            ),
        ),
    )
    library = build_operator_library(include_datajuicer=False)
    library.runtime.register(SemanticJudgmentOperator())

    observation = PipelineTrialRunner(library).run(
        PipelineTrialRequest(
            task_spec=task_spec,
            pipeline=pipeline,
            sample_paths=(str(source),),
        )
    )

    assert observation.status == PipelineTrialStatus.PASSED
    assert observation.constraint_results[0].status == ConstraintTrialStatus.PASSED


def test_trial_executes_on_isolated_copies_without_mutating_sources(
    tmp_path,
) -> None:
    source = tmp_path / "asset.txt"
    source.write_text("original", encoding="utf-8")
    task_spec = TaskSpecVersion(
        id="task_spec_source_isolation",
        version=1,
        created_by="user_1",
        change_reason="confirmed transformation trial",
        work_order_id="work_order_1",
        objective="test a transformation without mutating its source",
        data_sources=(
            DataSourceSpec(type="local_directory", uri=str(tmp_path)),
        ),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id="pipeline_source_isolation",
        family_id="pipeline_balanced",
        version=1,
        created_by="processing_agent",
        change_reason="transformation trial candidate",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=task_spec.id,
        nodes=(
            PipelineNode(
                id="transform",
                operator_version_id=MutatingTrialOperator.spec.id,
                name="transform working copy",
                category="TRANSFORMATION",
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
        ),
        created_from="processing_agent",
    )
    library = build_operator_library(include_datajuicer=False)
    library.runtime.register(MutatingTrialOperator())

    observation = PipelineTrialRunner(
        library,
        trial_root=tmp_path / "trials",
    ).run(
        PipelineTrialRequest(
            task_spec=task_spec,
            pipeline=pipeline,
            sample_paths=(str(source),),
        )
    )

    assert observation.status == PipelineTrialStatus.PASSED
    assert source.read_text(encoding="utf-8") == "original"


def test_trial_does_not_require_downstream_evidence_after_upstream_rejection(
    tmp_path,
) -> None:
    source = tmp_path / "low-quality.png"
    Image.new("RGB", (80, 80), color="white").save(source)
    quality = ConstraintContract(
        id="constraint_min_quality",
        source_text="quality score is at least 0.99",
        scope="asset",
        field="image.quality_score",
        operator="gte",
        value=0.99,
        unit="score",
        required_evidence_type="quality_score",
    )
    semantics = ConstraintContract(
        id="constraint_semantic_target",
        source_text="asset matches the semantic target",
        scope="asset",
        field="asset.semantic_target",
        operator="eq",
        value="confirmed-target",
        unit="category",
        required_evidence_type="visual_semantic_judgment",
    )
    task_spec = TaskSpecVersion(
        id="task_spec_short_circuit",
        version=1,
        created_by="user_1",
        change_reason="confirmed filtering requirements",
        work_order_id="work_order_1",
        objective="apply ordered filters",
        data_sources=(
            DataSourceSpec(type="local_directory", uri=str(tmp_path)),
        ),
        constraints=(quality, semantics),
        confirmed=True,
    )
    pipeline = PipelineVersion(
        id="pipeline_short_circuit",
        family_id="pipeline_balanced",
        version=1,
        created_by="processing_agent",
        change_reason="ordered filter trial candidate",
        strategy=PipelineStrategy.BALANCED,
        task_spec_version_id=task_spec.id,
        nodes=(
            PipelineNode(
                id="inspect_metadata",
                operator_version_id="builtin.decode_check:1",
                name="inspect metadata",
                category="INGESTION",
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
            PipelineNode(
                id="filter_quality",
                operator_version_id="builtin.quality_filter:1",
                name="filter quality",
                category="FILTERING",
                parameters={"confidence_threshold": 0.99},
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
            PipelineNode(
                id="judge_semantics",
                operator_version_id=SemanticJudgmentOperator.spec.id,
                name="judge semantics",
                category="UNDERSTANDING",
                parameters={"judgment": "match"},
                runtime_backend=RuntimeBackend.CPU,
                required=True,
            ),
        ),
        edges=(
            PipelineEdge(source="inspect_metadata", target="filter_quality"),
            PipelineEdge(source="filter_quality", target="judge_semantics"),
        ),
        created_from="processing_agent",
        required_constraint_ids=(quality.id, semantics.id),
        constraint_coverage=(
            ConstraintCoverage(
                constraint_id=quality.id,
                node_id="filter_quality",
                operator_version_id="builtin.quality_filter:1",
                evidence_type="quality_score",
            ),
            ConstraintCoverage(
                constraint_id=semantics.id,
                node_id="judge_semantics",
                operator_version_id=SemanticJudgmentOperator.spec.id,
                evidence_type="visual_semantic_judgment",
            ),
        ),
    )
    library = build_operator_library(include_datajuicer=False)
    library.runtime.register(SemanticJudgmentOperator())

    observation = PipelineTrialRunner(library).run(
        PipelineTrialRequest(
            task_spec=task_spec,
            pipeline=pipeline,
            sample_paths=(str(source),),
        )
    )

    by_constraint = {
        item.constraint_id: item for item in observation.constraint_results
    }
    assert observation.status == PipelineTrialStatus.PASSED
    assert by_constraint[quality.id].status == ConstraintTrialStatus.PASSED
    assert by_constraint[semantics.id].status == (
        ConstraintTrialStatus.NOT_EVALUATED
    )
    assert by_constraint[semantics.id].failure_code == "UPSTREAM_REJECTED"
