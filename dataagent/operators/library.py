from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .builtin import (
    builtin_image_operators,
    builtin_model_operators,
    builtin_utility_operators,
)
from .models import MockModelBackend, ModelManager
from .providers import (
    DataJuicerOperatorProvider,
    DataJuicerProcessExecutor,
    DataJuicerSubprocessSearcher,
    NativeOperatorProvider,
    ProviderRegistry,
    build_datajuicer_proxy_operators,
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
    datajuicer_python: Path | None = None,
    datajuicer_process_bin: Path | None = None,
    datajuicer_runtime_root: Path | None = None,
    datajuicer_timeout_seconds: int = 300,
) -> OperatorLibrary:
    model_manager = ModelManager(
        (MockModelBackend(),), allow_download=allow_model_download
    )
    native_operators = (
        *builtin_image_operators(),
        *builtin_utility_operators(),
        *builtin_model_operators(model_manager),
    )
    operators = list(native_operators)
    providers = ProviderRegistry((NativeOperatorProvider(native_operators),))
    if include_datajuicer:
        searcher = None
        executor = None
        provider_version = None
        availability_error = None
        if datajuicer_python is not None:
            searcher = DataJuicerSubprocessSearcher(datajuicer_python)
            try:
                worker_health = searcher.health()
            except Exception as exc:
                availability_error = str(exc)
                worker_health = {}
            provider_version = str(worker_health.get("provider_version") or "external")
            if datajuicer_process_bin is None and worker_health.get("process_bin"):
                datajuicer_process_bin = Path(str(worker_health["process_bin"]))
        if datajuicer_process_bin is not None and datajuicer_runtime_root is not None:
            if datajuicer_process_bin.expanduser().is_file():
                executor = DataJuicerProcessExecutor(
                    (datajuicer_process_bin,),
                    runtime_root=datajuicer_runtime_root,
                    timeout_seconds=datajuicer_timeout_seconds,
                    allow_model_download=allow_model_download,
                )
            else:
                availability_error = (
                    f"Data-Juicer process executable not found: {datajuicer_process_bin}"
                )
        datajuicer_provider = DataJuicerOperatorProvider(
                searcher_factory=(lambda: searcher) if searcher is not None else None,
                executor=executor,
                provider_version=provider_version,
                allow_model_download=allow_model_download,
                availability_error=availability_error,
            )
        providers.register(datajuicer_provider)
        if executor is not None and not availability_error:
            operators.extend(build_datajuicer_proxy_operators(datajuicer_provider))
    operators_tuple = tuple(operators)
    registry = OperatorRegistry(operator.spec for operator in operators_tuple)
    runtime = OperatorRuntime(operators_tuple)
    return OperatorLibrary(
        operators=operators_tuple,
        registry=registry,
        runtime=runtime,
        providers=providers,
    )


__all__ = ["OperatorLibrary", "build_operator_library"]
