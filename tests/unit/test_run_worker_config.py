from __future__ import annotations

from types import SimpleNamespace

from dataagent.application import run_worker
from dataagent.config import Settings
from dataagent.operators import build_operator_library


def test_local_worker_uses_the_same_vision_provider_configuration(
    tmp_path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_build_operator_library(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(runtime=object())

    monkeypatch.setattr(
        run_worker,
        "build_operator_library",
        fake_build_operator_library,
    )

    run_worker.LocalRunWorker(
        tmp_path,
        include_datajuicer=False,
        vision_model="vision-model-under-test",
        vision_api_base_url="https://vision.example.test/v1",
    )

    assert captured["vision_model"] == "vision-model-under-test"
    assert captured["vision_api_base_url"] == "https://vision.example.test/v1"


def test_local_worker_passes_bounded_asset_concurrency_to_executor(
    tmp_path,
    monkeypatch,
) -> None:
    executor_configuration = {}

    class FakeDatasetRunExecutor:
        def __init__(self, **kwargs):
            executor_configuration.update(kwargs)

    monkeypatch.setattr(
        run_worker,
        "DatasetRunExecutor",
        FakeDatasetRunExecutor,
    )
    monkeypatch.setattr(
        run_worker,
        "build_operator_library",
        lambda **_: SimpleNamespace(runtime=object()),
    )

    run_worker.LocalRunWorker(
        tmp_path,
        include_datajuicer=False,
        worker_concurrency=4,
    )

    assert executor_configuration["worker_concurrency"] == 4


def test_worker_concurrency_is_configurable_and_never_below_one(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATAAGENT_WORKER_CONCURRENCY", "0")

    settings = Settings.load(tmp_path)

    assert settings.worker_concurrency == 1


def test_builtin_operators_declare_parallel_safety() -> None:
    runtime = build_operator_library(include_datajuicer=False).runtime

    assert runtime.get("builtin.decode_check:1").parallel_safe is True
    assert runtime.get("builtin.quality_filter:1").parallel_safe is True
    assert runtime.get("native.remote_vlm:1").parallel_safe is True
    assert runtime.get("builtin.visual_semantic_selection:1").parallel_safe is True
    assert runtime.get("builtin.manifest:1").parallel_safe is True
    assert runtime.get("builtin.perceptual_dedup:1").parallel_safe is False
