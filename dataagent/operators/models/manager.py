from __future__ import annotations

from typing import Any, Protocol

from ...domain.operators import ModelRequirement, RuntimeBackend
from ..protocol import OperatorInput


class ModelBackend(Protocol):
    backend: RuntimeBackend
    requires_download: bool

    def infer(
        self,
        *,
        requirement: ModelRequirement,
        capability: str,
        input_data: OperatorInput,
        parameters: dict[str, Any],
    ) -> dict[str, Any]: ...


class ModelManager:
    """Backend router. Downloading backends are added only in provider workers."""

    def __init__(
        self,
        backends: tuple[ModelBackend, ...] = (),
        *,
        allow_download: bool = False,
    ) -> None:
        self.allow_download = allow_download
        self._backends = {backend.backend: backend for backend in backends}

    def register(self, backend: ModelBackend) -> None:
        if backend.backend in self._backends:
            raise ValueError(f"Model backend already registered: {backend.backend}")
        self._backends[backend.backend] = backend

    def infer(
        self,
        *,
        backend: RuntimeBackend,
        requirement: ModelRequirement,
        capability: str,
        input_data: OperatorInput,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            implementation = self._backends[backend]
        except KeyError as exc:
            raise RuntimeError(f"Model backend is not configured: {backend}") from exc
        if getattr(implementation, "requires_download", False) and not self.allow_download:
            raise PermissionError(
                "Model download is disabled; pre-pull the model or enable downloads explicitly"
            )
        return implementation.infer(
            requirement=requirement,
            capability=capability,
            input_data=input_data,
            parameters=parameters,
        )


__all__ = ["ModelBackend", "ModelManager"]
