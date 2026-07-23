from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from . import __version__
from .config import Settings


class LocalStackError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalRuntimeIdentity:
    instance_id: str
    source_revision: str
    version: str

    @classmethod
    def create(cls, root: Path | None = None) -> "LocalRuntimeIdentity":
        return cls(
            instance_id=f"local_{uuid.uuid4().hex[:16]}",
            source_revision=_source_revision(root or Path.cwd()),
            version=__version__,
        )

    @classmethod
    def from_environment(cls) -> "LocalRuntimeIdentity":
        return cls(
            instance_id=os.getenv("DATAAGENT_INSTANCE_ID", "unmanaged"),
            source_revision=os.getenv("DATAAGENT_SOURCE_REVISION", "unknown"),
            version=__version__,
        )

    def environment(self) -> dict[str, str]:
        return {
            "DATAAGENT_INSTANCE_ID": self.instance_id,
            "DATAAGENT_SOURCE_REVISION": self.source_revision,
        }


def _source_revision(root: Path) -> str:
    configured = os.getenv("DATAAGENT_SOURCE_REVISION")
    if configured:
        return configured
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return f"package-{__version__}"
    return completed.stdout.strip() or f"package-{__version__}"


def health_payload(*, role: str) -> dict[str, str]:
    identity = LocalRuntimeIdentity.from_environment()
    return {
        "status": "ok",
        "instance_id": identity.instance_id,
        "source_revision": identity.source_revision,
        "version": identity.version,
        "role": role,
    }


def assert_matching_health(
    payload: dict[str, Any],
    expected: LocalRuntimeIdentity,
) -> None:
    if (
        payload.get("status") != "ok"
        or payload.get("instance_id") != expected.instance_id
        or payload.get("source_revision") != expected.source_revision
        or payload.get("version") != expected.version
    ):
        raise LocalStackError(
            "Port 8000 is already served by another DataAgent instance. "
            "Stop the old API and Worker before starting the managed local stack."
        )


def child_command(
    python_executable: Path,
    role: str,
    *,
    owner_id: str | None = None,
    api_url: str | None = None,
) -> list[str]:
    modules = {
        "api": "apps.api.runner",
        "worker": "apps.worker.runner",
        "tui": "apps.tui.runner",
    }
    try:
        module = modules[role]
    except KeyError as exc:
        raise ValueError(f"Unknown local stack role: {role}") from exc
    command = [str(python_executable), "-m", module]
    if role == "tui":
        if not owner_id or not api_url:
            raise ValueError("TUI child command requires owner_id and api_url")
        command.extend(["--owner", owner_id, "--api-url", api_url])
    return command


class LocalStackLauncher:
    def __init__(
        self,
        *,
        owner_id: str,
        api_url: str = "http://127.0.0.1:8000",
        startup_timeout_seconds: float = 20.0,
    ) -> None:
        self.owner_id = owner_id
        self.api_url = api_url.rstrip("/")
        self.startup_timeout_seconds = startup_timeout_seconds
        self.identity = LocalRuntimeIdentity.create()
        self.processes: list[subprocess.Popen] = []

    def run(self) -> int:
        settings = Settings.load()
        log_root = (
            settings.home / "launcher" / self.identity.instance_id
        ).resolve()
        log_root.mkdir(parents=True, exist_ok=False)
        environment = os.environ.copy()
        environment.update(self.identity.environment())
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        python_executable = Path(sys.executable).resolve()

        try:
            with ExitStack() as stack:
                api_log = stack.enter_context(
                    (log_root / "api.log").open("a", encoding="utf-8")
                )
                worker_log = stack.enter_context(
                    (log_root / "worker.log").open("a", encoding="utf-8")
                )
                api = self._spawn(
                    child_command(python_executable, "api"),
                    environment=environment,
                    output=api_log,
                )
                self._wait_for_api(api)
                worker = self._spawn(
                    child_command(python_executable, "worker"),
                    environment=environment,
                    output=worker_log,
                )
                time.sleep(0.5)
                if worker.poll() is not None:
                    raise LocalStackError(
                        f"Worker exited during startup. See {log_root / 'worker.log'}"
                    )
                tui = self._spawn(
                    child_command(
                        python_executable,
                        "tui",
                        owner_id=self.owner_id,
                        api_url=self.api_url,
                    ),
                    environment=environment,
                    output=None,
                )
                return int(tui.wait())
        except KeyboardInterrupt:
            return 130
        finally:
            self.stop()

    def _spawn(
        self,
        command: list[str],
        *,
        environment: dict[str, str],
        output,
    ) -> subprocess.Popen:
        process = subprocess.Popen(
            command,
            cwd=Path.cwd(),
            env=environment,
            stdin=None,
            stdout=output,
            stderr=subprocess.STDOUT if output is not None else None,
        )
        self.processes.append(process)
        return process

    def _wait_for_api(self, process: subprocess.Popen) -> None:
        deadline = time.monotonic() + self.startup_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LocalStackError(
                    "API exited during startup. Another process may already own port 8000."
                )
            try:
                response = httpx.get(f"{self.api_url}/health", timeout=1.0)
                response.raise_for_status()
                assert_matching_health(response.json(), self.identity)
                return
            except LocalStackError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                time.sleep(0.2)
        raise LocalStackError(
            f"API did not become healthy within {self.startup_timeout_seconds:g}s: "
            f"{last_error or 'no health response'}"
        )

    def stop(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(self.processes):
            if process.poll() is not None:
                continue
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def run_local_stack(
    *,
    owner_id: str,
    api_url: str = "http://127.0.0.1:8000",
    startup_timeout_seconds: float = 20.0,
) -> int:
    return LocalStackLauncher(
        owner_id=owner_id,
        api_url=api_url,
        startup_timeout_seconds=startup_timeout_seconds,
    ).run()


__all__ = [
    "LocalRuntimeIdentity",
    "LocalStackError",
    "LocalStackLauncher",
    "assert_matching_health",
    "child_command",
    "health_payload",
    "run_local_stack",
]
