from __future__ import annotations

import argparse
from pathlib import Path

from dataagent.acceptance import (
    collect_acceptance_run_record,
    write_provider_smoke_record,
)
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a real P0 acceptance Run")
    parser.add_argument("--runtime-home", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    settings = Settings.load(cwd=args.repo)
    runtime = AgentRuntime(
        args.runtime_home,
        include_datajuicer=settings.datajuicer_enabled,
        allow_model_download=False,
        datajuicer_python=settings.datajuicer_python,
        datajuicer_process_bin=settings.datajuicer_process_bin,
        datajuicer_timeout_seconds=settings.datajuicer_timeout_seconds,
        remote_asset_timeout_seconds=settings.remote_asset_timeout_seconds,
        allow_datajuicer_candidate_execution=True,
        remote_operator_available=bool(settings.api_key),
        vision_model=settings.vision_model,
        vision_api_base_url=settings.vision_api_base_url,
    )
    run = runtime.get_run(run_id=args.run_id, owner_id=args.owner)
    if not run.get("dataset_version_id") or not run.get("qc_report_id"):
        raise SystemExit("Run has no DatasetVersion or QCReport")
    dataset = runtime.get_dataset(
        dataset_version_id=run["dataset_version_id"],
        owner_id=args.owner,
    )
    qc_report = runtime.get_qc_report(
        qc_report_id=run["qc_report_id"],
        owner_id=args.owner,
    )
    exported = runtime.export_deliverable_dataset(
        dataset_version_id=dataset["id"],
        owner_id=args.owner,
        destination=args.export,
    )
    provider = runtime.operator_library.providers.get("datajuicer")
    record = collect_acceptance_run_record(
        run=run,
        dataset=dataset,
        qc_report=qc_report,
        node_results=runtime.get_run_node_results(
            run_id=run["id"],
            owner_id=args.owner,
        ),
        events=runtime.get_run_events(run_id=run["id"], owner_id=args.owner),
        provider_id=provider.provider_id,
        provider_version=provider.provider_version,
        model_version=settings.vision_model,
        export_path=exported["root_uri"],
    )
    written = write_provider_smoke_record(record, args.output)
    print(f"status={record.status}")
    print(f"run_id={record.run_id}")
    print(f"dataset_version_id={record.dataset_version_id}")
    print(f"qc_report_id={record.qc_report_id}")
    print(f"remote_call_count={record.remote_call_count}")
    print(f"export_path={record.export_path}")
    print(f"record={written}")
    return 0 if record.status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
