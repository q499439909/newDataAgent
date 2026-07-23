from __future__ import annotations

import argparse
from pathlib import Path

from dataagent.acceptance import (
    build_p0_acceptance_dataset,
    validate_acceptance_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build DataAgent Acceptance Dataset v1")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_p0_acceptance_dataset(
        source_root=args.source,
        destination=args.destination,
    )
    report = validate_acceptance_dataset(
        root=args.destination,
        manifest=manifest,
    )
    print(f"dataset={manifest.id}")
    print(f"cases={report.case_count}")
    print(f"ready={str(report.ready).lower()}")
    print(f"manifest={(args.destination / 'manifest.json').resolve()}")
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
