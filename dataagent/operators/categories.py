from __future__ import annotations

from ..domain.operators import OperatorCategory


SECONDARY_CATEGORIES: dict[OperatorCategory, frozenset[str]] = {
    OperatorCategory.INGESTION: frozenset(
        {"file_discovery", "decoding", "metadata_extraction", "source_mapping"}
    ),
    OperatorCategory.FILTERING: frozenset(
        {
            "file_integrity",
            "resolution_and_format",
            "image_quality",
            "content_rule",
            "semantic_rule",
            "compliance_rule",
        }
    ),
    OperatorCategory.DEDUPLICATION: frozenset(
        {
            "exact_duplicate",
            "perceptual_duplicate",
            "semantic_duplicate",
            "burst_cluster",
            "cross_split_leakage",
        }
    ),
    OperatorCategory.UNDERSTANDING: frozenset(
        {
            "ocr",
            "classification",
            "object_detection",
            "segmentation",
            "face_and_person",
            "scene_understanding",
            "aesthetic_understanding",
            "vlm_judgement",
        }
    ),
    OperatorCategory.TRANSFORMATION: frozenset(
        {
            "crop",
            "resize",
            "rotate",
            "color_transform",
            "format_conversion",
            "annotation_conversion",
        }
    ),
    OperatorCategory.ENHANCEMENT: frozenset(
        {
            "denoise",
            "deblur",
            "super_resolution",
            "restoration",
            "augmentation",
            "generative_editing",
        }
    ),
    OperatorCategory.SAMPLING: frozenset(
        {
            "random_sampling",
            "stratified_sampling",
            "cluster_sampling",
            "quota_sampling",
            "diversity_sampling",
            "uncertainty_sampling",
            "hard_example_sampling",
        }
    ),
    OperatorCategory.EVALUATION: frozenset(
        {
            "hard_constraint_check",
            "semantic_quality",
            "distribution_profile",
            "diversity_metric",
            "slice_metric",
            "model_effect",
            "cost_and_latency",
        }
    ),
    OperatorCategory.OUTPUT: frozenset(
        {"manifest", "data_card", "dataset_freeze", "report_export", "lineage_export"}
    ),
}


def validate_secondary_category(category: OperatorCategory, secondary: str) -> None:
    if secondary not in SECONDARY_CATEGORIES[category]:
        allowed = ", ".join(sorted(SECONDARY_CATEGORIES[category]))
        raise ValueError(
            f"Unknown secondary category '{secondary}' for {category}; allowed: {allowed}"
        )
