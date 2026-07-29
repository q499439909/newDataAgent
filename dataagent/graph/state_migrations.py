from __future__ import annotations

from typing import Any, Mapping

from ..agents.shared.state import CURRENT_AGENT_STATE_VERSION


def migrate_work_order_state(
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Convert supported checkpoint shapes to the root Requirement Agent schema."""

    migrated = dict(state)
    if "requirement_agent_action" not in migrated:
        legacy_action = migrated.get("main_agent_action")
        if legacy_action is not None:
            migrated["requirement_agent_action"] = legacy_action
    if "requirement_agent_decisions" not in migrated:
        legacy_decisions = migrated.get("main_agent_decisions")
        if legacy_decisions is not None:
            migrated["requirement_agent_decisions"] = legacy_decisions
    migrated.pop("main_agent_action", None)
    migrated.pop("main_agent_decisions", None)
    migrated["agent_state_version"] = CURRENT_AGENT_STATE_VERSION
    return migrated


__all__ = ["CURRENT_AGENT_STATE_VERSION", "migrate_work_order_state"]
