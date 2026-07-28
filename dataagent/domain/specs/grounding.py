from __future__ import annotations

import re

from pydantic import Field

from ..common.models import DomainModel
from .models import RequirementDraft


class RequirementGroundingViolation(DomainModel):
    code: str = Field(min_length=1)
    source_text: str
    message: str = Field(min_length=1)


class RequirementGroundingObservation(DomainModel):
    ok: bool
    violations: tuple[RequirementGroundingViolation, ...] = ()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _clauses(requirement: str) -> tuple[str, ...]:
    clauses: list[str] = []
    for raw_clause in re.split(r"[；;\n。]+", requirement):
        clause = re.sub(r"^\s*\d+\s*[.、]\s*", "", raw_clause).strip()
        if len(_compact(clause)) >= 4:
            clauses.append(clause)
    return tuple(clauses)


def validate_requirement_draft_grounding(
    requirement: str,
    draft: RequirementDraft,
) -> RequirementGroundingObservation:
    """Validate source coverage and references without interpreting business words."""

    compact_requirement = _compact(requirement)
    violations: list[RequirementGroundingViolation] = []
    constraint_ids = {item.id for item in draft.constraints}

    for constraint in draft.constraints:
        if _compact(constraint.source_text) not in compact_requirement:
            violations.append(
                RequirementGroundingViolation(
                    code="UNGROUNDED_CONSTRAINT",
                    source_text=constraint.source_text,
                    message=(
                        f"{constraint.id}.source_text is not an exact span "
                        "of the requirement"
                    ),
                )
            )

    for trace in draft.clause_traces:
        compact_span = _compact(trace.source_text)
        if compact_span not in compact_requirement:
            violations.append(
                RequirementGroundingViolation(
                    code="UNGROUNDED_CLAUSE_TRACE",
                    source_text=trace.source_text,
                    message="Clause trace is not an exact span of the requirement",
                )
            )
        missing_refs = tuple(
            item for item in trace.constraint_refs if item not in constraint_ids
        )
        if missing_refs:
            violations.append(
                RequirementGroundingViolation(
                    code="UNKNOWN_CONSTRAINT_REFERENCE",
                    source_text=trace.source_text,
                    message=(
                        "Clause trace references unknown constraints: "
                        + ", ".join(missing_refs)
                    ),
                )
            )
        if trace.role in {"constraint", "definition"} and not trace.constraint_refs:
            violations.append(
                RequirementGroundingViolation(
                    code="MISSING_CONSTRAINT_REFERENCE",
                    source_text=trace.source_text,
                    message=f"{trace.role} clause must reference a constraint",
                )
            )

    grounded_spans = tuple(_compact(item.source_text) for item in draft.clause_traces)
    for clause in _clauses(requirement):
        compact_clause = _compact(clause)
        if not any(span and span in compact_clause for span in grounded_spans):
            violations.append(
                RequirementGroundingViolation(
                    code="UNGROUNDED_REQUIREMENT_CLAUSE",
                    source_text=clause,
                    message="Requirement clause has no ClauseTrace",
                )
            )

    constraint_trace_refs = {
        constraint_id
        for trace in draft.clause_traces
        if trace.role == "constraint"
        for constraint_id in trace.constraint_refs
    }
    for constraint in draft.constraints:
        if constraint.id not in constraint_trace_refs:
            violations.append(
                RequirementGroundingViolation(
                    code="UNTRACED_CONSTRAINT",
                    source_text=constraint.source_text,
                    message=(
                        f"{constraint.id} is not referenced by a constraint ClauseTrace"
                    ),
                )
            )

    return RequirementGroundingObservation(
        ok=not violations,
        violations=tuple(violations),
    )


__all__ = [
    "RequirementGroundingObservation",
    "RequirementGroundingViolation",
    "validate_requirement_draft_grounding",
]
