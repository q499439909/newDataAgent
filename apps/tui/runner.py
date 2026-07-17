from __future__ import annotations

import argparse

from .api_client import ControlPlaneClient
from .app import TuiApp
from .session import TuiSession


def main() -> None:
    parser = argparse.ArgumentParser(description="DataAgent terminal control surface")
    parser.add_argument("--owner", default="local-user")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    client = ControlPlaneClient(base_url=args.api_url, owner_id=args.owner)
    try:
        TuiApp(TuiSession(client)).run()
    finally:
        client.close()
