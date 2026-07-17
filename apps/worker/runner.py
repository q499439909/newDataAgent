from __future__ import annotations

import argparse

from dataagent.application.run_worker import LocalRunWorker
from dataagent.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="DataAgent local dataset worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args()
    worker = LocalRunWorker(Settings.load().home / "platform")
    if args.once:
        worker.process_next()
        return
    worker.run_forever(max(0.1, args.poll_interval))
