from __future__ import annotations

import re
from dataclasses import dataclass

from .models import (
    ClassificationLabelSpec,
    ClassificationSpec,
    ConstraintContract,
    TaskCapabilitySpec,
)


@dataclass(frozen=True)
class RequirementContract:
    constraints: tuple[ConstraintContract, ...]
    capability_requirements: tuple[TaskCapabilitySpec, ...]
    semantic_requirements: tuple[str, ...]
    classification: ClassificationSpec | None


def _constraint(
    constraint_id: str,
    *,
    source_text: str,
    scope: str,
    field: str,
    operator: str,
    value: str | bool | int | float,
    unit: str,
    evidence: str,
) -> ConstraintContract:
    return ConstraintContract(
        id=constraint_id,
        source_text=source_text,
        scope=scope,
        field=field,
        operator=operator,
        value=value,
        unit=unit,
        required_evidence_type=evidence,
    )


def _bytes(value: str, unit: str) -> int:
    multiplier = 1024 if unit.lower() == "kb" else 1024 * 1024
    return round(float(value) * multiplier)


def _capabilities(names: list[str]) -> tuple[TaskCapabilitySpec, ...]:
    descriptions = {
        "image_decode": "Decode images and collect immutable source metadata.",
        "image_shape": "Filter images by pixel width and height.",
        "aspect_ratio": "Filter images by width-to-height ratio.",
        "file_size": "Filter images by source file size in bytes.",
        "face_count": "Detect and filter images by the number of visible faces.",
        "perceptual_deduplication": "Remove exact and perceptual duplicates.",
        "visual_semantic_selection": (
            "Evaluate task-specific visual inclusion and exclusion criteria."
        ),
        "manifest": "Publish an immutable manifest with decisions and provenance.",
    }
    items: list[TaskCapabilitySpec] = []
    previous: str | None = None
    for name in names:
        items.append(
            TaskCapabilitySpec(
                id=name,
                capability=name,
                description=descriptions[name],
                depends_on=(previous,) if previous else (),
            )
        )
        previous = name
    return tuple(items)


