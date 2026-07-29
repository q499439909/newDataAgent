from __future__ import annotations

import argparse

from dataagent.agents.runtime import GatewayAgentPlanner
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.application.run_worker import LocalRunWorker
from dataagent.config import Settings
from dataagent.gateway import ModelGateway
from dataagent.worker_lease import WorkerProcessLease


def main() -> None:
    parser = argparse.ArgumentParser(description="DataAgent local dataset worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args()
    settings = Settings.load()
    gateway = ModelGateway(settings)
    vlm_gateway = gateway.call_vision_model_json if settings.api_key else None
    platform_home = settings.home / "platform"
    outcome_runtime = AgentRuntime(
        platform_home,
        include_datajuicer=settings.datajuicer_enabled,
        allow_model_download=settings.allow_model_download,
        datajuicer_python=settings.datajuicer_python,
        datajuicer_process_bin=settings.datajuicer_process_bin,
        datajuicer_timeout_seconds=settings.datajuicer_timeout_seconds,
        remote_asset_timeout_seconds=settings.remote_asset_timeout_seconds,
        allow_datajuicer_candidate_execution=(
            settings.allow_datajuicer_candidate_execution
        ),
        remote_operator_available=bool(settings.api_key),
        vision_model=settings.vision_model,
        vision_api_base_url=settings.vision_api_base_url,
        vlm_gateway=vlm_gateway,
        agent_planner=(
            GatewayAgentPlanner(gateway) if gateway.configured else None
        ),
        enable_pipeline_trials=gateway.configured,
    )
    with WorkerProcessLease(platform_home / "worker.lock.json"):
        worker = LocalRunWorker(
            platform_home,
            include_datajuicer=settings.datajuicer_enabled,
            allow_model_download=settings.allow_model_download,
            datajuicer_python=settings.datajuicer_python,
            datajuicer_process_bin=settings.datajuicer_process_bin,
            datajuicer_timeout_seconds=settings.datajuicer_timeout_seconds,
            remote_asset_timeout_seconds=settings.remote_asset_timeout_seconds,
            vision_model=settings.vision_model,
            vision_api_base_url=settings.vision_api_base_url,
            vlm_gateway=vlm_gateway,
            worker_concurrency=settings.worker_concurrency,
            run_outcome_handler=lambda run: (
                outcome_runtime.observe_run_outcome(
                    work_order_id=run["work_order_id"],
                    run_id=run["id"],
                    owner_id=run["owner_id"],
                )
            ),
        )
        if args.once:
            worker.process_next()
            return
        worker.run_forever(max(0.1, args.poll_interval))


if __name__ == "__main__":
    main()
