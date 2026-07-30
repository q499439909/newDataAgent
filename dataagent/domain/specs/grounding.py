from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

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
    boundaries = (
        r"(?:[；;\n。！？!?]+|"
        r"(?=\d+\s*[.、](?:\s+|[\u4e00-\u9fff])))"
    )
    for raw_clause in re.split(boundaries, requirement):
        clause = re.sub(r"^\s*\d+\s*[.、]\s*", "", raw_clause).strip()
        if len(_compact(clause)) >= 4:
            clauses.append(clause)
    return tuple(clauses)


def build_requirement_source_clauses(
    requirement: str,
) -> tuple[dict[str, str], ...]:
    """Create stable references so a model never has to reproduce source text."""

    return tuple(
        {"id": f"clause_{index:03d}", "text": text}
        for index, text in enumerate(_clauses(requirement), start=1)
    )


def canonicalize_requirement_draft_payload(
    payload: dict[str, Any],
    clauses: tuple[dict[str, str], ...],
    *,
    previous_draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assign durable Constraint IDs and direct ClauseTrace references.

    The model owns semantic decomposition. The server owns sequential IDs and
    the mechanically derivable relation between a Constraint's source clause
    and that clause's constraint trace.
    """

    normalized = deepcopy(payload)
    raw_constraints = normalized.get("constraints")
    constraints = raw_constraints if isinstance(raw_constraints, list) else []
    old_to_new: dict[str, str] = {}
    constraints_by_clause: dict[str, list[str]] = {}

    def identity(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            item.get("source_clause_id"),
            item.get("scope"),
            item.get("field"),
            item.get("operator"),
            item.get("unit"),
            item.get("required_evidence_type"),
        )

    previous_by_identity: dict[tuple[Any, ...], list[str]] = {}
    used_ids: set[str] = set()
    if isinstance(previous_draft, dict):
        for item in previous_draft.get("constraints", ()):
            if not isinstance(item, dict):
                continue
            constraint_id = item.get("id")
            if not isinstance(constraint_id, str) or not re.fullmatch(
                r"C\d{2,}",
                constraint_id,
            ):
                continue
            previous_by_identity.setdefault(identity(item), []).append(
                constraint_id
            )
            used_ids.add(constraint_id)
    next_number = max(
        (int(item[1:]) for item in used_ids),
        default=0,
    ) + 1

    for constraint in constraints:
        if not isinstance(constraint, dict):
            continue
        previous_ids = previous_by_identity.get(identity(constraint), [])
        if previous_ids:
            new_id = previous_ids.pop(0)
        else:
            while f"C{next_number:02d}" in used_ids:
                next_number += 1
            new_id = f"C{next_number:02d}"
            next_number += 1
        used_ids.add(new_id)
        old_id = constraint.get("id")
        if isinstance(old_id, str) and old_id:
            old_to_new[old_id] = new_id
        constraint["id"] = new_id
        clause_id = constraint.get("source_clause_id")
        if isinstance(clause_id, str) and clause_id:
            constraints_by_clause.setdefault(clause_id, []).append(new_id)

    known_ids = {
        str(item.get("id"))
        for item in constraints
        if isinstance(item, dict) and item.get("id")
    }
    raw_traces = normalized.get("clause_traces")
    traces = raw_traces if isinstance(raw_traces, list) else []
    constraint_trace_clauses: set[str] = set()
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        refs = trace.get("constraint_refs")
        remapped_refs: list[str] = []
        if isinstance(refs, (list, tuple)):
            for value in refs:
                reference = old_to_new.get(str(value), str(value))
                if reference not in remapped_refs:
                    remapped_refs.append(reference)
        clause_id = trace.get("source_clause_id")
        if trace.get("role") == "constraint" and isinstance(clause_id, str):
            for reference in constraints_by_clause.get(clause_id, ()):
                if reference not in remapped_refs:
                    remapped_refs.append(reference)
            if remapped_refs:
                constraint_trace_clauses.add(clause_id)
        trace["constraint_refs"] = remapped_refs

    clause_text_by_id = {item["id"]: item["text"] for item in clauses}
    for clause_id, refs in constraints_by_clause.items():
        if clause_id in constraint_trace_clauses:
            continue
        traces.append(
            {
                "source_text": clause_text_by_id.get(clause_id, clause_id),
                "source_clause_id": clause_id,
                "role": "constraint",
                "constraint_refs": [item for item in refs if item in known_ids],
                "normalized_effect": {},
            }
        )
    normalized["constraints"] = constraints
    normalized["clause_traces"] = traces
    return normalized


def hydrate_requirement_draft_sources(
    draft: RequirementDraft,
    clauses: tuple[dict[str, str], ...],
) -> RequirementDraft:
    """Replace model-authored display text with authoritative referenced clauses."""

    by_id = {item["id"]: item["text"] for item in clauses}

    def source_text(reference: str | None, fallback: str) -> str:
        if not reference:
            return fallback
        try:
            return by_id[reference]
        except KeyError as exc:
            raise ValueError(f"Unknown requirement source clause: {reference}") from exc

    constraints = tuple(
        item.model_copy(
            update={
                "source_text": source_text(
                    item.source_clause_id,
                    item.source_text,
                )
            }
        )
        for item in draft.constraints
    )
    traces = tuple(
        item.model_copy(
            update={
                "source_text": source_text(
                    item.source_clause_id,
                    item.source_text,
                )
            }
        )
        for item in draft.clause_traces
    )
    gaps = tuple(
        item.model_copy(
            update={
                "source_texts": tuple(
                    source_text(reference, "")
                    for reference in item.source_clause_ids
                )
                if item.source_clause_ids
                else item.source_texts
            }
        )
        for item in draft.gaps
    )
    return draft.model_copy(
        update={
            "constraints": constraints,
            "clause_traces": traces,
            "gaps": gaps,
        }
    )


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

    for gap in draft.gaps:
        for source_text in gap.source_texts:
            if _compact(source_text) not in compact_requirement:
                violations.append(
                    RequirementGroundingViolation(
                        code="UNGROUNDED_REQUIREMENT_GAP",
                        source_text=source_text,
                        message=(
                            f"{gap.id}.source_texts contains text that is not "
                            "an exact span of the requirement conversation"
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
    "build_requirement_source_clauses",
    "canonicalize_requirement_draft_payload",
    "hydrate_requirement_draft_sources",
    "RequirementGroundingObservation",
    "RequirementGroundingViolation",
    "validate_requirement_draft_grounding",
]
