from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw
from typer.testing import CliRunner

from dataagent.cli import app
from dataagent.config import Settings
from dataagent.store import Store


runner = CliRunner()


def test_cli_end_to_end(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "images"
    source.mkdir()
    for index in range(3):
        image = Image.new("RGB", (800, 600), (50 + index * 50, 90, 140))
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 100, 700, 500), outline="white", width=10)
        image.save(source / f"sample-{index}.jpg")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATAAGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DATAAGENT_OWNER", "cli-test")
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)

    created = runner.invoke(
        app,
        [
            "task",
            "create",
            "--source",
            str(source),
            "--requirement",
            "filter images and create manifest",
            "--no-model",
        ],
    )
    assert created.exit_code == 0, created.output
    task_id = re.search(r"task_[0-9a-f]{12}", created.output).group(0)
    assert runner.invoke(app, ["task", "confirm", task_id]).exit_code == 0
    trial = runner.invoke(
        app, ["trial", "run", task_id, "--sample-size", "3", "--vision-limit", "0"]
    )
    assert trial.exit_code == 0, trial.output

    settings = Settings.load(tmp_path)
    store = Store(settings.home / "dataagent.db")
    pipeline_id = next(
        item["id"] for item in store.pipelines_for_task(task_id) if item["strategy"] == "balanced"
    )
    reviewed = runner.invoke(
        app,
        [
            "review",
            "start",
            task_id,
            "--pipeline",
            pipeline_id,
            "--limit",
            "1",
            "--accept-all",
        ],
    )
    assert reviewed.exit_code == 0, reviewed.output
    executed = runner.invoke(
        app, ["run", "start", task_id, "--pipeline", pipeline_id]
    )
    assert executed.exit_code == 0, executed.output
    assert "数据版本已冻结" in executed.output
