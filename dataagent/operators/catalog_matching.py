from __future__ import annotations

import re
import json
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from ..domain.operators import (
    OperatorSpecVersion,
    OperatorStatus,
    RuntimeBackend,
    RuntimeResolution,
)
from ..domain.specs import TaskCapabilitySpec
from .registry import OperatorRegistry
from .runtime_resolution import unavailable_runtime_resolution


class OperatorCatalogMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: str
    capability: str = ""
    operator_version_id: str
    provider_id: str = "datajuicer"
    provider_operator_ref: str
    display_name: str
    score: int = Field(ge=0)
    matched_terms: tuple[str, ...] = ()
    recall_sources: tuple[str, ...] = ()
    rule_score: int = Field(default=0, ge=0)
    keyword_score: int = Field(default=0, ge=0)
    semantic_score: int = Field(default=0, ge=0)
    recall_score: int = Field(default=0, ge=0)
    runtime_score: int = 0
    lifecycle_score: int = 0
    io_score: int = 0
    cost_score: int = 0
    cost_tier: str = "unknown"
    ranking_reasons: tuple[str, ...] = ()
    runtime_backend: RuntimeBackend
    status: OperatorStatus
    executable: bool
    blocked_reason: str | None = None
    runtime_resolution: RuntimeResolution | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


def _suggest_parameters(provider_ref: str, requirement: str) -> dict[str, Any]:
    # Retrieval reports candidates only. The Processing Agent binds parameters
    # from confirmed Constraint Contracts and the selected Operator schema.
    return {}

def _tokens(value: str) -> frozenset[str]:
    stopwords = {
        "and",
        "datajuicer",
        "for",
        "image",
        "images",
        "operator",
        "provider",
        "the",
        "with",
    }
    return frozenset(
        token
        for token in re.findall(r"[a-z0-9]+", value.lower().replace("_", " "))
        if len(token) >= 3 and token not in stopwords
    )


def _operator_terms(operator: OperatorSpecVersion) -> frozenset[str]:
    text = " ".join(
        (
            operator.id,
            operator.display_name,
            operator.summary,
            operator.description,
            operator.secondary_category,
            " ".join(operator.capability_tags),
            json.dumps(operator.parameter_schema, ensure_ascii=True),
        )
    )
    return frozenset({*_tokens(text), *operator.capability_tags})


def _capability_names(
    required_capabilities: tuple[str, ...],
    capability_requirements: Iterable[TaskCapabilitySpec | dict[str, Any]],
) -> tuple[str, ...]:
    values = list(required_capabilities)
    for item in capability_requirements:
        capability = (
            item.capability
            if isinstance(item, TaskCapabilitySpec)
            else str(item.get("capability", ""))
        )
        if capability and capability not in values:
            values.append(capability)
    return tuple(values)


