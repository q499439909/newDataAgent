from __future__ import annotations

from collections.abc import Iterable

from ..domain.pipelines import PipelineVersion
from ..domain.operators import RuntimeBackend
from .protocol import Operator, OperatorContext, OperatorInput, OperatorResult
from .validation import validate_parameters


class OperatorRuntime:
    def __init__(self, operators: Iterable[Operator] = ()):
        self._operators: dict[str, Operator] = {}
        for operator in operators:
            self.register(operator)

    def register(self, operator: Operator) -> None:
        if operator.spec.id in self._operators:
            raise ValueError(f"Operator implementation already registered: {operator.spec.id}")
        self._operators[operator.spec.id] = operator

    def get(self, version_id: str) -> Operator:
        try:
            return self._operators[version_id]
        except KeyError as exc:
            raise KeyError(f"Operator implementation not found: {version_id}") from exc

    def validate_pipeline(self, pipeline: PipelineVersion) -> None:
        missing = [
            node.operator_version_id
            for node in pipeline.nodes
            if node.operator_version_id not in self._operators
        ]
        if missing:
            raise ValueError(f"Pipeline contains unavailable operators: {sorted(set(missing))}")
        for node in pipeline.nodes:
            operator = self.get(node.operator_version_id)
            supported = {profile.backend for profile in operator.spec.supported_runtime_profiles}
            if node.runtime_backend not in supported:
                raise ValueError(
                    f"Operator {node.operator_version_id} does not support "
                    f"runtime backend {node.runtime_backend}"
                )

    def execute(
        self,
        *,
        operator_version_id: str,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict,
        runtime_backend: RuntimeBackend = RuntimeBackend.CPU,
    ) -> OperatorResult:
        operator = self.get(operator_version_id)
        supported = {profile.backend for profile in operator.spec.supported_runtime_profiles}
        if runtime_backend not in supported:
            raise ValueError(
                f"Operator {operator_version_id} does not support runtime backend {runtime_backend}"
            )
        if runtime_backend == RuntimeBackend.MOCK and context.purpose == "production":
            raise PermissionError("Mock operators cannot execute in production runs")
        normalized = validate_parameters(operator.spec.parameter_schema, parameters)
        result = operator.execute(context, input_data, normalized)
        return OperatorResult.model_validate(result)
