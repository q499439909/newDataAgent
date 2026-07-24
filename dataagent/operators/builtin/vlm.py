from __future__ import annotations

import hashlib
import json
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...domain.operators import (
    AssetRef,
    ImplementationSpec,
    ImplementationType,
    OperatorCategory,
    OperatorSpecVersion,
    OperatorStatus,
    ProviderRef,
    RuntimeBackend,
    RuntimeProfile,
)
from ..protocol import OperatorContext, OperatorInput, OperatorResult


VlmGateway = Callable[..., dict[str, Any]]


def _operator_spec() -> OperatorSpecVersion:
    entrypoint = "dataagent.operators.builtin.vlm:NativeRemoteVlmOperator"
    return OperatorSpecVersion(
        id="native.remote_vlm:1",
        family_id="native.remote_vlm",
        version=1,
        created_by="system",
        change_reason="governed native remote VLM operator",
        display_name="Native Remote VLM",
        summary=(
            "Collect task-specific visual evidence through the configured remote "
            "vision model without a provider subprocess."
        ),
        description=(
            "Reads one image, invokes the governed vision gateway with a versioned "
            "task prompt, validates the returned tag contract, and records a compact "
            "audit artifact for deterministic downstream policy operators."
        ),
        primary_category=OperatorCategory.UNDERSTANDING,
        secondary_category="vlm_judgement",
        capability_tags=frozenset(
            {
                "image",
                "remote",
                "commercial_model",
                "visual_understanding",
                "image_tagging",
                "classification",
                "vlm_judgement",
            }
        ),
        input_schema="ImageAssetRef",
        output_schema="ImageTagSet",
        parameter_schema={
            "type": "object",
            "properties": {
                "system_prompt": {"type": "string", "minLength": 1},
                "tag_field_name": {
                    "type": "string",
                    "minLength": 1,
                    "default": "visual_tags",
                },
                "allowed_tags": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "default": [],
                },
                "required_tag_groups": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "default": [],
                },
                "model": {"type": ["string", "null"], "default": None},
                "max_tokens": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 4096,
                    "default": 1024,
                },
            },
            "required": ["system_prompt"],
            "additionalProperties": False,
        },
        provider=ProviderRef(
            provider_id="native",
            provider_version="0.1.0",
            provider_operator_ref="remote_vlm",
        ),
        implementation=ImplementationSpec(
            implementation_type=ImplementationType.EXTERNAL_SERVICE,
            entrypoint=entrypoint,
        ),
        supported_runtime_profiles=(
            RuntimeProfile(
                backend=RuntimeBackend.REMOTE,
                concurrency=4,
                timeout_seconds=90,
            ),
        ),
        implementation_type=ImplementationType.EXTERNAL_SERVICE,
        implementation_ref=entrypoint,
        failure_policy="fail_asset",
        side_effects=(
            "Sends image bytes and the resolved task prompt to the configured model API.",
            "Writes a compact JSON evidence artifact under the run artifact root.",
        ),
        limitations=(
            "Requires a configured remote vision-model credential.",
            "Model judgments are probabilistic and must be resolved by downstream policy.",
        ),
        status=OperatorStatus.PERSONAL_RELEASE,
        owner_id="system",
        visibility="public",
    )


def _canonical_tag(value: Any) -> str:
    return "_".join(
        str(value).strip().lower().replace("-", " ").replace("_", " ").split()
    )


def _validated_tags(
    payload: dict[str, Any],
    *,
    allowed_tags: list[str],
    required_tag_groups: list[list[str]],
) -> list[str]:
    raw_tags = payload.get("tags")
    if not isinstance(raw_tags, list) or not raw_tags:
        raise RuntimeError("Native remote VLM returned no non-empty tags array")
    tags = list(
        dict.fromkeys(
            tag for tag in (_canonical_tag(item) for item in raw_tags) if tag
        )
    )
    if not tags:
        raise RuntimeError("Native remote VLM returned no non-empty tags array")

    allowed = {_canonical_tag(item) for item in allowed_tags}
    outside = sorted(set(tags).difference(allowed)) if allowed else []
    if outside:
        raise RuntimeError(
            "Native remote VLM returned tags outside the governed contract: "
            + ", ".join(outside)
        )

    for raw_group in required_tag_groups:
        group = {_canonical_tag(item) for item in raw_group}
        selected = sorted(group.intersection(tags))
        if len(selected) != 1:
            raise RuntimeError(
                "Native remote VLM must return exactly one tag from governed group "
                f"{sorted(group)}; received {selected}"
            )
    return tags


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Native remote VLM returned invalid confidence") from exc


def _emit(context: OperatorContext, event_type: str, **details: Any) -> None:
    event_sink = context.shared.get("event_sink")
    if callable(event_sink):
        event_sink(event_type, details)