class HybridOperatorCatalogMatcher:
    """Explainable rule, lexical, and capability-semantic catalog recall."""

    def __init__(
        self,
        registry: OperatorRegistry,
        *,
        provider_ids: frozenset[str] | None = None,
    ) -> None:
        self.registry = registry
        self.provider_ids = provider_ids

    def match(
        self,
        requirement: str,
        *,
        required_capabilities: tuple[str, ...] = (),
        capability_requirements: Iterable[TaskCapabilitySpec | dict[str, Any]] = (),
        allow_draft_candidates: bool = True,
    ) -> tuple[OperatorCatalogMatch, ...]:
        normalized = " ".join(requirement.lower().split())
        capabilities = _capability_names(
            required_capabilities,
            capability_requirements,
        )
        operators = [
            item
            for item in self.registry.search(include_drafts=True)
            if not self.provider_ids or item.provider.provider_id in self.provider_ids
        ]
        evidence: dict[tuple[str, str], dict[str, Any]] = {}

        def recall(
            capability: str,
            operator: OperatorSpecVersion,
            source: str,
            points: int,
            terms: Iterable[str],
        ) -> None:
            key = (capability, operator.id)
            item = evidence.setdefault(
                key,
                {
                    "operator": operator,
                    "sources": set(),
                    "terms": set(),
                    "scores": {"rule": 0, "keyword": 0, "semantic": 0},
                },
            )
            item["sources"].add(source)
            item["terms"].update(term for term in terms if term)
            item["scores"][source] = max(item["scores"][source], points)

        query_tokens = _tokens(normalized)
        for operator in operators:
            operator_terms = _operator_terms(operator)
            overlap = query_tokens.intersection(operator_terms)
            explicit_ref = operator.provider.provider_operator_ref in normalized
            if overlap or explicit_ref:
                lexical_capability = next(
                    (
                        capability
                        for capability in capabilities
                        if (
                            _tokens(capability).intersection(operator_terms)
                            or capability in operator.capability_tags
                            or capability == operator.secondary_category
                            or capability
                            == operator.provider.provider_operator_ref
                        )
                    ),
                    operator.secondary_category,
                )
                recall(
                    lexical_capability,
                    operator,
                    "keyword",
                    10 * len(overlap) + (40 if explicit_ref else 0),
                    (*sorted(overlap), operator.provider.provider_operator_ref if explicit_ref else ""),
                )

        for capability in capabilities:
            concepts = _tokens(capability)
            for operator in operators:
                operator_terms = _operator_terms(operator)
                matched = concepts.intersection(operator_terms)
                exact_metadata_match = (
                    capability in operator.capability_tags
                    or capability == operator.secondary_category
                    or capability
                    == operator.provider.provider_operator_ref
                )
                if exact_metadata_match:
                    matched = {*matched, capability}
                if matched:
                    recall(
                        capability,
                        operator,
                        "semantic",
                        25 * len(matched),
                        sorted(matched),
                    )

        matches = [
            self._to_match(
                capability,
                item,
                requirement=requirement,
                allow_draft_candidates=allow_draft_candidates,
            )
            for (capability, _), item in evidence.items()
        ]
        return tuple(
            sorted(matches, key=lambda item: (-item.score, item.operator_version_id))
        )

    @staticmethod
    def _to_match(
        capability: str,
        evidence: dict[str, Any],
        *,
        requirement: str,
        allow_draft_candidates: bool,
    ) -> OperatorCatalogMatch:
        operator: OperatorSpecVersion = evidence["operator"]
        backends = {profile.backend for profile in operator.supported_runtime_profiles}
        backend = (
            RuntimeBackend.CPU
            if RuntimeBackend.CPU in backends
            else RuntimeBackend.REMOTE
            if RuntimeBackend.REMOTE in backends
            else RuntimeBackend.CUDA
            if RuntimeBackend.CUDA in backends
            else next(iter(backends))
        )
        blocked_reason = None
        runtime_resolution = None
        if "image" not in operator.capability_tags:
            blocked_reason = "The current Agent only executes image operators"
        elif backend != RuntimeBackend.CPU:
            blocked_reason = f"Runtime backend {backend.value} is not available"
            runtime_resolution = unavailable_runtime_resolution(backend)
        elif operator.status == OperatorStatus.DRAFT and not allow_draft_candidates:
            blocked_reason = "Draft candidate execution is disabled"
        scores = evidence["scores"]
        return OperatorCatalogMatch(
            intent=capability,
            capability=capability,
            operator_version_id=operator.id,
            provider_id=operator.provider.provider_id,
            provider_operator_ref=operator.provider.provider_operator_ref,
            display_name=operator.display_name,
            score=sum(scores.values()),
            matched_terms=tuple(sorted(evidence["terms"])),
            recall_sources=tuple(sorted(evidence["sources"])),
            rule_score=scores["rule"],
            keyword_score=scores["keyword"],
            semantic_score=scores["semantic"],
            recall_score=sum(scores.values()),
            runtime_backend=backend,
            status=operator.status,
            executable=blocked_reason is None,
            blocked_reason=blocked_reason,
            runtime_resolution=runtime_resolution,
            parameters=_suggest_parameters(
                operator.provider.provider_operator_ref,
                requirement,
            ),
        )


class DataJuicerCatalogMatcher(HybridOperatorCatalogMatcher):
    def __init__(self, registry: OperatorRegistry) -> None:
        super().__init__(registry, provider_ids=frozenset({"datajuicer"}))


__all__ = [
    "DataJuicerCatalogMatcher",
    "HybridOperatorCatalogMatcher",
    "OperatorCatalogMatch",
]
