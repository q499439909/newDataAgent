from __future__ import annotations

from dataclasses import dataclass

from ..domain.operators import (
    OperatorCategory,
    OperatorSpecVersion,
    OperatorStatus,
    RuntimeBackend,
)
from ..domain.specs import TaskCapabilitySpec
from .registry import OperatorRegistry


class CapabilityGapError(LookupError):
    pass


@dataclass(frozen=True)
class OperatorRequirement:
    capability: str
    category: OperatorCategory | None = None
    secondary_category: str | None = None
    runtime_backend: RuntimeBackend | None = None
    allow_drafts: bool = False


_STATUS_RANK = {
    OperatorStatus.PUBLIC_RELEASE: 0,
    OperatorStatus.PERSONAL_RELEASE: 1,
    OperatorStatus.EVALUATED: 2,
    OperatorStatus.PROVIDER_AVAILABLE: 3,
    OperatorStatus.DRAFT: 4,
    OperatorStatus.DEPRECATED: 99,
}


class OperatorSelector:
    """Select an Operator only from explicit catalog metadata."""

    def __init__(self, registry: OperatorRegistry) -> None:
        self.registry = registry

    def select(self, requirement: OperatorRequirement) -> OperatorSpecVersion:
        candidates = self.registry.search(
            category=requirement.category,
            secondary_category=requirement.secondary_category,
            tags={requirement.capability},
            include_drafts=requirement.allow_drafts,
        )
        candidates = [
            item for item in candidates if item.status != OperatorStatus.DEPRECATED
        ]
        if requirement.runtime_backend is not None:
            candidates = [
                item
                for item in candidates
                if requirement.runtime_backend
                in {profile.backend for profile in item.supported_runtime_profiles}
            ]
        if not candidates:
            raise CapabilityGapError(
                "No operator satisfies capability "
                f"'{requirement.capability}' with category={requirement.category}, "
                f"secondary={requirement.secondary_category}, "
                f"backend={requirement.runtime_backend}"
            )
        return min(
            candidates,
            key=lambda item: (
                _STATUS_RANK[item.status],
                item.version * -1,
                item.id,
            ),
        )


def exclude_task_capabilities(
    capabilities: tuple[TaskCapabilitySpec, ...],
    disabled_capabilities: set[str],
) -> tuple[TaskCapabilitySpec, ...]:
    """Apply an explicit user disable-list to a model-authored capability DAG."""

    disabled = disabled_capabilities.difference({"image_decode", "manifest"})
    by_id = {item.id: item for item in capabilities}

    def retained_dependencies(capability_id: str) -> tuple[str, ...]:
        item = by_id[capability_id]
        resolved: list[str] = []
        for dependency in item.depends_on:
            dependency_item = by_id[dependency]
            if dependency in disabled or dependency_item.capability in disabled:
                resolved.extend(retained_dependencies(dependency))
            else:
                resolved.append(dependency)
        return tuple(dict.fromkeys(resolved))

    return tuple(
        item.model_copy(update={"depends_on": retained_dependencies(item.id)})
        for item in capabilities
        if item.id not in disabled and item.capability not in disabled
    )


__all__ = [
    "CapabilityGapError",
    "OperatorRequirement",
    "OperatorSelector",
    "exclude_task_capabilities",
]
