from __future__ import annotations

import argparse

from dataagent.application.run_worker import LocalRunWorker
from dataagent.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="DataAgent local dataset worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args()
    settings = Settings.load()
    worker = LocalRunWorker(
        settings.home / "platform",
        include_datajuicer=settings.datajuicer_enabled,
        allow_model_download=settings.allow_model_download,
        datajuicer_python=settings.datajuicer_python,
        datajuicer_process_bin=settings.datajuicer_process_bin,
        datajuicer_timeout_seconds=settings.datajuicer_timeout_seconds,
        remote_asset_timeout_seconds=settings.remote_asset_timeout_seconds,
        vision_model=settings.vision_model,
        vision_api_base_url=settings.vision_api_base_url,
    )
    if args.once:
        worker.process_next()
        return
    worker.run_forever(max(0.1, args.poll_interval))


if __name__ == "__main__":
    main()
