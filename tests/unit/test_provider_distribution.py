from __future__ import annotations

import json
import subprocess
from pathlib import Path

from dataagent.config import Settings
from dataagent.distribution.datajuicer import (
    DATAJUICER_EXPECTED_CATALOG_COUNT,
    DataJuicerInstaller,
    ProviderInstallError,
    load_provider_registration,
)


class _FakeSearcher:
    process_bin: Path

    def __init__(self, python_executable: Path) -> None:
        self.python_executable = python_executable

    def health(self) -> dict:
        return {
            "ok": True,
            "provider_version": "1.5.3",
            "python": str(self.python_executable),
            "process_bin": str(self.process_bin),
            "package_identity": {
                "distribution": "py-data-juicer",
                "version": "1.5.3",
                "license": "Apache-2.0",
            },
        }

    def search(self) -> list[dict]:
        records = [
            _record("image_shape_filter", "filter", ["cpu", "image"]),
            _record("image_aspect_ratio_filter", "filter", ["cpu", "image"]),
            _record("image_deduplicator", "deduplicator", ["cpu", "image"]),
            _record(
                "image_tagging_vlm_mapper",
                "mapper",
                ["api", "gpu", "image", "multimodal", "vllm"],
            ),
        ]
        records.extend(
            _record(f"catalog_operator_{index:03d}", "filter", ["cpu", "text"])
            for index in range(DATAJUICER_EXPECTED_CATALOG_COUNT - len(records))
        )
        return records


