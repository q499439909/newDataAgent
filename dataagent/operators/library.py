from __future__ import annotations

from dataclasses import dataclass

from .builtin import (
    builtin_image_operators,
    builtin_model_operators,
    builtin_utility_operators,
)
from .models import MockModelBackend, ModelManager
from .providers import (
    DataJuicerOperatorProvider,
    NativeOperatorProvider,
    ProviderRegistry,
)
from .registry import OperatorRegistry
from .runtime import OperatorRuntime


@dataclass(frozen=True)
class OperatorLibrary:
    operators: tuple
    registry: OperatorRegistry
    runtime: OperatorRuntime
    providers: ProviderRegistry


def build_operator_library(
    *,
    include_datajuicer: bool = True,
    allow_model_download: bool = False,
) -> OperatorLibrary:
    model_manager = ModelManager(
        (MockModelBackend(),), allow_download=allow_model_download
    )
    operators = (
        *builtin_image_operators(),
        *builtin_utility_operators(),
        *builtin_model_operators(model_manager),
    )
    registry = OperatorRegistry(operator.spec for operator in operators)
    runtime = OperatorRuntime(operators)
    providers = ProviderRegistry((NativeOperatorProvider(operators),))
    if include_datajuicer:
        providers.register(DataJuicerOperatorProvider())
    return OperatorLibrary(
        operators=operators,
        registry=registry,
        runtime=runtime,
        providers=providers,
    )


__all__ = ["OperatorLibrary", "build_operator_library"]
