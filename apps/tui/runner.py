from __future__ import annotations

import argparse

from .api_client import ControlPlaneClient
from .app import TuiApp
from .session import TuiSession


def main() -> None:
    parser = argparse.ArgumentParser(description="DataAgent terminal control surface")
    parser.add_argument("--owner", default="local-user")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--conversation", help="Resume an existing conversation id")
    args = parser.parse_args()
    client = ControlPlaneClient(base_url=args.api_url, owner_id=args.owner)
    try:
        TuiApp(TuiSession(client, conversation_id=args.conversation)).run()
    finally:
        client.close()


if __name__ == "__main__":
    main()
