from __future__ import annotations

from collections.abc import Iterable

from ...domain.operators import RuntimeBackend
from ..protocol import Operator
from ..runtime import OperatorRuntime
from ..validation import ParameterValidationError, validate_parameters
from .protocol import (
    ProviderExecuteRequest,
    ProviderExecuteResult,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderOperatorDescriptor,
    ProviderValidationResult,
)


class NativeOperatorProvider:
    provider_id = "native"
    provider_version = "0.1.0"

    def __init__(self, operators: Iterable[Operator] = ()) -> None:
        operators = tuple(operators)
        self._runtime = OperatorRuntime(operators)
        self._operators = {
            operator.spec.provider.provider_operator_ref or operator.spec.id: operator
            for operator in operators
        }

    def discover(self) -> list[ProviderOperatorDescriptor]:
        return [self._descriptor(operator) for operator in self._operators.values()]

    def describe(self, provider_operator_ref: str) -> ProviderOperatorDescriptor:
        try:
            return self._descriptor(self._operators[provider_operator_ref])
        except KeyError as exc:
            raise KeyError(f"Native operator not found: {provider_operator_ref}") from exc

    def validate(
        self,
        provider_operator_ref: str,
        parameters: dict,
        runtime_backend: RuntimeBackend,
    ) -> ProviderValidationResult:
        descriptor = self.describe(provider_operator_ref)
        spec = descriptor.operator_spec
        assert spec is not None
        supported = {profile.backend for profile in spec.supported_runtime_profiles}
        if runtime_backend not in supported:
            return ProviderValidationResult(
                ok=False,
                errors=(f"Unsupported runtime backend: {runtime_backend}",),
            )
        try:
            normalized = validate_parameters(spec.parameter_schema, parameters)
        except ParameterValidationError as exc:
            return ProviderValidationResult(ok=False, errors=(str(exc),))
        return ProviderValidationResult(ok=True, normalized_parameters=normalized)

    def execute(self, request: ProviderExecuteRequest) -> ProviderExecuteResult:
        descriptor = self.describe(request.provider_operator_ref)
        spec = descriptor.operator_spec
        assert spec is not None
        try:
            result = self._runtime.execute(
                operator_version_id=spec.id,
                context=request.context,
                input_data=request.input_data,
                parameters=request.parameters,
                runtime_backend=request.runtime_backend,
            )
        except Exception as exc:
            return ProviderExecuteResult(
                ok=False,
                error_type=type(exc).__name__,
                message=str(exc),
            )
        return ProviderExecuteResult(ok=True, result=result)

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            status=ProviderHealthStatus.AVAILABLE,
        )

    def _descriptor(self, operator: Operator) -> ProviderOperatorDescriptor:
        spec = operator.spec
        return ProviderOperatorDescriptor(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            provider_operator_ref=spec.provider.provider_operator_ref or spec.id,
            display_name=spec.display_name,
            description=spec.description,
            parameter_schema=spec.parameter_schema,
            tags=spec.capability_tags,
            source_digest=spec.provider.source_digest,
            suggested_category=spec.primary_category,
            suggested_secondary_category=spec.secondary_category,
            operator_spec=spec,
        )


__all__ = ["NativeOperatorProvider"]