def _write_evidence(
    context: OperatorContext,
    input_data: OperatorInput,
    *,
    prompt: str,
    tags: list[str],
    confidence: float | None,
    reason: str,
    gateway_metadata: dict[str, Any],
) -> AssetRef | None:
    artifact_root = context.shared.get("artifact_root")
    if not artifact_root:
        return None
    node_id = str(context.shared.get("active_node_id") or "native_remote_vlm")
    asset_identity = str(
        input_data.metrics.get("sha256")
        or hashlib.sha256(input_data.source_path.encode("utf-8")).hexdigest()
    )
    target = Path(artifact_root) / "native-vlm" / node_id / f"{asset_identity}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema": "dataagent.native-vlm-evidence:1",
        "run_id": context.run_id,
        "work_order_id": context.work_order_id,
        "operator_version_id": "native.remote_vlm:1",
        "source_path": input_data.source_path,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "response": {
            "tags": tags,
            "confidence": confidence,
            "reason": reason,
        },
        "gateway": {
            "model": gateway_metadata.get("model"),
            "request_id": gateway_metadata.get("request_id"),
            "usage": gateway_metadata.get("usage", {}),
        },
    }
    encoded = json.dumps(
        evidence,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    target.write_bytes(encoded)
    return AssetRef(
        uri=str(target),
        media_type="application/json",
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


class NativeRemoteVlmOperator:
    parallel_safe = True
    spec = _operator_spec()

    def __init__(self, vlm_gateway: VlmGateway | None = None) -> None:
        self._vlm_gateway = vlm_gateway

    def execute(
        self,
        context: OperatorContext,
        input_data: OperatorInput,
        parameters: dict[str, Any],
    ) -> OperatorResult:
        gateway = context.shared.get("vlm_call")
        if not callable(gateway):
            gateway = self._vlm_gateway
        if not callable(gateway):
            raise RuntimeError("Native remote VLM gateway is not configured")

        prompt = str(parameters["system_prompt"])
        tag_field_name = str(parameters.get("tag_field_name") or "visual_tags")
        allowed_tags = list(parameters.get("allowed_tags") or [])
        required_tag_groups = [
            list(group) for group in parameters.get("required_tag_groups") or []
        ]
        if not allowed_tags or not required_tag_groups:
            raise RuntimeError(
                "Native remote VLM requires a governed, non-empty tag contract"
            )
        path = Path(input_data.current_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        if mime_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            mime_type = "image/jpeg"
        model = parameters.get("model")
        _emit(
            context,
            "native_vlm_call_started",
            operator_version_id=self.spec.id,
            model=model,
            source_path=input_data.source_path,
        )
        try:
            payload = gateway(
                prompt=prompt,
                image_bytes=path.read_bytes(),
                mime_type=mime_type,
                model=model,
                max_tokens=int(parameters.get("max_tokens", 1024)),
            )
            if not isinstance(payload, dict):
                raise RuntimeError("Native remote VLM returned a non-object payload")
            tags = _validated_tags(
                payload,
                allowed_tags=allowed_tags,
                required_tag_groups=required_tag_groups,
            )
            confidence = _confidence(payload.get("confidence"))
            reason = str(payload.get("reason") or "")
            metadata = payload.get("_dataagent_gateway")
            metadata = metadata if isinstance(metadata, dict) else {}
            model_version_id = str(metadata.get("model") or model or "") or None
            evidence_ref = _write_evidence(
                context,
                input_data,
                prompt=prompt,
                tags=tags,
                confidence=confidence,
                reason=reason,
                gateway_metadata=metadata,
            )
        except Exception as exc:
            _emit(
                context,
                "native_vlm_call_failed",
                operator_version_id=self.spec.id,
                source_path=input_data.source_path,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

        _emit(
            context,
            "native_vlm_call_completed",
            operator_version_id=self.spec.id,
            model=model_version_id,
            request_id=metadata.get("request_id"),
            source_path=input_data.source_path,
            tag_count=len(tags),
        )
        existing_output = input_data.labels.get("datajuicer_output")
        existing_output = (
            dict(existing_output) if isinstance(existing_output, dict) else {}
        )
        provider_raw = {
            key: value
            for key, value in payload.items()
            if key != "_dataagent_gateway"
        }
        labels = {
            **input_data.labels,
            "datajuicer_output": {
                **existing_output,
                tag_field_name: tags,
                f"{tag_field_name}__provider_raw": provider_raw,
                "_dataagent_output_contract": "image_tag_set:1",
            },
            "native_vlm_evidence": {
                "operator_version_id": self.spec.id,
                "model": model_version_id,
                "request_id": metadata.get("request_id"),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "reason": reason,
            },
        }
        artifacts = [
            *input_data.artifacts,
            *([evidence_ref] if evidence_ref is not None else []),
        ]
        return OperatorResult(
            output_path=input_data.current_path,
            metrics={
                **input_data.metrics,
                "native_vlm_tag_count": len(tags),
                **(
                    {"native_vlm_confidence": confidence}
                    if confidence is not None
                    else {}
                ),
            },
            labels=labels,
            artifacts=artifacts,
            annotations=input_data.annotations,
            embeddings=input_data.embeddings,
            decision="continue",
            confidence=confidence,
            model_version_id=model_version_id,
        )


def builtin_vlm_operators(
    vlm_gateway: VlmGateway | None = None,
) -> tuple[NativeRemoteVlmOperator, ...]:
    return (NativeRemoteVlmOperator(vlm_gateway),)


__all__ = ["NativeRemoteVlmOperator", "builtin_vlm_operators"]
