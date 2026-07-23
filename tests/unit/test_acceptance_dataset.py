from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image

from dataagent.acceptance import (
    build_p0_acceptance_dataset,
    load_acceptance_manifest,
    validate_acceptance_dataset,
)


def _source_dataset(root: Path) -> Path:
    images = root / "images"
    images.mkdir(parents=True)
    rows = []
    for label, color in (("cat", "red"), ("dog", "blue")):
        for index in range(20):
            name = f"{label}_{index:02d}.jpg"
            Image.new("RGB", (128, 96), color).save(images / name)
            rows.append({"filename": name, "label": label})
    with (root / "labels.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("filename", "label"))
        writer.writeheader()
        writer.writerows(rows)
    return root


def test_p0_acceptance_dataset_is_reproducible_and_covers_required_matrix(
    tmp_path: Path,
) -> None:
    source = _source_dataset(tmp_path / "source")
    destination = tmp_path / "acceptance"

    manifest = build_p0_acceptance_dataset(
        source_root=source,
        destination=destination,
    )
    loaded = load_acceptance_manifest(destination / "manifest.json")
    report = validate_acceptance_dataset(root=destination, manifest=loaded)

    assert manifest == loaded
    assert len(manifest.cases) == 60
    assert report.ready is True
    assert report.case_count == 60
    assert report.missing_tags == ()
    assert {item.fault_mode for item in manifest.cases} >= {
        "fail_once",
        "always_fail",
        "timeout",
        "unreadable",
    }
    assert not (destination / "assets" / "intentionally_missing.jpg").exists()


def test_acceptance_validation_detects_asset_hash_changes(tmp_path: Path) -> None:
    source = _source_dataset(tmp_path / "source")
    destination = tmp_path / "acceptance"
    manifest = build_p0_acceptance_dataset(
        source_root=source,
        destination=destination,
    )
    changed = destination / manifest.cases[0].relative_path
    changed.write_bytes(b"changed")

    report = validate_acceptance_dataset(root=destination, manifest=manifest)

    assert report.ready is False
    assert report.hash_mismatches == (manifest.cases[0].relative_path,)
