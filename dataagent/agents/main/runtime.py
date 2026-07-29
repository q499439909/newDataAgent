"""Deprecated compatibility exports for the promoted Requirement Agent.

The Main Agent no longer owns an LLM decision loop. New callers must import
from ``dataagent.agents.requirement``.
"""

from __future__ import annotations

from typing import Any
from warnings import warn

from ..requirement.runtime import (
    build_task_plan,
    decide_requirement_agent_turn,
)
from ..runtime import AgentPlanner
from ..shared import WorkOrderGraphState


def decide_main_agent_turn(
    state: WorkOrderGraphState,
    *,
    planner: AgentPlanner | None = None,
) -> dict[str, Any]:
    warn(
        "decide_main_agent_turn() is deprecated; use "
        "decide_requirement_agent_turn()",
        DeprecationWarning,
        stacklevel=2,
    )
    return decide_requirement_agent_turn(state, planner=planner)


__all__ = [
    "build_task_plan",
    "decide_main_agent_turn",
    "decide_requirement_agent_turn",
]