def parse_requirement_contract(requirement: str) -> RequirementContract:
    """Normalize supported hard requirements without treating keywords as coverage."""

    constraints: list[ConstraintContract] = []
    capabilities = ["image_decode"]

    dimension = re.search(
        r"宽高[^；;。]*?(?:均|都)?[^；;。]*?(?:不少于|至少|>=)\s*(\d+)",
        requirement,
        flags=re.IGNORECASE,
    )
    if dimension:
        minimum = int(dimension.group(1))
        text = dimension.group(0)
        constraints.extend(
            (
                _constraint(
                    "C01",
                    source_text=text,
                    scope="asset",
                    field="width_px",
                    operator="gte",
                    value=minimum,
                    unit="px",
                    evidence="image_width_px",
                ),
                _constraint(
                    "C02",
                    source_text=text,
                    scope="asset",
                    field="height_px",
                    operator="gte",
                    value=minimum,
                    unit="px",
                    evidence="image_height_px",
                ),
            )
        )
        capabilities.append("image_shape")

    ratio = re.search(
        r"宽高比[^；;。]*?(\d+(?:\.\d+)?)\s*(?:到|至|[-~])\s*(\d+(?:\.\d+)?)",
        requirement,
        flags=re.IGNORECASE,
    )
    if ratio:
        text = ratio.group(0)
        constraints.extend(
            (
                _constraint(
                    "C03",
                    source_text=text,
                    scope="asset",
                    field="aspect_ratio",
                    operator="gte",
                    value=float(ratio.group(1)),
                    unit="ratio",
                    evidence="image_aspect_ratio",
                ),
                _constraint(
                    "C04",
                    source_text=text,
                    scope="asset",
                    field="aspect_ratio",
                    operator="lte",
                    value=float(ratio.group(2)),
                    unit="ratio",
                    evidence="image_aspect_ratio",
                ),
            )
        )
        capabilities.append("aspect_ratio")

    size = re.search(
        r"文件大小[^；;。]*?(\d+(?:\.\d+)?)\s*(KB|MB)"
        r"\s*(?:到|至|[-~])\s*(\d+(?:\.\d+)?)\s*(KB|MB)",
        requirement,
        flags=re.IGNORECASE,
    )
    if size:
        text = size.group(0)
        constraints.extend(
            (
                _constraint(
                    "C05",
                    source_text=text,
                    scope="asset",
                    field="file_size_bytes",
                    operator="gte",
                    value=_bytes(size.group(1), size.group(2)),
                    unit="bytes",
                    evidence="source_file_size_bytes",
                ),
                _constraint(
                    "C06",
                    source_text=text,
                    scope="asset",
                    field="file_size_bytes",
                    operator="lte",
                    value=_bytes(size.group(3), size.group(4)),
                    unit="bytes",
                    evidence="source_file_size_bytes",
                ),
            )
        )
        capabilities.append("file_size")

    face_inclusive = re.search(
        r"人脸[^；;。]*?(?:(\d+)\s*个?\s*(?:及以下|以内)"
        r"|(?:不多于|至多|小于等于|<=)\s*(\d+))",
        requirement,
        flags=re.IGNORECASE,
    )
    face = face_inclusive or re.search(
        r"人脸[^；;。]*?(?:少于|小于|<)\s*(\d+)",
        requirement,
        flags=re.IGNORECASE,
    )
    if face:
        if face_inclusive:
            value = int(face.group(1) or face.group(2))
            operator = "lte"
        else:
            value = int(face.group(1))
            operator = "lt"
        constraints.append(
            _constraint(
                "C07",
                source_text=face.group(0),
                scope="asset",
                field="face_count",
                operator=operator,
                value=value,
                unit="count",
                evidence="detected_face_count",
            )
        )
        capabilities.append("face_count")

    semantic_requirements: tuple[str, ...] = ()
    classification: ClassificationSpec | None = None
    black_clothing = re.search(
        r"(?:主体|人物|人)[^；;。]*?(?:穿|衣服|衣着)[^；;。]*?黑色"
        r"|黑色[^；;。]*?(?:衣服|衣着|上衣)",
        requirement,
        flags=re.IGNORECASE,
    )
    if black_clothing:
        constraints.append(
            _constraint(
                "C08",
                source_text=black_clothing.group(0),
                scope="asset",
                field="primary_subject_garment_color",
                operator="eq",
                value="black",
                unit="label",
                evidence="black_clothing_semantic_judgment",
            )
        )
        semantic_requirements = (
            "The primary visible subject's dominant visible garment is black.",
        )
        classification = ClassificationSpec(
            labels=(
                ClassificationLabelSpec(
                    id="black_clothing",
                    display_name="主体穿黑色衣服",
                    aliases=("黑色衣着", "黑色上衣"),
                ),
                ClassificationLabelSpec(
                    id="not_black_clothing",
                    display_name="主体未穿黑色衣服",
                    aliases=("非黑色衣着",),
                ),
            )
        )
        capabilities.append("visual_semantic_selection")

    if re.search(r"去重|重复图片|dedup", requirement, flags=re.IGNORECASE):
        constraints.extend(
            (
                _constraint(
                    "C09",
                    source_text="去除精确重复图片",
                    scope="dataset",
                    field="exact_duplicate_count",
                    operator="eq",
                    value=0,
                    unit="count",
                    evidence="exact_duplicate_group",
                ),
                _constraint(
                    "C10",
                    source_text="应用感知重复策略",
                    scope="dataset",
                    field="perceptual_duplicate_policy_applied",
                    operator="eq",
                    value=True,
                    unit="boolean",
                    evidence="perceptual_duplicate_group",
                ),
            )
        )
        capabilities.append("perceptual_deduplication")

    if constraints:
        constraints.append(
            _constraint(
                "C11",
                source_text="DataAgent source preservation invariant",
                scope="dataset",
                field="source_assets_immutable",
                operator="eq",
                value=True,
                unit="boolean",
                evidence="source_sha256_unchanged",
            )
        )

    capabilities.append("manifest")
    priority = {
        name: index
        for index, name in enumerate(
            (
                "image_decode",
                "image_shape",
                "aspect_ratio",
                "file_size",
                "face_count",
                "perceptual_deduplication",
                "visual_semantic_selection",
                "manifest",
            )
        )
    }
    ordered_names = sorted(
        dict.fromkeys(capabilities),
        key=lambda name: priority.get(name, len(priority)),
    )
    return RequirementContract(
        constraints=tuple(constraints),
        capability_requirements=_capabilities(ordered_names),
        semantic_requirements=semantic_requirements,
        classification=classification,
    )


__all__ = ["RequirementContract", "parse_requirement_contract"]
