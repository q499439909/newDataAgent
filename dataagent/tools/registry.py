from __future__ import annotations

from pydantic import ValidationError

from .observations import ToolResult
from .spec import ToolConfirmation, ToolContext, ToolSpec


FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "delete_dataset",
        "execute_operator",
        "execute_provider_operator",
        "run_python",
        "run_shell",
        "write_arbitrary_yaml",
    }
)


class ToolRegistry:
    def __init__(self, tools: tuple[ToolSpec, ...] = ()) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        if tool.name in FORBIDDEN_TOOL_NAMES:
            raise ValueError(f"Tool is forbidden by policy: {tool.name}")
        if tool.name in self._tools:
            raise ValueError(f"Tool is already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Tool not found: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def execute(
        self,
        name: str,
        context: ToolContext,
        raw_input: dict,
    ) -> ToolResult:
        tool = self.get(name)
        if (
            tool.confirmation == ToolConfirmation.REQUIRED
            and not context.confirmed
        ):
            return ToolResult(
                ok=False,
                tool=name,
                status="confirmation_required",
                summary="Explicit user confirmation is required.",
                requires_confirmation=True,
                error_type="confirmation_required",
            )
        try:
            parsed = tool.input_model.model_validate(raw_input)
        except ValidationError:
            return ToolResult(
                ok=False,
                tool=name,
                status="failed",
                summary="Tool input validation failed.",
                error_type="input_validation_error",
            )
        try:
            result = tool.executor(context, parsed)
        except Exception as exc:
            return ToolResult(
                ok=False,
                tool=name,
                status="failed",
                summary=f"Tool execution failed: {exc}",
                error_type="tool_execution_error",
            )
        if result.tool != name:
            return result.model_copy(update={"tool": name})
        return result


__all__ = ["FORBIDDEN_TOOL_NAMES", "ToolRegistry"]
