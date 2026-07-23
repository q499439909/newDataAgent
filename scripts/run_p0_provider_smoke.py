from __future__ import annotations

import argparse
from pathlib import Path

from dataagent.acceptance import run_remote_vlm_smoke, write_provider_smoke_record
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.config import Settings
from dataagent.prompts import builtin_prompt_registry


REMOTE_VLM_OPERATOR = "datajuicer.image_tagging_vlm_mapper.remote_api:2"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the real P0 Remote VLM smoke")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    settings = Settings.load(cwd=args.repo)
    if not settings.api_key:
        raise SystemExit("BAILIAN_API_KEY or DASHSCOPE_API_KEY is required")
    runtime = AgentRuntime(
        settings.home / "acceptance-provider-smoke",
        include_datajuicer=settings.datajuicer_enabled,
        allow_model_download=False,
        datajuicer_python=settings.datajuicer_python,
        datajuicer_process_bin=settings.datajuicer_process_bin,
        datajuicer_timeout_seconds=settings.datajuicer_timeout_seconds,
        remote_asset_timeout_seconds=settings.remote_asset_timeout_seconds,
        remote_operator_available=True,
        vision_model=settings.vision_model,
        vision_api_base_url=settings.vision_api_base_url,
    )
    operator = runtime.operator_registry.get(REMOTE_VLM_OPERATOR)
    properties = operator.parameter_schema.get("properties", {})
    parameters = {
        name: schema["default"]
        for name, schema in properties.items()
        if "default" in schema
    }
    parameters["tag_field_name"] = "image_tags"
    parameters["system_prompt"] = builtin_prompt_registry().resolve(
        "closed-set-image-classification",
        1,
        variables={"allowed_labels": "cat, dog, mixed, unknown"},
    ).text
    provider = runtime.operator_library.providers.get("datajuicer")
    record = run_remote_vlm_smoke(
        provider=provider,
        operator=operator,
        image_path=args.image,
        parameters=parameters,
    )
    written = write_provider_smoke_record(record, args.output)
    print(f"status={record.status}")
    print(f"provider={record.provider_id}@{record.provider_version}")
    print(f"operator={record.operator_version_id}")
    print(f"model={record.model_version}")
    print(f"duration_seconds={record.duration_seconds:.3f}")
    print(f"reason_codes={','.join(record.failure_reason_codes) or '-'}")
    print(f"record={written}")
    return 0 if record.status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
