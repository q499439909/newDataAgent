from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain.pipelines import PromptBinding


_MINIMUM_EXECUTABLE_PROMPT_VERSIONS = {
    "image-semantic-selection": 2,
    "image-task-visual-tagging": 2,
}


def prompt_execution_violation(binding: PromptBinding | None) -> str | None:
    if binding is None:
        return None
    minimum = _MINIMUM_EXECUTABLE_PROMPT_VERSIONS.get(binding.template_id, 1)
    if binding.template_version < minimum:
        return (
            f"Prompt {binding.template_id}:{binding.template_version} is retained "
            f"for audit only; production execution requires version {minimum} or newer"
        )
    return None


class PromptTemplateVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: int = Field(ge=1)
    purpose: str
    template: str = Field(min_length=1)
    required_variables: tuple[str, ...] = ()
    output_contract: dict[str, Any]

    @model_validator(mode="after")
    def validate_template_variables(self) -> "PromptTemplateVersion":
        referenced = set(Template(self.template).get_identifiers())
        required = set(self.required_variables)
        if referenced != required:
            raise ValueError(
                "Prompt template variables must exactly match required_variables"
            )
        return self

    @property
    def sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ResolvedPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    binding: PromptBinding
    output_contract: dict[str, Any]


class PromptRegistry:
    def __init__(self, templates: tuple[PromptTemplateVersion, ...]) -> None:
        self._templates = {(item.id, item.version): item for item in templates}
        if len(self._templates) != len(templates):
            raise ValueError("Prompt template ids and versions must be unique")

    @classmethod
    def from_directory(cls, directory: Path) -> "PromptRegistry":
        templates = tuple(
            PromptTemplateVersion.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8"))
            )
            for path in sorted(directory.glob("*.yaml"))
        )
        return cls(templates)

    def get(self, template_id: str, version: int) -> PromptTemplateVersion:
        try:
            return self._templates[(template_id, version)]
        except KeyError as exc:
            raise KeyError(f"Prompt template not found: {template_id}:{version}") from exc

    def resolve(
        self,
        template_id: str,
        version: int,
        *,
        variables: dict[str, Any],
    ) -> ResolvedPrompt:
        template = self.get(template_id, version)
        expected = set(template.required_variables)
        actual = set(variables)
        if actual != expected:
            raise ValueError(
                f"Prompt variables must be exactly {sorted(expected)}; got {sorted(actual)}"
            )
        serialized = {
            key: value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True)
            for key, value in variables.items()
        }
        text = Template(template.template).substitute(serialized).strip()
        resolved_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ResolvedPrompt(
            text=text,
            binding=PromptBinding(
                template_id=template.id,
                template_version=template.version,
                template_sha256=template.sha256,
                variables=variables,
                resolved_sha256=resolved_sha256,
            ),
            output_contract=template.output_contract,
        )


@lru_cache(maxsize=1)
def builtin_prompt_registry() -> PromptRegistry:
    return PromptRegistry.from_directory(Path(__file__).parent / "resources" / "prompts")


__all__ = [
    "PromptRegistry",
    "PromptTemplateVersion",
    "ResolvedPrompt",
    "builtin_prompt_registry",
    "prompt_execution_violation",
]
