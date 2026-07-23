from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import uuid
from enum import StrEnum
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from pydantic import Field, model_validator

from ..domain.common.models import DomainModel


REQUIRED_P0_TAGS = frozenset(
    {
        "cat",
        "dog",
        "mixed",
        "neither",
        "blur",
        "low_light",
        "low_resolution",
        "crop_abnormal",
        "duplicate",
        "near_duplicate",
        "synthetic",
        "screenshot",
        "illustration",
        "corrupt_file",
        "unreadable_path",
        "timeout_simulation",
        "multi_subject",
        "occlusion",
        "boundary_class",
        "repair_succeeds",
        "abandoned",
        "excluded",
    }
)


class AcceptanceFaultMode(StrEnum):
    NONE = "none"
    FAIL_ONCE = "fail_once"
    ALWAYS_FAIL = "always_fail"
    TIMEOUT = "timeout"
    UNREADABLE = "unreadable"


class AcceptanceCase(DomainModel):
    id: str
    relative_path: str
    sha256: str | None
    expected_class: str
    expected_outcome: str
    tags: tuple[str, ...] = Field(min_length=1)
    fault_mode: AcceptanceFaultMode = AcceptanceFaultMode.NONE
    materialized: bool = True

    @model_validator(mode="after")
    def validate_case(self) -> "AcceptanceCase":
        path = Path(self.relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Acceptance case path must stay inside the dataset root")
        if self.materialized and not self.sha256:
            raise ValueError("Materialized acceptance cases require SHA256")
        if not self.materialized and self.fault_mode != AcceptanceFaultMode.UNREADABLE:
            raise ValueError("Only unreadable fault cases may be non-materialized")
        return self


class AcceptanceDatasetManifest(DomainModel):
    id: str = "acceptance_dataset_p0_v1"
    version: int = 1
    builder_version: str = "p0-v1"
    source_digest: str
    cases: tuple[AcceptanceCase, ...] = Field(min_length=50, max_length=100)

    @model_validator(mode="after")
    def validate_coverage(self) -> "AcceptanceDatasetManifest":
        case_ids = [item.id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Acceptance case ids must be unique")
        coverage = {tag for item in self.cases for tag in item.tags}
        missing = REQUIRED_P0_TAGS - coverage
        if missing:
            raise ValueError(f"Acceptance Dataset is missing tags: {sorted(missing)}")
        return self


class AcceptanceValidationReport(DomainModel):
    ready: bool
    case_count: int
    covered_tags: tuple[str, ...]
    missing_tags: tuple[str, ...]
    missing_files: tuple[str, ...]
    hash_mismatches: tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_image(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="JPEG", quality=90)


def _load_labels(source_root: Path) -> dict[str, str]:
    labels_path = source_root / "labels.csv"
    image_root = source_root / "images"
    if not labels_path.is_file() or not image_root.is_dir():
        raise ValueError("Source must contain labels.csv and an images directory")
    with labels_path.open("r", encoding="utf-8-sig", newline="") as handle:
        labels = {
            row["filename"]: row["label"].strip().lower()
            for row in csv.DictReader(handle)
        }
    for required in ("cat", "dog"):
        if sum(label == required for label in labels.values()) < 20:
            raise ValueError(f"Source requires at least 20 {required} images")
    return labels


def build_p0_acceptance_dataset(
    *,
    source_root: Path,
    destination: Path,
) -> AcceptanceDatasetManifest:
    source_root = source_root.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Acceptance Dataset destination exists: {destination}")
    labels = _load_labels(source_root)
    image_root = source_root / "images"
    selected = {
        label: [
            image_root / name
            for name, value in sorted(labels.items())
            if value == label and (image_root / name).is_file()
        ][:20]
        for label in ("cat", "dog")
    }
    if any(len(paths) < 20 for paths in selected.values()):
        raise ValueError("Labeled source files are missing")
    source_digest = hashlib.sha256(
        "".join(
            f"{path.name}:{_sha256(path)}"
            for label in ("cat", "dog")
            for path in selected[label]
        ).encode()
    ).hexdigest()
    staging = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.tmp"
    )
    cases: list[AcceptanceCase] = []

    def add_case(
        case_id: str,
        relative_path: str,
        *,
        expected_class: str,
        expected_outcome: str,
        tags: tuple[str, ...],
        fault_mode: AcceptanceFaultMode = AcceptanceFaultMode.NONE,
        materialized: bool = True,
    ) -> None:
        path = staging / relative_path
        cases.append(
            AcceptanceCase(
                id=case_id,
                relative_path=relative_path,
                sha256=_sha256(path) if materialized else None,
                expected_class=expected_class,
                expected_outcome=expected_outcome,
                tags=tags,
                fault_mode=fault_mode,
                materialized=materialized,
            )
        )

    try:
        for label in ("cat", "dog"):
            for index, source in enumerate(selected[label], start=1):
                relative = f"assets/base/{label}_{index:02d}.jpg"
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                add_case(
                    f"base_{label}_{index:02d}",
                    relative,
                    expected_class=label,
                    expected_outcome="keep",
                    tags=(label,),
                )

        cat = Image.open(selected["cat"][0]).convert("RGB")
        dog = Image.open(selected["dog"][0]).convert("RGB")

        shutil.copy2(staging / "assets/base/cat_01.jpg", staging / "assets/duplicate.jpg")
        add_case(
            "duplicate",
            "assets/duplicate.jpg",
            expected_class="cat",
            expected_outcome="reject",
            tags=("cat", "duplicate"),
        )
        _write_image(
            staging / "assets/near_duplicate.jpg",
            ImageEnhance.Color(cat).enhance(0.92),
        )
        add_case(
            "near_duplicate",
            "assets/near_duplicate.jpg",
            expected_class="cat",
            expected_outcome="reject",
            tags=("cat", "near_duplicate"),
        )
        _write_image(staging / "assets/blur.jpg", cat.filter(ImageFilter.GaussianBlur(12)))
        add_case(
            "blur",
            "assets/blur.jpg",
            expected_class="cat",
            expected_outcome="reject",
            tags=("cat", "blur"),
        )
        _write_image(
            staging / "assets/low_light.jpg",
            ImageEnhance.Brightness(dog).enhance(0.12),
        )
        add_case(
            "low_light",
            "assets/low_light.jpg",
            expected_class="dog",
            expected_outcome="reject",
            tags=("dog", "low_light"),
        )
        _write_image(staging / "assets/low_resolution.jpg", cat.resize((32, 32)))
        add_case(
            "low_resolution",
            "assets/low_resolution.jpg",
            expected_class="cat",
            expected_outcome="reject",
            tags=("cat", "low_resolution"),
        )
        width, height = dog.size
        _write_image(
            staging / "assets/crop_abnormal.jpg",
            dog.crop((0, 0, max(1, width // 8), height)),
        )
        add_case(
            "crop_abnormal",
            "assets/crop_abnormal.jpg",
            expected_class="dog",
            expected_outcome="reject",
            tags=("dog", "crop_abnormal"),
        )

        mixed = Image.new("RGB", (512, 256), "white")
        mixed.paste(cat.resize((256, 256)), (0, 0))
        mixed.paste(dog.resize((256, 256)), (256, 0))
        _write_image(staging / "assets/mixed.jpg", mixed)
        add_case(
            "mixed",
            "assets/mixed.jpg",
            expected_class="mixed",
            expected_outcome="keep",
            tags=("mixed", "multi_subject"),
        )
        _write_image(staging / "assets/multi_subject.jpg", mixed.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
        add_case(
            "multi_subject",
            "assets/multi_subject.jpg",
            expected_class="mixed",
            expected_outcome="keep",
            tags=("mixed", "multi_subject"),
        )

        neither = Image.new("RGB", (320, 240), "white")
        draw = ImageDraw.Draw(neither)
        draw.rectangle((40, 40, 280, 200), fill="#4d8ac9")
        draw.ellipse((110, 70, 210, 170), fill="#f2cf4a")
        _write_image(staging / "assets/neither.jpg", neither)
        add_case(
            "neither",
            "assets/neither.jpg",
            expected_class="neither",
            expected_outcome="reject",
            tags=("neither",),
        )
        synthetic = neither.filter(ImageFilter.CONTOUR)
        _write_image(staging / "assets/synthetic.jpg", synthetic)
        add_case(
            "synthetic",
            "assets/synthetic.jpg",
            expected_class="neither",
            expected_outcome="reject",
            tags=("neither", "synthetic"),
        )

        screenshot = Image.new("RGB", (800, 600), "#d8dde3")
        screenshot.paste(dog.resize((480, 360)), (160, 140))
        ImageDraw.Draw(screenshot).rectangle((0, 0, 800, 60), fill="#39424e")
        _write_image(staging / "assets/screenshot.jpg", screenshot)
        add_case(
            "screenshot",
            "assets/screenshot.jpg",
            expected_class="dog",
            expected_outcome="reject",
            tags=("dog", "screenshot"),
        )
        illustration_source = image_root / "img_00201.jpg"
        illustration = (
            Image.open(illustration_source).convert("RGB")
            if illustration_source.is_file()
            else synthetic
        )
        _write_image(staging / "assets/illustration.jpg", illustration)
        add_case(
            "illustration",
            "assets/illustration.jpg",
            expected_class="neither",
            expected_outcome="reject",
            tags=("neither", "illustration"),
        )
        corrupt_path = staging / "assets/corrupt.jpg"
        corrupt_path.write_bytes(b"not-an-image")
        add_case(
            "corrupt",
            "assets/corrupt.jpg",
            expected_class="unknown",
            expected_outcome="reject",
            tags=("corrupt_file",),
        )

        occluded = cat.copy()
        ow, oh = occluded.size
        ImageDraw.Draw(occluded).rectangle(
            (ow // 4, oh // 4, 3 * ow // 4, 3 * oh // 4),
            fill="black",
        )
        _write_image(staging / "assets/occlusion.jpg", occluded)
        add_case(
            "occlusion",
            "assets/occlusion.jpg",
            expected_class="cat",
            expected_outcome="review",
            tags=("cat", "occlusion"),
        )
        boundary = Image.blend(
            cat.resize((256, 256)),
            dog.resize((256, 256)),
            0.5,
        )
        _write_image(staging / "assets/boundary.jpg", boundary)
        add_case(
            "boundary",
            "assets/boundary.jpg",
            expected_class="unknown",
            expected_outcome="review",
            tags=("boundary_class",),
        )

        fault_cases = (
            (
                "repair_succeeds",
                "assets/repair_succeeds.jpg",
                AcceptanceFaultMode.FAIL_ONCE,
                "keep",
                ("cat", "repair_succeeds"),
                cat,
            ),
            (
                "abandoned",
                "assets/abandoned.jpg",
                AcceptanceFaultMode.ALWAYS_FAIL,
                "abandoned",
                ("dog", "abandoned"),
                dog,
            ),
            (
                "excluded",
                "assets/excluded.jpg",
                AcceptanceFaultMode.ALWAYS_FAIL,
                "excluded",
                ("dog", "excluded"),
                dog,
            ),
            (
                "timeout",
                "assets/timeout.jpg",
                AcceptanceFaultMode.TIMEOUT,
                "failed",
                ("cat", "timeout_simulation"),
                cat,
            ),
        )
        for case_id, relative, fault, outcome, tags, image in fault_cases:
            _write_image(staging / relative, image)
            add_case(
                case_id,
                relative,
                expected_class=tags[0],
                expected_outcome=outcome,
                tags=tags,
                fault_mode=fault,
            )
        add_case(
            "unreadable",
            "assets/intentionally_missing.jpg",
            expected_class="unknown",
            expected_outcome="failed",
            tags=("unreadable_path",),
            fault_mode=AcceptanceFaultMode.UNREADABLE,
            materialized=False,
        )

        manifest = AcceptanceDatasetManifest(
            source_digest=source_digest,
            cases=tuple(cases),
        )
        (staging / "manifest.json").write_text(
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, destination)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_acceptance_manifest(path: Path) -> AcceptanceDatasetManifest:
    return AcceptanceDatasetManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def validate_acceptance_dataset(
    *,
    root: Path,
    manifest: AcceptanceDatasetManifest,
) -> AcceptanceValidationReport:
    root = root.expanduser().resolve()
    covered = {tag for item in manifest.cases for tag in item.tags}
    missing_files: list[str] = []
    mismatches: list[str] = []
    for case in manifest.cases:
        path = root / case.relative_path
        if not case.materialized:
            if path.exists():
                mismatches.append(case.relative_path)
            continue
        if not path.is_file():
            missing_files.append(case.relative_path)
        elif _sha256(path) != case.sha256:
            mismatches.append(case.relative_path)
    missing_tags = tuple(sorted(REQUIRED_P0_TAGS - covered))
    return AcceptanceValidationReport(
        ready=not missing_tags and not missing_files and not mismatches,
        case_count=len(manifest.cases),
        covered_tags=tuple(sorted(covered)),
        missing_tags=missing_tags,
        missing_files=tuple(missing_files),
        hash_mismatches=tuple(mismatches),
    )
