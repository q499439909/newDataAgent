from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from ..domain.common import utc_now
from ..domain.common.models import DomainModel
from ..domain.operators import OperatorSpecVersion, RuntimeBackend
from ..operators.protocol import OperatorContext, OperatorInput
from ..operators.providers.protocol import OperatorProvider, ProviderExecuteRequest


class ProviderSmokeRecord(DomainModel):
    id: str
    status: str
    run_id: str
    dataset_version_id: str | None = None
    qc_report_id: str | None = None
    provider_id: str
    provider_version: str
    operator_version_id: str
    model_version: str
    source_uri: str
    source_sha256: str
    duration_seconds: float = Field(ge=0)
    remote_call_count: int = Field(ge=0)
    failure_reason_codes: tuple[str, ...] = ()
    stdout_summary: str = ""
    stderr_summary: str = ""
    structured_output: dict[str, Any] = Field(default_factory=dict)
    event_types: tuple[str, ...] = ()
    export_path: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class AcceptanceRunRecord(DomainModel):
    id: str
    status: str
    run_id: str
    dataset_version_id: str
    qc_report_id: str
    pipeline_version_id: str
    provider_id: str
    provider_version: str
    model_version: str
    duration_seconds: float = Field(ge=0)
    remote_call_count: int = Field(ge=0)
    node_result_count: int = Field(ge=0)
    failure_reason_codes: tuple[str, ...] = ()
    classifications: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    stdout_summaries: tuple[str, ...] = ()
    stderr_summaries: tuple[str, ...] = ()
    export_path: str
    created_at: datetime = Field(default_factory=utc_now)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_provider_smoke_record(
    record: ProviderSmokeRecord | AcceptanceRunRecord,
    destination: Path,
) -> Path:
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Provider smoke record already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def collect_acceptance_run_record(
    *,
    run: dict[str, Any],
    dataset: dict[str, Any],
    qc_report: dict[str, Any],
    node_results: list[dict[str, Any]],
    events: list[dict[str, Any]],
    provider_id: str,
    provider_version: str,
    model_version: str,
    export_path: str,
) -> AcceptanceRunRecord:
    provider_events = [
        item for item in events if item.get("event_type") == "provider_process_completed"
    ]
    reasons = tuple(
        dict.fromkeys(
            [
                *(qc_report.get("reason_codes") or []),
                *(
                    reason
                    for item in node_results
                    for reason in item.get("reason_codes") or []
                ),
            ]
        )
    )
    classifications = {
        str(Path(asset["source_uri"]).name): tuple(
            (
                asset.get("labels", {})
                .get("datajuicer_output", {})
                .get("image_tags", [])
            )
        )
        for asset in dataset.get("assets", [])
        if asset.get("decision") == "keep"
    }
    stdout = tuple(
        str(item.get("details", {}).get("stdout_tail") or "")
        for item in provider_events
        if item.get("details", {}).get("stdout_tail")
    )
    stderr = tuple(
        str(item.get("details", {}).get("stderr_tail") or "")
        for item in provider_events
        if item.get("details", {}).get("stderr_tail")
    )
    passed = (
        run.get("status") == "SUCCEEDED"
        and qc_report.get("status") == "PASSED"
        and bool(provider_events)
        and not reasons
        and all(classifications.values())
    )
    duration = (
        _as_datetime(run["updated_at"]) - _as_datetime(run["created_at"])
    ).total_seconds()
    return AcceptanceRunRecord(
        id=f"acceptance_run_{uuid.uuid4().hex[:16]}",
        status="PASSED" if passed else "FAILED",
        run_id=run["id"],
        dataset_version_id=dataset["id"],
        qc_report_id=qc_report["id"],
        pipeline_version_id=run["pipeline_version_id"],
        provider_id=provider_id,
        provider_version=provider_version,
        model_version=model_version,
        duration_seconds=max(0, duration),
        remote_call_count=len(provider_events),
        node_result_count=len(node_results),
        failure_reason_codes=reasons,
        classifications=classifications,
        stdout_summaries=stdout,
        stderr_summaries=stderr,
        export_path=export_path,
    )


def run_remote_vlm_smoke(
    *,
    provider: OperatorProvider,
    operator: OperatorSpecVersion,
    image_path: Path,
    parameters: dict[str, Any],
    owner_id: str = "acceptance",
) -> ProviderSmokeRecord:
    image_path = image_path.expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    run_id = f"smoke_{uuid.uuid4().hex[:16]}"
    events: list[str] = []

    def event_sink(event_type: str, details: dict[str, Any]) -> None:
        del details
        events.append(event_type)

    validation = provider.validate(
        operator.provider.provider_operator_ref,
        parameters,
        RuntimeBackend.REMOTE,
    )
    if not validation.ok:
        return ProviderSmokeRecord(
            id=f"provider_smoke_{uuid.uuid4().hex[:16]}",
            status="FAILED",
            run_id=run_id,
            provider_id=provider.provider_id,
            provider_version=provider.provider_version,
            operator_version_id=operator.id,
            model_version=str(parameters.get("api_or_hf_model") or "unknown"),
            source_uri=str(image_path),
            source_sha256=_sha256(image_path),
            duration_seconds=0,
            remote_call_count=0,
            failure_reason_codes=("PROVIDER_VALIDATION_FAILED",),
            stderr_summary="; ".join(validation.errors),
        )
    started = time.perf_counter()
    response = provider.execute(
        ProviderExecuteRequest(
            provider_operator_ref=operator.provider.provider_operator_ref,
            runtime_backend=RuntimeBackend.REMOTE,
            context=OperatorContext(
                run_id=run_id,
                work_order_id="acceptance_provider_smoke",
                owner_id=owner_id,
                purpose="development",
                shared={"event_sink": event_sink},
            ),
            input_data=OperatorInput(
                source_path=str(image_path),
                current_path=str(image_path),
            ),
            parameters=validation.normalized_parameters or parameters,
        )
    )
    duration = response.duration_seconds or (time.perf_counter() - started)
    output = (
        response.result.labels.get("datajuicer_output", {})
        if response.result is not None
        else {}
    )
    tag_field = str(parameters.get("tag_field_name") or "image_tags")
    tags = output.get(tag_field) if isinstance(output, dict) else None
    reasons: list[str] = []
    if not response.ok or response.result is None:
        reasons.append(response.error_type or "PROVIDER_EXECUTION_FAILED")
    elif not isinstance(tags, list) or not tags:
        reasons.append("EMPTY_STRUCTURED_OUTPUT")
    return ProviderSmokeRecord(
        id=f"provider_smoke_{uuid.uuid4().hex[:16]}",
        status="PASSED" if not reasons else "FAILED",
        run_id=run_id,
        provider_id=provider.provider_id,
        provider_version=provider.provider_version,
        operator_version_id=operator.id,
        model_version=str(parameters.get("api_or_hf_model") or "unknown"),
        source_uri=str(image_path),
        source_sha256=_sha256(image_path),
        duration_seconds=duration,
        remote_call_count=1,
        failure_reason_codes=tuple(reasons),
        stdout_summary=response.stdout_tail,
        stderr_summary=response.stderr_tail or response.message,
        structured_output=output if isinstance(output, dict) else {},
        event_types=tuple(events),
    )
