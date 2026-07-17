from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ...domain.operators import AssetRef, RuntimeBackend
from ..protocol import OperatorResult
from .protocol import ProviderExecuteRequest, ProviderExecuteResult


_SAFE_OPERATOR_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _tail(value: str, limit: int = 4000) -> str:
    return value[-limit:].strip()


class DataJuicerSubprocessSearcher:
    def __init__(
        self,
        python_executable: Path,
        *,
        worker_script: Path | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.python_executable = python_executable.expanduser().resolve()
        self.worker_script = (
            worker_script or Path(__file__).with_name("datajuicer_worker.py")
        ).resolve()
        self.timeout_seconds = timeout_seconds
        self._health: dict[str, Any] | None = None

    def health(self) -> dict[str, Any]:
        if self._health is None:
            self._health = self._invoke("health")
        return dict(self._health)

    def search(self) -> list[dict[str, Any]]:
        payload = self._invoke("discover")
        self._health = {
            key: payload.get(key)
            for key in ("ok", "provider_version", "python", "process_bin")
        }
        operators = payload.get("operators", [])
        if not isinstance(operators, list):
            raise RuntimeError("Data-Juicer worker returned an invalid operator catalog")
        return [item for item in operators if isinstance(item, dict)]

    def _invoke(self, action: str) -> dict[str, Any]:
        if not self.python_executable.is_file():
            raise FileNotFoundError(
                f"Data-Juicer Python executable not found: {self.python_executable}"
            )
        environment = os.environ.copy()
        environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        completed = subprocess.run(
            [str(self.python_executable), str(self.worker_script), action],
            shell=False,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,
        )
        stdout = completed.stdout.strip()
        if not stdout:
            raise RuntimeError(
                "Data-Juicer worker returned no JSON output: " + _tail(completed.stderr)
            )
        try:
            payload = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("Data-Juicer worker returned invalid JSON") from exc
        if completed.returncode != 0 or not payload.get("ok", False):
            message = payload.get("message") or _tail(completed.stderr)
            raise RuntimeError(f"Data-Juicer worker failed: {message}")
        return payload


class DataJuicerProcessExecutor:
    def __init__(
        self,
        command_prefix: Sequence[str | Path],
        *,
        runtime_root: Path,
        timeout_seconds: int = 300,
        allow_model_download: bool = False,
    ) -> None:
        if not command_prefix:
            raise ValueError("Data-Juicer command prefix must not be empty")
        self.command_prefix = tuple(str(item) for item in command_prefix)
        self.runtime_root = runtime_root.expanduser().resolve()
        self.timeout_seconds = timeout_seconds
        self.allow_model_download = allow_model_download

    def __call__(self, request: ProviderExecuteRequest) -> ProviderExecuteResult:
        if request.runtime_backend != RuntimeBackend.CPU:
            return ProviderExecuteResult(
                ok=False,
                error_type="unsupported_runtime_backend",
                message="The local Data-Juicer executor currently supports CPU only",
            )
        if not _SAFE_OPERATOR_NAME.fullmatch(request.provider_operator_ref):
            return ProviderExecuteResult(
                ok=False,
                error_type="invalid_operator_ref",
                message="Data-Juicer operator name contains unsupported characters",
            )
        source = Path(request.input_data.current_path).expanduser().resolve()
        if not source.is_file():
            return ProviderExecuteResult(
                ok=False,
                error_type="input_not_found",
                message=f"Input asset not found: {source}",
            )

        identity = json.dumps(
            {
                "run_id": request.context.run_id,
                "operator": request.provider_operator_ref,
                "source": str(source),
                "parameters": request.parameters,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        execution_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        execution_root = self.runtime_root / request.context.run_id / execution_id
        execution_root.mkdir(parents=True, exist_ok=True)
        input_path = execution_root / "input.jsonl"
        export_path = execution_root / "output.jsonl"
        recipe_path = execution_root / "recipe.yaml"
        record = {
            "_dataagent_asset_id": execution_id,
            "images": [str(source)],
            "text": "<__dj__image>",
        }
        input_path.write_text(
            json.dumps(record, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        recipe = {
            "project_name": f"dataagent_{execution_id}",
            "dataset": {
                "configs": [
                    {
                        "type": "local",
                        "path": str(input_path),
                        "weight": 1.0,
                    }
                ]
            },
            "export_path": str(export_path),
            "np": 1,
            "executor_type": "default",
            "process": [
                {request.provider_operator_ref: dict(request.parameters)}
            ],
        }
        recipe_path.write_text(
            json.dumps(recipe, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

        command = [*self.command_prefix, "--config", str(recipe_path)]
        environment = os.environ.copy()
        environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        if not self.allow_model_download:
            policy_root = execution_root / "offline-policy"
            policy_root.mkdir(exist_ok=True)
            (policy_root / "sitecustomize.py").write_text(
                """
try:
    from data_juicer.utils.lazy_loader import LazyLoader

    def _blocked_install(cls, package_spec, pip_args=None):
        raise RuntimeError(
            "DataAgent offline policy blocked Data-Juicer dependency installation: "
            + str(package_spec)
        )

    LazyLoader._install_package = classmethod(_blocked_install)
except ImportError:
    pass
""".strip()
                + "\n",
                encoding="utf-8",
            )
            existing_pythonpath = environment.get("PYTHONPATH", "")
            environment["PYTHONPATH"] = str(policy_root) + (
                os.pathsep + existing_pythonpath if existing_pythonpath else ""
            )
            environment.update(
                {
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "DATASETS_OFFLINE": "1",
                    "UV_OFFLINE": "1",
                    "PIP_NO_INDEX": "1",
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                }
            )
        returncode, stdout, stderr, error_type = self._run(
            command,
            execution_root,
            environment,
            request.context.shared.get("cancel_check"),
        )
        if returncode != 0:
            return ProviderExecuteResult(
                ok=False,
                error_type=error_type or "command_failed",
                message=_tail(stderr or stdout) or "Data-Juicer execution failed",
            )
        try:
            rows = self._read_jsonl(export_path)
        except Exception as exc:
            return ProviderExecuteResult(
                ok=False,
                error_type="invalid_provider_output",
                message=str(exc),
            )

        kept = next(
            (
                row
                for row in rows
                if row.get("_dataagent_asset_id") == execution_id
            ),
            rows[0] if rows else None,
        )
        decision = "continue" if kept is not None else "reject"
        output_fields = {
            key: value
            for key, value in (kept or {}).items()
            if key not in {"_dataagent_asset_id", "images", "text"}
        }
        output_path = str(source)
        images = (kept or {}).get("images", [])
        if isinstance(images, list) and images:
            candidate = Path(str(images[0])).expanduser()
            if not candidate.is_absolute():
                candidate = (execution_root / candidate).resolve()
            if candidate.is_file():
                output_path = str(candidate)
        artifact = AssetRef(
            uri=str(export_path),
            media_type="application/x-ndjson",
            sha256=hashlib.sha256(export_path.read_bytes()).hexdigest(),
        )
        return ProviderExecuteResult(
            ok=True,
            result=OperatorResult(
                output_path=output_path,
                metrics=request.input_data.metrics,
                labels={
                    **request.input_data.labels,
                    "datajuicer_operator": request.provider_operator_ref,
                    "datajuicer_output": output_fields,
                },
                artifacts=[*request.input_data.artifacts, artifact],
                annotations=request.input_data.annotations,
                embeddings=request.input_data.embeddings,
                decision=decision,
                reason_codes=[] if kept is not None else ["DATAJUICER_FILTERED_OUT"],
            ),
        )

    def _run(
        self,
        command: list[str],
        cwd: Path,
        environment: dict[str, str],
        cancel_check: Any,
    ) -> tuple[int, str, str, str | None]:
        stdout_handle = tempfile.TemporaryFile(
            mode="w+", encoding="utf-8", errors="replace"
        )
        stderr_handle = tempfile.TemporaryFile(
            mode="w+", encoding="utf-8", errors="replace"
        )
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                command,
                shell=False,
                cwd=cwd,
                env=environment,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                start_new_session=True,
            )
            deadline = time.monotonic() + self.timeout_seconds
            error_type: str | None = None
            while process.poll() is None:
                if isinstance(cancel_check, Callable) and cancel_check():
                    error_type = "cancelled"
                    self._terminate(process)
                    break
                if time.monotonic() >= deadline:
                    error_type = "timeout"
                    self._terminate(process)
                    break
                time.sleep(0.1)
            returncode = process.wait(timeout=5)
            if error_type == "cancelled":
                returncode = 130
            elif error_type == "timeout":
                returncode = 124
            stdout_handle.seek(0)
            stderr_handle.seek(0)
            return returncode, stdout_handle.read(), stderr_handle.read(), error_type
        except FileNotFoundError as exc:
            return 127, "", str(exc), "missing_command"
        except Exception as exc:
            if process is not None and process.poll() is None:
                self._terminate(process)
            return 1, "", str(exc), type(exc).__name__
        finally:
            stdout_handle.close()
            stderr_handle.close()

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                shell=False,
                capture_output=True,
                check=False,
            )
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            raise FileNotFoundError(f"Data-Juicer export was not created: {path}")
        rows = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), start=1
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"Data-Juicer output line {line_number} is not an object"
                )
            rows.append(value)
        return rows


__all__ = ["DataJuicerProcessExecutor", "DataJuicerSubprocessSearcher"]
