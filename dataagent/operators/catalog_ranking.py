from __future__ import annotations

from dataclasses import dataclass

from ..domain.operators import OperatorStatus, RuntimeBackend
from .catalog_matching import OperatorCatalogMatch
from .registry import OperatorRegistry
from .runtime_resolution import unavailable_runtime_resolution


_LIFECYCLE_SCORES = {
    OperatorStatus.PUBLIC_RELEASE: 45,
    OperatorStatus.PERSONAL_RELEASE: 40,
    OperatorStatus.EVALUATED: 25,
    OperatorStatus.PROVIDER_AVAILABLE: 20,
    OperatorStatus.DRAFT: 5,
    OperatorStatus.DEPRECATED: -200,
}

_LIFECYCLE_ORDER = {
    OperatorStatus.PUBLIC_RELEASE: 0,
    OperatorStatus.PERSONAL_RELEASE: 1,
    OperatorStatus.EVALUATED: 2,
    OperatorStatus.PROVIDER_AVAILABLE: 3,
    OperatorStatus.DRAFT: 4,
    OperatorStatus.DEPRECATED: 5,
}

_EXPECTED_OUTPUTS: dict[str, frozenset[str]] = {
    "image_classification": frozenset(
        {"ImageTagSet", "ImageClassification", "ProviderDecision"}
    ),
    "perceptual_deduplication": frozenset({"ProviderDecision"}),
    "image_quality": frozenset({"ProviderDecision", "EnrichedImageAsset"}),
    "authenticity_assessment": frozenset(
        {"ImageAuthenticityAssessment", "ImageTagSet", "ProviderDecision"}
    ),
    "visual_semantic_selection": frozenset({"ImageTagSet", "ProviderDecision"}),
    "segmentation": frozenset({"ImageSegmentation", "ProviderDecision"}),
    "watermark_detection": frozenset(
        {"WatermarkAssessment", "ProviderDecision"}
    ),
}


@dataclass(frozen=True)
class OperatorRankingPolicy:
    available_runtime_backends: frozenset[RuntimeBackend] = frozenset(
        {RuntimeBackend.CPU}
    )
    allow_draft_candidates: bool = False
    input_schema: str = "ImageAssetRef"
    cost_preference: str = "balanced"


class OperatorCandidateRanker:
    def __init__(self, registry: OperatorRegistry) -> None:
        self.registry = registry

    def rank(
        self,
        candidates: tuple[OperatorCatalogMatch, ...],
        *,
        policy: OperatorRankingPolicy,
    ) -> tuple[OperatorCatalogMatch, ...]:
        ranked = [self._rank_one(candidate, policy) for candidate in candidates]
        return tuple(
            sorted(
                ranked,
                key=lambda item: (
                    not item.executable,
                    _LIFECYCLE_ORDER[item.status],
                    -item.score,
                    item.operator_version_id,
                ),
            )
        )

    def _rank_one(
        self,
        candidate: OperatorCatalogMatch,
        policy: OperatorRankingPolicy,
    ) -> OperatorCatalogMatch:
        operator = self.registry.get(candidate.operator_version_id)
        supported = {
            profile.backend for profile in operator.supported_runtime_profiles
        }
        backend = self._select_backend(supported, policy.available_runtime_backends)
        runtime_available = backend in policy.available_runtime_backends
        runtime_score = 55 if runtime_available else -120
        lifecycle_score = _LIFECYCLE_SCORES[operator.status]
        io_score, io_reason = self._io_score(
            candidate.capability or candidate.intent,
            input_schema=policy.input_schema,
            operator_input=operator.input_schema,
            operator_output=operator.output_schema,
        )
        cost_tier, cost_score = self._cost_score(backend, policy.cost_preference)
        blocked_reason = None
        runtime_resolution = None
        if "image" not in operator.capability_tags:
            blocked_reason = "The current Agent only executes image operators"
        elif not runtime_available:
            blocked_reason = f"Runtime backend {backend.value} is not available"
            runtime_resolution = unavailable_runtime_resolution(backend)
        elif operator.status == OperatorStatus.DEPRECATED:
            blocked_reason = "Deprecated operators cannot be selected"
        elif operator.status == OperatorStatus.DRAFT and not policy.allow_draft_candidates:
            blocked_reason = "Draft candidate execution is disabled"
        elif io_score < 0:
            blocked_reason = io_reason
        reasons = (
            f"runtime:{backend.value}:{'available' if runtime_available else 'unavailable'}",
            f"lifecycle:{operator.status.value}",
            io_reason,
            f"cost:{cost_tier}",
        )
        score = max(
            0,
            candidate.recall_score
            + runtime_score
            + lifecycle_score
            + io_score
            + cost_score,
        )
        return candidate.model_copy(
            update={
                "score": score,
                "runtime_backend": backend,
                "runtime_score": runtime_score,
                "lifecycle_score": lifecycle_score,
                "io_score": io_score,
                "cost_score": cost_score,
                "cost_tier": cost_tier,
                "ranking_reasons": reasons,
                "status": operator.status,
                "executable": blocked_reason is None,
                "blocked_reason": blocked_reason,
                "runtime_resolution": runtime_resolution,
            }
        )

    @staticmethod
    def _select_backend(
        supported: set[RuntimeBackend],
        available: frozenset[RuntimeBackend],
    ) -> RuntimeBackend:
        for backend in (
            RuntimeBackend.CPU,
            RuntimeBackend.REMOTE,
            RuntimeBackend.CUDA,
            RuntimeBackend.MOCK,
        ):
            if backend in supported and backend in available:
                return backend
        for backend in (
            RuntimeBackend.CPU,
            RuntimeBackend.REMOTE,
            RuntimeBackend.CUDA,
            RuntimeBackend.MOCK,
        ):
            if backend in supported:
                return backend
        raise ValueError("Operator has no supported runtime backend")

    @staticmethod
    def _io_score(
        capability: str,
        *,
        input_schema: str,
        operator_input: str,
        operator_output: str,
    ) -> tuple[int, str]:
        if operator_input != input_schema:
            return -100, f"io:input_mismatch:{operator_input}"
        expected = _EXPECTED_OUTPUTS.get(capability)
        if expected is not None and operator_output not in expected:
            return -60, f"io:output_mismatch:{operator_output}"
        return 35, f"io:compatible:{operator_input}->{operator_output}"

    @staticmethod
    def _cost_score(backend: RuntimeBackend, preference: str) -> tuple[str, int]:
        if backend == RuntimeBackend.CPU:
            return "low", 20
        if backend == RuntimeBackend.REMOTE:
            return "metered", 0 if preference == "quality" else -10
        if backend == RuntimeBackend.CUDA:
            return "high_compute", -5
        return "development_only", -30


__all__ = ["OperatorCandidateRanker", "OperatorRankingPolicy"]
