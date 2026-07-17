from __future__ import annotations

import time
from pathlib import Path

from .imaging import hard_constraint_failures
from .models import (
    CandidateReport,
    ImageDecision,
    ImageMetrics,
    PipelineDefinition,
    TaskSpec,
)


def default_candidates() -> list[PipelineDefinition]:
    common = ["decode_check", "hard_constraints", "exact_deduplicate", "manifest"]
    return [
        PipelineDefinition(
            strategy="retain",
            display_name="保留优先",
            description="只剔除明确不合格和重复图片，低置信度样本保留给人工判断。",
            blur_min=8.0,
            brightness_min=8.0,
            brightness_max=247.0,
            semantic_min=0.45,
            operators=common + ["soft_quality_filter", "semantic_judge_low_threshold"],
        ),
        PipelineDefinition(
            strategy="balanced",
            display_name="均衡方案",
            description="在质量、保留率和模型调用成本之间取平衡，作为默认推荐。",
            blur_min=14.0,
            brightness_min=16.0,
            brightness_max=239.0,
            semantic_min=0.65,
            operators=common + ["soft_quality_filter", "semantic_judge"],
        ),
        PipelineDefinition(
            strategy="quality",
            display_name="质量优先",
            description="采用严格质量阈值和高置信度语义判断，可能损失更多数据。",
            blur_min=22.0,
            brightness_min=24.0,
            brightness_max=231.0,
            semantic_min=0.82,
            operators=common + ["strict_quality_filter", "semantic_judge_high_threshold"],
        ),
    ]


def decide(
    metrics: ImageMetrics,
    spec: TaskSpec,
    pipeline: PipelineDefinition,
    seen_hashes: set[str],
) -> ImageDecision:
    reasons = hard_constraint_failures(metrics, spec.hard_constraints)
    if pipeline.deduplicate and metrics.sha256 in seen_hashes:
        reasons.append("精确重复")
    else:
        seen_hashes.add(metrics.sha256)
    if metrics.decode_ok:
        if metrics.blur_score < pipeline.blur_min:
            reasons.append(f"清晰度代理分 {metrics.blur_score:.1f} < {pipeline.blur_min:.1f}")
        if metrics.brightness < pipeline.brightness_min:
            reasons.append("图片过暗")
        if metrics.brightness > pipeline.brightness_max:
            reasons.append("图片过亮")
    if metrics.semantic_score is not None:
        if not metrics.semantic_pass or metrics.semantic_score < pipeline.semantic_min:
            reasons.append(
                f"语义评估未通过（置信度 {metrics.semantic_score:.2f}，门槛 {pipeline.semantic_min:.2f}）"
            )
    return ImageDecision(path=metrics.path, keep=not reasons, reasons=reasons, metrics=metrics)


def evaluate_candidate(
    pipeline_id: str,
    pipeline: PipelineDefinition,
    spec: TaskSpec,
    metrics_list: list[ImageMetrics],
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> CandidateReport:
    started = time.perf_counter()
    seen: set[str] = set()
    decisions = [decide(metrics, spec, pipeline, seen) for metrics in metrics_list]
    kept = sum(decision.keep for decision in decisions)
    hard_pass = sum(not hard_constraint_failures(item, spec.hard_constraints) for item in metrics_list)
    semantic = [item for item in metrics_list if item.semantic_score is not None]
    semantic_pass = (
        sum(bool(item.semantic_pass) for item in semantic) / len(semantic) if semantic else None
    )
    ranked = sorted(
        decisions,
        key=lambda item: min(
            abs(item.metrics.blur_score - pipeline.blur_min) / max(pipeline.blur_min, 1),
            abs(item.metrics.brightness - pipeline.brightness_min) / 255,
            abs(item.metrics.brightness - pipeline.brightness_max) / 255,
            abs((item.metrics.semantic_score or pipeline.semantic_min) - pipeline.semantic_min),
        ),
    )
    boundary_paths = [item.path for item in ranked[: min(50, len(ranked))]]
    total = len(decisions)
    return CandidateReport(
        pipeline_id=pipeline_id,
        strategy=pipeline.strategy,
        display_name=pipeline.display_name,
        sample_size=total,
        kept=kept,
        rejected=total - kept,
        retention_rate=kept / total if total else 0.0,
        hard_constraint_pass_rate=hard_pass / total if total else 0.0,
        semantic_evaluated=len(semantic),
        semantic_pass_rate=semantic_pass,
        api_input_tokens=input_tokens,
        api_output_tokens=output_tokens,
        elapsed_seconds=round(time.perf_counter() - started, 4),
        decisions=decisions,
        boundary_paths=boundary_paths,
        proxy_only=True,
    )


def strategy_score(report: CandidateReport) -> float:
    semantic = report.semantic_pass_rate if report.semantic_pass_rate is not None else 0.5
    return (
        report.hard_constraint_pass_rate * 0.4
        + semantic * 0.35
        + min(report.retention_rate, 0.9) / 0.9 * 0.2
        + (0.05 if report.strategy == "balanced" else 0.0)
    )
