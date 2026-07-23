from __future__ import annotations

from types import SimpleNamespace

from dataagent.application import run_worker


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
