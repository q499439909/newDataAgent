from __future__ import annotations

import hashlib
import json
import platform
import time
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from ..domain.common.models import DomainModel, VersionedModel
from ..domain.operators import OperatorSpecVersion, RuntimeBackend
from .protocol import OperatorContext, OperatorInput
from .runtime import OperatorRuntime


class OperatorGoldenCase(DomainModel):
    id: str
    operator_version_id: str
    input_paths: tuple[str, ...]
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_decisions: tuple[str, ...]
    max_duration_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def decisions_match_inputs(self) -> "OperatorGoldenCase":
        if len(self.input_paths) != len(self.expected_decisions):
            raise ValueError("Golden case decisions must match its inputs")
        return self


class OperatorGoldenSet(VersionedModel):
    provider_id: str
    provider_version: str
    runtime_backend: RuntimeBackend
    cases: tuple[OperatorGoldenCase, ...]

    @model_validator(mode="after")
    def require_cases(self) -> "OperatorGoldenSet":
        if not self.cases:
            raise ValueError("Golden set must contain at least one case")
        return self


class GoldenCaseResult(DomainModel):
    case_id: str
    actual_decisions: tuple[str, ...]
    expected_decisions: tuple[str, ...]
    duration_seconds: float = Field(ge=0)
    passed: bool
    error: str | None = None


class OperatorBenchmarkReport(VersionedModel):
    golden_set_id: str
    provider_id: str
    provider_version: str
    runtime_backend: RuntimeBackend
    hardware: str
    case_results: tuple[GoldenCaseResult, ...]
    asset_count: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    assets_per_second: float = Field(ge=0)
    passed: bool
    evidence_sha256: str


class OperatorGoldenSetRunner:
    def __init__(self, runtime: OperatorRuntime) -> None:
        self.runtime = runtime

    def evaluate(
        self,
        golden_set: OperatorGoldenSet,
        *,
        context: OperatorContext,
        report_id: str,
        actor: str,
    ) -> OperatorBenchmarkReport:
        suite_started = time.monotonic()
        results: list[GoldenCaseResult] = []
        total_assets = 0
        for case in golden_set.cases:
            started = time.monotonic()
            inputs = tuple(
                OperatorInput(source_path=str(Path(path).resolve()), current_path=str(Path(path).resolve()))
                for path in case.input_paths
            )
            total_assets += len(inputs)
            actual: tuple[str, ...] = ()
            error: str | None = None
            try:
                node_id = f"golden_{case.id}"
                context.shared["active_node_id"] = node_id
                self.runtime.prepare_dataset_node(
                    operator_version_id=case.operator_version_id,
                    node_id=node_id,
                    context=context,
                    inputs=inputs,
                    parameters=case.parameters,
                    runtime_backend=golden_set.runtime_backend,
                )
                actual = tuple(
                    self.runtime.execute(
                        operator_version_id=case.operator_version_id,
                        context=context,
                        input_data=item,
                        parameters=case.parameters,
                        runtime_backend=golden_set.runtime_backend,
                    ).decision
                    for item in inputs
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            duration = time.monotonic() - started
            results.append(
                GoldenCaseResult(
                    case_id=case.id,
                    actual_decisions=actual,
                    expected_decisions=case.expected_decisions,
                    duration_seconds=duration,
                    passed=(
                        error is None
                        and actual == case.expected_decisions
                        and duration <= case.max_duration_seconds
                    ),
                    error=error,
                )
            )
        duration = time.monotonic() - suite_started
        evidence = {
            "golden_set": golden_set.model_dump(mode="json"),
            "results": [item.model_dump(mode="json") for item in results],
        }
        return OperatorBenchmarkReport(
            id=report_id,
            version=1,
            created_by=actor,
            change_reason="operator golden set and performance baseline",
            golden_set_id=golden_set.id,
            provider_id=golden_set.provider_id,
            provider_version=golden_set.provider_version,
            runtime_backend=golden_set.runtime_backend,
            hardware=f"{platform.system()} {platform.machine()} {platform.processor()}".strip(),
            case_results=tuple(results),
            asset_count=total_assets,
            duration_seconds=duration,
            assets_per_second=total_assets / duration if duration else 0,
            passed=all(item.passed for item in results),
            evidence_sha256=hashlib.sha256(
                json.dumps(evidence, sort_keys=True, ensure_ascii=True).encode("utf-8")
            ).hexdigest(),
        )


class ModelEvaluationEvidence(DomainModel):
    worker_id: str
    runtime_backend: RuntimeBackend
    model_id: str
    revision: str
    sha256: str
    code_license: str
    checkpoint_license: str
    license_reviewed: bool
    golden_set_sha256: str
    benchmark_passed: bool


def model_release_violations(
    spec: OperatorSpecVersion, evidence: ModelEvaluationEvidence | None
) -> tuple[str, ...]:
    requirement = spec.model_requirement
    violations: list[str] = []
    if requirement is None:
        return ("operator has no model requirement",)
    if evidence is None:
        return ("independent GPU worker evidence is missing",)
    if evidence.runtime_backend != RuntimeBackend.CUDA:
        violations.append("evaluation did not run on a CUDA worker")
    if not evidence.worker_id.strip():
        violations.append("GPU worker identity is missing")
    for name in ("revision", "sha256", "code_license", "checkpoint_license"):
        if getattr(evidence, name) != getattr(requirement, name):
            violations.append(f"evidence {name} does not match the frozen model requirement")
    if evidence.model_id != requirement.model_id:
        violations.append("evidence model id does not match the frozen model requirement")
    if len(requirement.sha256) != 64:
        violations.append("model SHA256 is not frozen")
    if requirement.revision.lower() in {"", "main", "master", "latest", "candidate"}:
        violations.append("model revision is not immutable")
    blocked_licenses = {"", "unknown", "review_required", "unreviewed"}
    if requirement.code_license.lower() in blocked_licenses:
        violations.append("code license has not been approved")
    if requirement.checkpoint_license.lower() in blocked_licenses:
        violations.append("checkpoint license has not been approved")
    if not evidence.license_reviewed:
        violations.append("license review evidence is missing")
    if len(evidence.golden_set_sha256) != 64 or not evidence.benchmark_passed:
        violations.append("GPU golden set or performance evaluation did not pass")
    return tuple(dict.fromkeys(violations))


def assert_model_release_eligible(
    spec: OperatorSpecVersion, evidence: ModelEvaluationEvidence | None
) -> None:
    violations = model_release_violations(spec, evidence)
    if violations:
        raise ValueError("Model operator is not release eligible: " + "; ".join(violations))


__all__ = [
    "GoldenCaseResult",
    "ModelEvaluationEvidence",
    "OperatorBenchmarkReport",
    "OperatorGoldenCase",
    "OperatorGoldenSet",
    "OperatorGoldenSetRunner",
    "assert_model_release_eligible",
    "model_release_violations",
]
