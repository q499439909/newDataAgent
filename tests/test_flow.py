from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from dataagent.config import Settings
from dataagent.imaging import hashes
from dataagent.models import ModelUsage, TaskSpec, TaskState
from dataagent.service import DataAgentService


def make_images(root: Path) -> list[Path]:
    root.mkdir()
    paths = []
    for index, color in enumerate([(30, 30, 30), (120, 80, 50), (220, 220, 220), (80, 130, 180)]):
        path = root / f"image-{index}.png"
        image = Image.new("RGB", (640 + index * 10, 480 + index * 10), color)
        # Add edges so blur proxy scores are not all zero.
        for x in range(0, image.width, 20):
            for y in range(image.height):
                image.putpixel((x, y), (255 - color[0], 255 - color[1], 255 - color[2]))
        image.save(path)
        paths.append(path)
    duplicate = root / "duplicate.png"
    duplicate.write_bytes(paths[1].read_bytes())
    paths.append(duplicate)
    return paths


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        api_key=None,
        base_url="https://example.invalid/apps/anthropic",
        planning_model="planner",
        vision_model="vision",
        home=tmp_path / "home",
        owner="tester",
        env_path=None,
    )


def test_complete_local_flow_preserves_sources(tmp_path: Path) -> None:
    source = tmp_path / "source"
    paths = make_images(source)
    before = hashes(paths)
    svc = DataAgentService(settings_for(tmp_path))

    task, used_model, warning = svc.create_task(
        "筛选宽度至少600的图片并去重", source, use_model=False
    )
    assert not used_model
    assert warning is None
    assert task["state"] == TaskState.WAITING_SPEC_CONFIRMATION
    assert task["spec"]["hard_constraints"]["min_width"] == 600

    svc.confirm_spec(task["id"])
    trial = svc.run_trial(task["id"], sample_size=10, vision_limit=0)
    assert len(trial.candidates) == 3
    assert {item.strategy for item in trial.candidates} == {"retain", "balanced", "quality"}
    balanced = next(item for item in trial.candidates if item.strategy == "balanced")
    assert balanced.sample_size == 5

    path = balanced.boundary_paths[0]
    assert svc.review(task["id"], balanced.pipeline_id, {path: True}) == 1
    run_id, dataset_id = svc.execute(task["id"], balanced.pipeline_id)

    assert svc.store.get_run(run_id)["state"] == "COMPLETED"
    dataset = svc.store.get_dataset(dataset_id)
    manifest_path = Path(dataset["manifest_path"])
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["original_files_unchanged"] is True
    assert hashes(paths) == before
    assert svc.store.get_task(task["id"], "tester")["state"] == TaskState.COMPLETED
    assert svc.store.list_reusable_pipelines("tester")


def test_illegal_state_transition_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    make_images(source)
    svc = DataAgentService(settings_for(tmp_path))
    task = svc.store.create_task("tester", "需求", str(source))
    try:
        svc.store.transition(task["id"], "tester", TaskState.COMPLETED)
    except ValueError as exc:
        assert "Illegal task transition" in str(exc)
    else:
        raise AssertionError("illegal transition should fail")


def test_pipeline_reuse_creates_three_candidate_trial(tmp_path: Path) -> None:
    source = tmp_path / "source"
    make_images(source)
    svc = DataAgentService(settings_for(tmp_path))
    task, _, _ = svc.create_task("图片去重", source, use_model=False)
    svc.confirm_spec(task["id"])
    trial = svc.run_trial(task["id"], sample_size=5, vision_limit=0)
    candidate = next(item for item in trial.candidates if item.strategy == "balanced")
    svc.review(task["id"], candidate.pipeline_id, {candidate.boundary_paths[0]: True})
    svc.execute(task["id"], candidate.pipeline_id)

    reused = svc.reuse_pipeline(candidate.pipeline_id, source)
    svc.confirm_spec(reused["id"])
    reused_trial = svc.run_trial(reused["id"], sample_size=5, vision_limit=0)
    assert len(reused_trial.candidates) == 3


def test_transformed_outputs_point_to_published_version(tmp_path: Path) -> None:
    source = tmp_path / "source"
    paths = make_images(source)
    before = hashes(paths)
    svc = DataAgentService(settings_for(tmp_path))
    task, _, _ = svc.create_task("图片去重", source, use_model=False)
    spec = TaskSpec.model_validate(task["spec"])
    spec.output_actions = ["filter", "resize", "manifest"]
    spec.transforms.resize_long_edge = 320
    svc.store.save_spec(task["id"], "tester", spec.model_dump(mode="json"))
    svc.confirm_spec(task["id"])
    trial = svc.run_trial(task["id"], sample_size=5, vision_limit=0)
    candidate = next(item for item in trial.candidates if item.strategy == "retain")
    svc.review(task["id"], candidate.pipeline_id, {candidate.boundary_paths[0]: True})
    _, dataset_id = svc.execute(task["id"], candidate.pipeline_id)
    dataset = svc.store.get_dataset(dataset_id)
    manifest = json.loads(Path(dataset["manifest_path"]).read_text(encoding="utf-8"))
    outputs = [Path(item["transformed_path"]) for item in manifest["files"] if item["transformed_path"]]
    assert outputs
    assert all(path.exists() for path in outputs)
    assert all(".tmp" not in str(path) for path in outputs)
    assert hashes(paths) == before


def test_semantic_tasks_are_evaluated_during_full_run(tmp_path: Path) -> None:
    source = tmp_path / "source"
    paths = make_images(source)
    configured = settings_for(tmp_path)
    configured = Settings(
        api_key="fake",
        base_url=configured.base_url,
        planning_model=configured.planning_model,
        vision_model=configured.vision_model,
        home=configured.home,
        owner=configured.owner,
        env_path=None,
    )
    svc = DataAgentService(configured)

    class FakeGateway:
        configured = True

        def __init__(self):
            self.calls = 0

        def evaluate_image(self, path, spec):
            self.calls += 1
            return {
                "meets_requirement": True,
                "confidence": 0.99,
                "reason": "matches",
                "tags": ["test"],
            }, ModelUsage(input_tokens=1, output_tokens=1)

    fake = FakeGateway()
    svc.gateway = fake
    task, _, _ = svc.create_task("图片去重", source, use_model=False)
    spec = TaskSpec.model_validate(task["spec"])
    spec.semantic_requirements = ["contains the test subject"]
    svc.store.save_spec(task["id"], "tester", spec.model_dump(mode="json"))
    svc.confirm_spec(task["id"])
    trial = svc.run_trial(task["id"], sample_size=5, vision_limit=0)
    candidate = next(item for item in trial.candidates if item.strategy == "retain")
    svc.review(task["id"], candidate.pipeline_id, {candidate.boundary_paths[0]: True})
    svc.execute(task["id"], candidate.pipeline_id)
    assert fake.calls == len(paths)
