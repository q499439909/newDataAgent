from __future__ import annotations

from collections.abc import Iterable

from ..domain.operators import OperatorCategory, OperatorSpecVersion, OperatorStatus
from .categories import validate_secondary_category


class OperatorRegistry:
    """In-memory registry used by agents and tests; persistence is supplied by a repository."""

    def __init__(self, operators: Iterable[OperatorSpecVersion] = ()):
        self._versions: dict[str, OperatorSpecVersion] = {}
        for operator in operators:
            self.register(operator)

    def register(self, operator: OperatorSpecVersion) -> None:
        validate_secondary_category(operator.primary_category, operator.secondary_category)
        if operator.id in self._versions:
            raise ValueError(f"Operator version already registered: {operator.id}")
        self._versions[operator.id] = operator

    def get(self, version_id: str) -> OperatorSpecVersion:
        try:
            return self._versions[version_id]
        except KeyError as exc:
            raise KeyError(f"Operator version not found: {version_id}") from exc

    def search(
        self,
        *,
        category: OperatorCategory | None = None,
        secondary_category: str | None = None,
        tags: set[str] | None = None,
        include_drafts: bool = False,
    ) -> list[OperatorSpecVersion]:
        results = []
        for operator in self._versions.values():
            if not include_drafts and operator.status == OperatorStatus.DRAFT:
                continue
            if category is not None and operator.primary_category != category:
                continue
            if secondary_category is not None and operator.secondary_category != secondary_category:
                continue
            if tags and not tags.issubset(operator.capability_tags):
                continue
            results.append(operator)
        return sorted(results, key=lambda item: (item.display_name, item.version))

    def categories(self) -> dict[OperatorCategory, int]:
        counts = {category: 0 for category in OperatorCategory}
        for operator in self._versions.values():
            counts[operator.primary_category] += 1
        return counts