def _record(name: str, operator_type: str, tags: list[str]) -> dict:
    return {
        "name": name,
        "desc": f"Descriptor for {name}",
        "type": operator_type,
        "tags": tags,
        "parameter_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }


def _freeze_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
    assert command[-3:] == ["pip", "freeze", "--all"]
    return subprocess.CompletedProcess(
        command,
        0,
        stdout="py-data-juicer==1.5.3\n",
        stderr="",
    )


def test_register_existing_provider_and_load_from_settings(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    python_executable = tmp_path / "provider" / "python.exe"
    process_bin = tmp_path / "provider" / "dj-process.exe"
    python_executable.parent.mkdir(parents=True)
    python_executable.touch()
    process_bin.touch()
    _FakeSearcher.process_bin = process_bin
    monkeypatch.setenv("BAILIAN_API_KEY", "configured-for-test")
    monkeypatch.setenv("DATAAGENT_HOME", str(home))
    monkeypatch.delenv("DATAAGENT_DATAJUICER_PYTHON", raising=False)
    monkeypatch.delenv("DATAAGENT_DATAJUICER_PROCESS_BIN", raising=False)

    result = DataJuicerInstaller(
        home,
        command_runner=_freeze_runner,
        searcher_factory=_FakeSearcher,
    ).install(existing_python=python_executable)

    registration = result["registration"]
    report = result["report"]
    assert registration["catalog_count"] == DATAJUICER_EXPECTED_CATALOG_COUNT
    assert report["counts"] == {
        "discovered": 217,
        "local_cpu_candidates": 216,
        "remote_api_candidates": 1,
        "linux_gpu_candidates": 1,
        "provider_callable_now": 217,
        "dataagent_verified": 3,
        "provider_available": 214,
    }
    assert load_provider_registration(home, "datajuicer") == registration
    assert Path(registration["catalog"]).is_file()
    assert Path(registration["capability_report"]).is_file()
    assert registration["runtime_root"]
    assert len(report["operators"]) == 217

    settings = Settings.load(tmp_path)
    assert settings.datajuicer_python == python_executable.resolve()
    assert settings.datajuicer_process_bin == process_bin.resolve()


def test_environment_paths_override_provider_registry(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    registry = home / "providers" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "providers": {
                    "datajuicer": {
                        "python": str(tmp_path / "registered-python"),
                        "process_bin": str(tmp_path / "registered-process"),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    override_python = tmp_path / "override-python"
    override_process = tmp_path / "override-process"
    monkeypatch.setenv("DATAAGENT_HOME", str(home))
    monkeypatch.setenv("DATAAGENT_DATAJUICER_PYTHON", str(override_python))
    monkeypatch.setenv("DATAAGENT_DATAJUICER_PROCESS_BIN", str(override_process))

    settings = Settings.load(tmp_path)

    assert settings.datajuicer_python == override_python.resolve()
    assert settings.datajuicer_process_bin == override_process.resolve()


def test_fresh_install_creates_isolated_runtime_with_frozen_packages(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[list[str]] = []
    home = tmp_path / "home"
    process_bin = tmp_path / "dj-process.exe"
    process_bin.touch()
    _FakeSearcher.process_bin = process_bin
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "venv" in command and "pip" not in command:
            runtime = Path(command[-1])
            python_executable = (
                runtime / "Scripts" / "python.exe"
                if __import__("os").name == "nt"
                else runtime / "bin" / "python"
            )
            python_executable.parent.mkdir(parents=True)
            python_executable.touch()
        if "--report" in command:
            report_path = Path(command[command.index("--report") + 1])
            report_path.write_text(
                json.dumps(
                    {
                        "install": [
                            {
                                "requested": True,
                                "metadata": {
                                    "name": "py-data-juicer",
                                    "version": "1.5.3",
                                },
                                "download_info": {
                                    "url": "https://example.invalid/datajuicer.whl",
                                    "archive_info": {
                                        "hashes": {"sha256": "a" * 64}
                                    },
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
        stdout = "py-data-juicer==1.5.3\n" if command[-3:] == ["pip", "freeze", "--all"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    result = DataJuicerInstaller(
        home,
        command_runner=runner,
        searcher_factory=_FakeSearcher,
    ).install(profile="auto")

    install_command = next(command for command in commands if "--report" in command)
    assert "py-data-juicer==1.5.3" in install_command
    assert "imagededup==0.3.3.post2" in install_command
    assert "openai>=1,<2" in install_command
    assert result["report"]["install_artifacts"][0]["sha256"] == "a" * 64
    assert result["report"]["counts"]["provider_callable_now"] == 216
    assert not (
        home / "providers" / "datajuicer" / "1.5.3" / ".installing"
    ).exists()


def test_catalog_profile_reports_discovery_without_execution(
    tmp_path: Path, monkeypatch
) -> None:
    python_executable = tmp_path / "provider" / "python.exe"
    process_bin = tmp_path / "provider" / "dj-process.exe"
    python_executable.parent.mkdir(parents=True)
    python_executable.touch()
    process_bin.touch()
    _FakeSearcher.process_bin = process_bin
    monkeypatch.setenv("BAILIAN_API_KEY", "configured-for-test")

    result = DataJuicerInstaller(
        tmp_path / "home",
        command_runner=_freeze_runner,
        searcher_factory=_FakeSearcher,
    ).install(profile="catalog", existing_python=python_executable)

    assert result["report"]["counts"]["provider_callable_now"] == 0
    by_ref = {
        item["operator_ref"]: item for item in result["report"]["operators"]
    }
    assert "CPU_PROVIDER_PACK_REQUIRED" in by_ref["image_shape_filter"][
        "blocked_reasons"
    ]
    assert "REMOTE_PROVIDER_PACK_REQUIRED" in by_ref[
        "image_tagging_vlm_mapper"
    ]["blocked_reasons"]


def test_unadmitted_operator_is_provider_available_when_runtime_is_ready(
    tmp_path: Path, monkeypatch
) -> None:
    python_executable = tmp_path / "provider" / "python.exe"
    process_bin = tmp_path / "provider" / "dj-process.exe"
    python_executable.parent.mkdir(parents=True)
    python_executable.touch()
    process_bin.touch()
    _FakeSearcher.process_bin = process_bin
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    result = DataJuicerInstaller(
        tmp_path / "home",
        command_runner=_freeze_runner,
        searcher_factory=_FakeSearcher,
    ).install(profile="cpu", existing_python=python_executable)

    by_ref = {
        item["operator_ref"]: item for item in result["report"]["operators"]
    }
    candidate = by_ref["catalog_operator_000"]
    assert candidate["governance_status"] == "PROVIDER_AVAILABLE"
    assert candidate["dataagent_verified"] is False
    assert candidate["callable_profiles"] == ["local_cpu"]
    assert "PRODUCTION_ADMISSION_REQUIRED" not in candidate["blocked_reasons"]


def test_catalog_count_drift_is_rejected(tmp_path: Path) -> None:
    python_executable = tmp_path / "python.exe"
    process_bin = tmp_path / "dj-process.exe"
    python_executable.touch()
    process_bin.touch()

    class ShortCatalogSearcher(_FakeSearcher):
        def search(self) -> list[dict]:
            return [_record("only_one", "filter", ["cpu"])]

    ShortCatalogSearcher.process_bin = process_bin
    installer = DataJuicerInstaller(
        tmp_path / "home",
        command_runner=_freeze_runner,
        searcher_factory=ShortCatalogSearcher,
    )

    try:
        installer.install(existing_python=python_executable)
    except ProviderInstallError as exc:
        assert "expected 217, got 1" in str(exc)
    else:
        raise AssertionError("catalog drift was not rejected")


def test_linux_gpu_profile_requires_linux_gpu_worker(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "dataagent.distribution.datajuicer.probe_machine",
        lambda: {
            "system": "windows",
            "release": "test",
            "architecture": "amd64",
            "python": "3.11",
            "nvidia_smi": {"available": False},
            "ffmpeg": {"available": False},
            "linux_gpu_worker_available": False,
            "remote_api_credentials_configured": False,
        },
    )

    try:
        DataJuicerInstaller(tmp_path / "home").install(profile="linux-gpu")
    except ProviderInstallError as exc:
        assert "requires Linux" in str(exc)
    else:
        raise AssertionError("linux-gpu profile was accepted without a GPU worker")
