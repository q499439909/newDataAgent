from .artifacts import (
    compile_pipeline_artifact_spec,
    validate_pipeline_artifact_spec,
)
from .inspection import query_control_facts_spec
from .executor import ControlToolExecutor
from .observations import ToolEvidence, ToolResult, ToolStatus
from .planning import retrieve_operators_spec
from .registry import FORBIDDEN_TOOL_NAMES, ToolRegistry
from .spec import (
    ToolConfirmation,
    ToolContext,
    ToolEffect,
    ToolExecutor,
    ToolSpec,
)


def build_p0_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        (
            retrieve_operators_spec(),
            compile_pipeline_artifact_spec(),
            validate_pipeline_artifact_spec(),
            query_control_facts_spec(),
        )
    )


__all__ = [
    "FORBIDDEN_TOOL_NAMES",
    "ControlToolExecutor",
    "ToolConfirmation",
    "ToolContext",
    "ToolEffect",
    "ToolEvidence",
    "ToolExecutor",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "ToolStatus",
    "build_p0_tool_registry",
]
