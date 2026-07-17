from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from .config import Settings
from .gateway import ModelGateway, ModelGatewayError, fallback_task_spec
from .imaging import analyze_image, hashes, representative_sample, scan_images, transform_image
from .models import DatasetManifest, ImageDecision, TaskSpec, TaskState, TrialBundle
from .pipelines import decide, default_candidates
from .store import ALLOWED_TRANSITIONS, Store


ProgressCallback = Callable[[int, int, str], None]


class DataAgentService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_directories()
        self.store = Store(settings.home / "dataagent.db")
        self.gateway = ModelGateway(settings)

    def create_task(
        self, requirement: str, source: Path, use_model: bool = True
    ) -> tuple[dict, bool, str | None]:
        source = source.expanduser().resolve()
        images = scan_images(source)
        if not images:
            raise ValueError(f"No supported images found under: {source}")
        task = self.store.create_task(self.settings.owner, requirement, str(source))
        self.store.transition(task["id"], self.settings.owner, TaskState.PLANNING)
        used_model = False
        warning = None
        if use_model and self.gateway.configured:
            try:
                spec, usage = self.gateway.plan_task(requirement, str(source))
                used_model = True
                self.store.audit(
                    self.settings.owner,
                    "model.plan",
                    "task",
                    task["id"],
                    {
                        "model": self.settings.planning_model,
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                    },
                )
            except (ModelGatewayError, ValueError) as exc:
                warning = str(exc)
                spec = fallback_task_spec(requirement, str(source))
        else:
            spec = fallback_task_spec(requirement, str(source))
            if use_model:
                warning = "Model API is not configured; used local conservative planning."
        self.store.save_spec(task["id"], self.settings.owner, spec.model_dump(mode="json"))
        self.store.transition(
            task["id"], self.settings.owner, TaskState.WAITING_SPEC_CONFIRMATION
        )
        return self.store.get_task(task["id"], self.settings.owner), used_model, warning

    def confirm_spec(self, task_id: str) -> dict:
        task = self.store.get_task(task_id, self.settings.owner)
        TaskSpec.model_validate(task["spec"])
        self.store.transition(task_id, self.settings.owner, TaskState.TRIAL_RUNNING)
        return self.store.get_task(task_id, self.settings.owner)

    def run_trial(
        self,
        task_id: str,
        sample_size: int = 100,
        vision_limit: int = 8,
        progress: ProgressCallback | None = None,
    ) -> TrialBundle:
        task = self.store.get_task(task_id, self.settings.owner)
        if task["state"] != TaskState.TRIAL_RUNNING:
            raise ValueError(
                f"Task must be in TRIAL_RUNNING state, current state is {task['state']}"
            )
        spec = TaskSpec.model_validate(task["spec"])
        paths = scan_images(Path(task["source_path"]))
        sample = representative_sample(paths, min(max(sample_size, 1), 300))
        metrics_list = []
        total = len(sample)
        for index, path in enumerate(sample, start=1):
            metrics_list.append(analyze_image(path))
            if progress:
                progress(index, total, "分析图片")

        input_tokens = 0
        output_tokens = 0
        needs_semantic = bool(spec.semantic_requirements or spec.exclusion_requirements)
        if needs_semantic and vision_limit > 0 and self.gateway.configured:
            # Spread calls across the sample so one filename cluster does not dominate.
            vision_paths = representative_sample(sample, min(vision_limit, len(sample)))
            by_path = {item.path: item for item in metrics_list}
            for index, path in enumerate(vision_paths, start=1):
                try:
                    result, usage = self.gateway.evaluate_image(path, spec)
                    item = by_path[str(path.resolve())]
                    item.semantic_score = result["confidence"]
                    item.semantic_pass = result["meets_requirement"]
                    item.semantic_reason = result["reason"]
                    item.semantic_tags = result["tags"]
                    input_tokens += usage.input_tokens
                    output_tokens += usage.output_tokens
                except ModelGatewayError as exc:
                    self.store.audit(
                        self.settings.owner,
                        "model.vision_failed",
                        "task",
                        task_id,
                        {"path": str(path), "error": str(exc)[:300]},
                    )
                if progress:
                    progress(index, len(vision_paths), "视觉评估")

        existing = self.store.pipelines_for_task(task_id)
        if existing:
            pipeline_rows = existing
            existing_strategies = {row["strategy"] for row in existing}
            for definition in default_candidates():
                if definition.strategy not in existing_strategies:
                    pipeline_id = self.store.add_pipeline(
                        task_id, definition.model_dump(mode="json")
                    )
                    pipeline_rows.append(self.store.get_pipeline(pipeline_id, task_id))
        else:
            pipeline_rows = []
            for definition in default_candidates():
                pipeline_id = self.store.add_pipeline(
                    task_id, definition.model_dump(mode="json")
                )
                pipeline_rows.append(self.store.get_pipeline(pipeline_id, task_id))

        from .pipelines import evaluate_candidate

        reports = []
        for row in pipeline_rows:
            definition = row["definition"]
            report = evaluate_candidate(
                row["id"],
                default_pipeline_from_dict(definition),
                spec,
                metrics_list,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            self.store.save_trial(row["id"], report.model_dump(mode="json"))
            reports.append(report)

        bundle = TrialBundle(
            task_id=task_id,
            sample_paths=[str(path) for path in sample],
            candidates=reports,
        )
        report_path = self.settings.home / "reports" / f"{task_id}-trial.json"
        report_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        self.store.audit(
            self.settings.owner,
            "trial.completed",
            "task",
            task_id,
            {"sample_size": len(sample), "report_path": str(report_path)},
        )
        self.store.transition(task_id, self.settings.owner, TaskState.WAITING_REVIEW)
        return bundle

    def get_trial(self, task_id: str) -> TrialBundle:
        path = self.settings.home / "reports" / f"{task_id}-trial.json"
        if not path.exists():
            raise KeyError(f"Trial report not found for task: {task_id}")
        return TrialBundle.model_validate_json(path.read_text(encoding="utf-8"))

    def review(self, task_id: str, pipeline_id: str, verdicts: dict[str, bool]) -> int:
        task = self.store.get_task(task_id, self.settings.owner)
        if task["state"] != TaskState.WAITING_REVIEW:
            raise ValueError("Task is not waiting for review")
        self.store.get_pipeline(pipeline_id, task_id)
        for path, accepted in verdicts.items():
            self.store.add_review(task_id, pipeline_id, path, "accepted" if accepted else "rejected")
        all_reviews = [
            item
            for item in self.store.reviews_for_task(task_id)
            if item["pipeline_id"] == pipeline_id
        ]
        bundle = self.get_trial(task_id)
        candidate = next(item for item in bundle.candidates if item.pipeline_id == pipeline_id)
        candidate.human_reviewed = len(all_reviews)
        candidate.human_acceptance_rate = (
            sum(item["verdict"] == "accepted" for item in all_reviews) / len(all_reviews)
            if all_reviews
            else None
        )
        candidate.proxy_only = not bool(all_reviews)
        report_path = self.settings.home / "reports" / f"{task_id}-trial.json"
        report_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        self.store.save_trial(pipeline_id, candidate.model_dump(mode="json"))
        self.store.audit(
            self.settings.owner,
            "review.submitted",
            "task",
            task_id,
            {"pipeline_id": pipeline_id, "count": len(verdicts)},
        )
        return len(verdicts)

    def execute(
        self,
        task_id: str,
        pipeline_id: str,
        progress: ProgressCallback | None = None,
        require_review: bool = True,
        evaluate_semantic: bool = True,
    ) -> tuple[str, str]:
        task = self.store.get_task(task_id, self.settings.owner)
        if task["state"] != TaskState.WAITING_REVIEW:
            raise ValueError(
                f"Task must be in WAITING_REVIEW state, current state is {task['state']}"
            )
        reviews = [r for r in self.store.reviews_for_task(task_id) if r["pipeline_id"] == pipeline_id]
        if require_review and not reviews:
            raise ValueError("At least one boundary sample review is required before full execution")

        pipeline_row = self.store.get_pipeline(pipeline_id, task_id)
        pipeline = default_pipeline_from_dict(pipeline_row["definition"])
        spec = TaskSpec.model_validate(task["spec"])
        semantic_required = bool(spec.semantic_requirements or spec.exclusion_requirements)
        if semantic_required and evaluate_semantic and not self.gateway.configured:
            raise ValueError(
                "Task contains semantic requirements but the vision model is not configured"
            )
        source_root = Path(task["source_path"]).resolve()
        paths = scan_images(source_root)
        before = hashes(paths)
        self.store.select_pipeline(task_id, self.settings.owner, pipeline_id)
        self.store.transition(task_id, self.settings.owner, TaskState.FULL_RUNNING)
        run_id = self.store.create_run(task_id, pipeline_id, len(paths))
        dataset_id = self.store.new_id("dataset")
        final_root = self.settings.home / "datasets" / dataset_id
        temp_root = self.settings.home / "datasets" / f".{dataset_id}.tmp"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True)
        output_root = temp_root / "files"
        transform_requested = bool(
            {"resize", "crop", "enhance", "convert", "repair"}
            & set(spec.output_actions)
        ) or any(
            [
                spec.transforms.resize_long_edge,
                spec.transforms.output_format,
                spec.transforms.autocontrast,
                spec.transforms.center_crop_ratio,
            ]
        )
        decisions: list[ImageDecision] = []
        seen: set[str] = set()
        try:
            for index, path in enumerate(paths, start=1):
                metrics = analyze_image(path)
                if semantic_required and evaluate_semantic and metrics.decode_ok:
                    result, usage = self.gateway.evaluate_image(path, spec)
                    metrics.semantic_score = result["confidence"]
                    metrics.semantic_pass = result["meets_requirement"]
                    metrics.semantic_reason = result["reason"]
                    metrics.semantic_tags = result["tags"]
                    self.store.audit(
                        self.settings.owner,
                        "model.vision",
                        "task",
                        task_id,
                        {
                            "model": self.settings.vision_model,
                            "path": str(path),
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                        },
                    )
                decision = decide(metrics, spec, pipeline, seen)
                if decision.keep and transform_requested:
                    relative = path.relative_to(source_root)
                    destination = transform_image(path, output_root / relative, spec.transforms)
                    decision.transformed_path = str(
                        (final_root / destination.relative_to(temp_root)).resolve()
                    )
                decisions.append(decision)
                if index % 100 == 0 or index == len(paths):
                    self.store.update_run(run_id, progress=index)
                if progress:
                    progress(index, len(paths), "全量执行")

            after = hashes(paths)
            unchanged = before == after
            if not unchanged:
                raise RuntimeError("Source image hash changed during execution; dataset was not published")
            kept = sum(item.keep for item in decisions)
            manifest = DatasetManifest(
                dataset_id=dataset_id,
                task_id=task_id,
                pipeline_id=pipeline_id,
                source_root=str(source_root),
                output_root=str((final_root / "files").resolve()),
                source_count=len(paths),
                kept_count=kept,
                rejected_count=len(paths) - kept,
                original_files_unchanged=unchanged,
                files=decisions,
                task_spec=spec,
                pipeline=pipeline,
            )
            manifest_path = temp_root / "manifest.json"
            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            temp_root.rename(final_root)
            final_manifest = final_root / "manifest.json"
            self.store.transition(task_id, self.settings.owner, TaskState.EVALUATING)
            self.store.add_dataset(
                dataset_id,
                task_id,
                pipeline_id,
                str(final_root),
                str(final_manifest),
                {
                    "source_count": len(paths),
                    "kept_count": kept,
                    "rejected_count": len(paths) - kept,
                    "original_files_unchanged": unchanged,
                },
            )
            self.store.mark_pipeline_reusable(pipeline_id)
            self.store.update_run(
                run_id, state="COMPLETED", progress=len(paths), dataset_id=dataset_id
            )
            self.store.transition(task_id, self.settings.owner, TaskState.COMPLETED)
            self.store.audit(
                self.settings.owner,
                "dataset.published",
                "dataset",
                dataset_id,
                {"manifest": str(final_manifest), "pipeline_id": pipeline_id},
            )
            return run_id, dataset_id
        except Exception as exc:
            if temp_root.exists():
                shutil.rmtree(temp_root, ignore_errors=True)
            self.store.update_run(run_id, state="FAILED", error=str(exc)[:1000])
            current = self.store.get_task(task_id, self.settings.owner)["state"]
            if TaskState.FAILED in ALLOWED_TRANSITIONS.get(current, set()):
                self.store.transition(task_id, self.settings.owner, TaskState.FAILED)
            raise

    def reuse_pipeline(
        self, pipeline_id: str, source: Path, requirement: str | None = None
    ) -> dict:
        original_pipeline = self.store.get_pipeline(pipeline_id)
        original_task = self.store.get_task(original_pipeline["task_id"], self.settings.owner)
        source = source.expanduser().resolve()
        if not scan_images(source):
            raise ValueError(f"No supported images found under: {source}")
        task = self.store.create_task(
            self.settings.owner,
            requirement or original_task["requirement"],
            str(source),
        )
        self.store.transition(task["id"], self.settings.owner, TaskState.PLANNING)
        spec = TaskSpec.model_validate(original_task["spec"]).model_copy(
            update={"source_path": str(source), "objective": requirement or original_task["requirement"]}
        )
        self.store.save_spec(task["id"], self.settings.owner, spec.model_dump(mode="json"))
        self.store.add_pipeline(task["id"], original_pipeline["definition"])
        self.store.transition(
            task["id"], self.settings.owner, TaskState.WAITING_SPEC_CONFIRMATION
        )
        self.store.audit(
            self.settings.owner,
            "pipeline.reused",
            "pipeline",
            pipeline_id,
            {"new_task_id": task["id"]},
        )
        return self.store.get_task(task["id"], self.settings.owner)


def default_pipeline_from_dict(data: dict):
    from .models import PipelineDefinition

    return PipelineDefinition.model_validate(data)
