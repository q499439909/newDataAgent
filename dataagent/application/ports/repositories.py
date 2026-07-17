from __future__ import annotations

from typing import Protocol

from ...domain.operators import OperatorSpecVersion
from ...domain.pipelines import PipelineVersion
from ...domain.specs import TaskSpecVersion


class TaskSpecRepository(Protocol):
    def get_version(self, version_id: str) -> TaskSpecVersion: ...

    def save_version(self, spec: TaskSpecVersion) -> None: ...


class OperatorRepository(Protocol):
    def get_version(self, version_id: str) -> OperatorSpecVersion: ...

    def save_version(self, operator: OperatorSpecVersion) -> None: ...

    def list_versions(self) -> list[OperatorSpecVersion]: ...


class PipelineRepository(Protocol):
    def get_version(self, version_id: str) -> PipelineVersion: ...

    def save_version(self, pipeline: PipelineVersion) -> None: ...

    def list_versions(self, family_id: str) -> list[PipelineVersion]: ...
