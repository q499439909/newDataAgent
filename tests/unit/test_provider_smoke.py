from __future__ import annotations

import json
from pathlib import Path

from dataagent.acceptance import (
    collect_acceptance_run_record,
    run_remote_vlm_smoke,
    write_provider_smoke_record,
)
from dataagent.domain.operators import (
    ImplementationSpec,
    ImplementationType,
    OperatorCategory,
    OperatorSpecVersion,
    ProviderRef,
    RuntimeBackend,
    RuntimeProfile,
)
from dataagent.operators.protocol import OperatorResult
from dataagent.operators.providers.protocol import (
    ProviderExecuteResult,
    ProviderValidationResult,
)


class FakeProvider:
    provider_id = "datajuicer"
    provider_version = "1.5.3"

    def __init__(self, tags):
        self.tags = tags

    def validate(self, provider_operator_ref, parameters, runtime_backend):
        assert provider_operator_ref == "image_tagging_vlm_mapper"
        assert runtime_backend == RuntimeBackend.REMOTE
        return ProviderValidationResult(ok=True, normalized_parameters=parameters)

    def execute(self, request):
        request.context.shared["event_sink"]("provider_process_started", {})
        request.context.shared["event_sink"]("provider_process_completed", {})
        return ProviderExecuteResult(
            ok=True,
            result=OperatorResult(
                labels={
                    "datajuicer_output": {
                        "image_tags": self.tags,
                        "_dataagent_output_contract": "image_tag_set:1",
                    }
                }
            ),
            duration_seconds=0.25,
            stdout_tail="processed 1 asset",
        )


def _operator() -> OperatorSpecVersion:
    return OperatorSpecVersion(
        id="datajuicer.image_tagging_vlm_mapper.remote_api:2",
        version=2,
        created_by="system",
        change_reason="test",
        family_id="datajuicer.image_tagging_vlm_mapper",
        display_name="Remote VLM",
        summary="test",
        description="test",
        primary_category=OperatorCategory.UNDERSTANDING,
        secondary_category="classification",
        capability_tags=frozenset({"image", "api", "remote"}),
        input_schema="ImageAssetRef",
        output_schema="ImageTagSet",
        parameter_schema={"type": "object", "properties": {}},
        provider=ProviderRef(
            provider_id="datajuicer",
            provider_version="1.5.3",
            provider_operator_ref="image_tagging_vlm_mapper",
        ),
        implementation=ImplementationSpec(
            implementation_type=ImplementationType.EXTERNAL_SERVICE,
            entrypoint="test",
        ),
        supported_runtime_profiles=(
            RuntimeProfile(backend=RuntimeBackend.REMOTE),
        ),
        implementation_ref="test",
        owner_id="system",
    )


def test_remote_provider_smoke_requires_nonempty_structured_tags(
    tmp_path: Path,
) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image")
    parameters = {
        "api_or_hf_model": "qwen3.7-plus",
        "tag_field_name": "image_tags",
    }

    passed = run_remote_vlm_smoke(
        provider=FakeProvider(["cat"]),  # type: ignore[arg-type]
        operator=_operator(),
        image_path=image,
        parameters=parameters,
    )
    failed = run_remote_vlm_smoke(
        provider=FakeProvider([]),  # type: ignore[arg-type]
        operator=_operator(),
        image_path=image,
        parameters=parameters,
    )

    assert passed.status == "PASSED"
    assert passed.remote_call_count == 1
    assert passed.structured_output["image_tags"] == ["cat"]
    assert passed.event_types == (
        "provider_process_started",
        "provider_process_completed",
    )
    assert failed.status == "FAILED"
    assert failed.failure_reason_codes == ("EMPTY_STRUCTURED_OUTPUT",)


def test_provider_smoke_record_is_written_atomically(tmp_path: Path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image")
    record = run_remote_vlm_smoke(
        provider=FakeProvider(["dog"]),  # type: ignore[arg-type]
        operator=_operator(),
        image_path=image,
        parameters={
            "api_or_hf_model": "qwen3.7-plus",
            "tag_field_name": "image_tags",
        },
    )
    destination = tmp_path / "records" / "smoke.json"

    written = write_provider_smoke_record(record, destination)

    assert written == destination.resolve()
    assert json.loads(destination.read_text(encoding="utf-8"))["status"] == "PASSED"
    assert not list(destination.parent.glob("*.tmp"))


def test_acceptance_run_record_requires_grounded_provider_and_qc_evidence() -> None:
    record = collect_acceptance_run_record(
        run={
            "id": "run_1",
            "status": "SUCCEEDED",
            "pipeline_version_id": "pipeline_1",
            "created_at": "2026-07-23T00:00:00Z",
            "updated_at": "2026-07-23T00:00:10Z",
        },
        dataset={
            "id": "dataset_1",
            "assets": [
                {
                    "source_uri": "D:/images/cat.jpg",
                    "decision": "keep",
                    "labels": {
                        "datajuicer_output": {"image_tags": ["cat"]}
                    },
                }
            ],
        },
        qc_report={"id": "qc_1", "status": "PASSED", "reason_codes": []},
        node_results=[
            {
                "operator_version_id": (
                    "datajuicer.image_tagging_vlm_mapper.remote_api:2"
                ),
                "reason_codes": [],
            }
        ],
        events=[
            {
                "event_type": "provider_process_completed",
                "details": {
                    "stdout_tail": "done",
                    "stderr_tail": "HTTP 200",
                },
            }
        ],
        provider_id="datajuicer",
        provider_version="1.5.3",
        model_version="qwen3.7-plus",
        export_path="D:/exports/export_1",
    )

    assert record.status == "PASSED"
    assert record.duration_seconds == 10
    assert record.remote_call_count == 1
    assert record.classifications == {"cat.jpg": ("cat",)}
    assert record.stdout_summaries == ("done",)
    assert record.stderr_summaries == ("HTTP 200",)
