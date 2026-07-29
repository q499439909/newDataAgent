"""Deprecated compatibility exports for ControlToolExecutor."""

from .executor import ControlToolExecutor

GovernedToolLoop = ControlToolExecutor

__all__ = ["ControlToolExecutor", "GovernedToolLoop"]
