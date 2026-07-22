from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AdaptedProviderOutput:
    fields: dict[str, Any]
    contract_id: str | None = None
    errors: tuple[str, ...] = ()


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        for item in value.split(","):
            if item.strip():
                yield item.strip()
    elif isinstance(value, dict):
        candidate = value.get("tags", value.get("tag"))
        if candidate is not None:
            yield from _strings(candidate)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)


def _adapt_image_tagging(
    parameters: dict[str, Any], fields: dict[str, Any]
) -> AdaptedProviderOutput:
    tag_field = str(parameters.get("tag_field_name") or "image_tags")
    raw = fields.get(tag_field)
    tags = list(
        dict.fromkeys(
            item.lower().replace(" ", "-")[:30]
            for item in _strings(raw)
            if item.strip()
        )
    )[:10]
    adapted = dict(fields)
    adapted[f"{tag_field}__provider_raw"] = raw
    adapted[tag_field] = tags
    adapted["_dataagent_output_contract"] = "image_tag_set:1"
    return AdaptedProviderOutput(
        fields=adapted,
        contract_id="image_tag_set:1",
        errors=() if tags else (f"{tag_field} is empty or malformed",),
    )


_ADAPTERS: dict[
    str, Callable[[dict[str, Any], dict[str, Any]], AdaptedProviderOutput]
] = {
    "image_tagging_vlm_mapper": _adapt_image_tagging,
}


def adapt_provider_output(
    provider_operator_ref: str,
    parameters: dict[str, Any],
    fields: dict[str, Any],
) -> AdaptedProviderOutput:
    adapter = _ADAPTERS.get(provider_operator_ref)
    if adapter is None:
        return AdaptedProviderOutput(fields=dict(fields))
    return adapter(parameters, fields)


__all__ = ["AdaptedProviderOutput", "adapt_provider_output"]
