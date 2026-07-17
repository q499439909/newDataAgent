from __future__ import annotations

from collections.abc import Iterable

from ..domain.pipelines import PipelineVersion
from .protocol import Operator, OperatorContext, OperatorInput, OperatorResult


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

    def execute(
        self,
        *,
        operator_version_id: str,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict,
    ) -> OperatorResult:
        return self.get(operator_version_id).execute(context, input_data, parameters)
