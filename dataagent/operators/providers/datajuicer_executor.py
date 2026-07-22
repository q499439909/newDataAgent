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
from .protocol import (
    ProviderDatasetExecuteRequest,
    ProviderDatasetExecuteResult,
    ProviderDatasetItem,
    ProviderDatasetItemResult,
    ProviderExecuteRequest,
    ProviderExecuteResult,
)


_SAFE_OPERATOR_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _tail(value: str, limit: int = 4000) -> str:
    return value[-limit:].strip()


def _has_semantic_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_semantic_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_semantic_value(item) for item in value)
    return False


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
        remote_asset_timeout_seconds: int = 90,
        allow_model_download: bool = False,
    ) -> None:
        if not command_prefix:
            raise ValueError("Data-Juicer command prefix must not be empty")
        self.command_prefix = tuple(str(item) for item in command_prefix)
        self.runtime_root = runtime_root.expanduser().resolve()
        self.timeout_seconds = timeout_seconds
        self.remote_asset_timeout_seconds = remote_asset_timeout_seconds
        self.allow_model_download = allow_model_download

    def __call__(self, request: ProviderExecuteRequest) -> ProviderExecuteResult:
        batch = self.execute_dataset(
            ProviderDatasetExecuteRequest(
                provider_operator_ref=request.provider_operator_ref,
                runtime_backend=request.runtime_backend,
                context=request.context,
                items=(ProviderDatasetItem(asset_id="asset_0", input_data=request.input_data),),
                parameters=request.parameters,
            )
        )
        return ProviderExecuteResult(
            ok=batch.ok,
            result=batch.items[0].result if batch.ok and batch.items else None,
            error_type=batch.error_type,
            message=batch.message,
            duration_seconds=batch.duration_seconds,
            stdout_tail=batch.stdout_tail,
            stderr_tail=batch.stderr_tail,
        )

    def execute_dataset(
        self, request: ProviderDatasetExecuteRequest
    ) -> ProviderDatasetExecuteResult:
        started = time.monotonic()
        if request.runtime_backend not in {RuntimeBackend.CPU, RuntimeBackend.REMOTE}:
            return ProviderDatasetExecuteResult(
                ok=False,
                error_type="unsupported_runtime_backend",
                message="The Data-Juicer executor supports CPU and remote API profiles",
            )
        if not _SAFE_OPERATOR_NAME.fullmatch(request.provider_operator_ref):
            return ProviderDatasetExecuteResult(
                ok=False,
                error_type="invalid_operator_ref",
                message="Data-Juicer operator name contains unsupported characters",
            )
        sources: list[Path] = []
        for item in request.items:
            source = Path(item.input_data.current_path).expanduser().resolve()
            if not source.is_file():
                return ProviderDatasetExecuteResult(
                    ok=False,
                    error_type="input_not_found",
                    message=f"Input asset not found: {source}",
                )
            sources.append(source)
        if not request.items:
            return ProviderDatasetExecuteResult(ok=True)

        identity = json.dumps(
            {
                "run_id": request.context.run_id,
                "operator": request.provider_operator_ref,
                "assets": [item.asset_id for item in request.items],
                "sources": [str(item) for item in sources],
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
        internal_ids = {
            f"asset_{index:08d}": (item, source)
            for index, (item, source) in enumerate(zip(request.items, sources, strict=True))
        }
        records = [
            {
                "_dataagent_asset_id": internal_id,
                "images": [str(source)],
                "text": "<__dj__image>",
            }
            for internal_id, (_, source) in internal_ids.items()
        ]
        input_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
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
        if request.runtime_backend == RuntimeBackend.REMOTE:
            api_key = environment.get("OPENAI_API_KEY") or environment.get(
                "BAILIAN_API_KEY"
            ) or environment.get("DASHSCOPE_API_KEY")
            if not api_key:
                return ProviderDatasetExecuteResult(
                    ok=False,
                    error_type="missing_remote_api_key",
                    message="Remote Data-Juicer execution requires a model API key",
                )
            environment["OPENAI_API_KEY"] = api_key
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
        event_sink = request.context.shared.get("event_sink")
        self._emit(
            event_sink,
            "provider_process_started",
            provider_id="datajuicer",
            operator_ref=request.provider_operator_ref,
            item_count=len(request.items),
        )
        returncode, stdout, stderr, error_type = self._run(
            command,
            execution_root,
            environment,
            request.context.shared.get("cancel_check"),
            timeout_seconds=(
                self.remote_asset_timeout_seconds
                if request.runtime_backend == RuntimeBackend.REMOTE
                and len(request.items) == 1
                else self.timeout_seconds
            ),
        )
        duration = time.monotonic() - started
        stdout_tail = _tail(stdout)
        stderr_tail = _tail(stderr)
        self._emit(
            event_sink,
            "provider_process_completed",
            provider_id="datajuicer",
            operator_ref=request.provider_operator_ref,
            item_count=len(request.items),
            returncode=returncode,
            duration_seconds=round(duration, 6),
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
        )
        if returncode != 0:
            return ProviderDatasetExecuteResult(
                ok=False,
                error_type=error_type or "command_failed",
                message=stderr_tail or stdout_tail or "Data-Juicer execution failed",
                duration_seconds=duration,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
        try:
            rows = self._read_jsonl(export_path)
        except Exception as exc:
            return ProviderDatasetExecuteResult(
                ok=False,
                error_type="invalid_provider_output",
                message=str(exc),
                duration_seconds=duration,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
        stats_path = export_path.with_name("output_stats.jsonl")
        try:
            stats_rows = self._read_jsonl(stats_path) if stats_path.is_file() else []
        except Exception as exc:
            return ProviderDatasetExecuteResult(
                ok=False,
                error_type="invalid_provider_stats",
                message=str(exc),
                duration_seconds=duration,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
        rows_by_id = {
            str(row.get("_dataagent_asset_id")): row
            for row in rows
            if row.get("_dataagent_asset_id") is not None
        }
        if rows and not rows_by_id:
            return ProviderDatasetExecuteResult(
                ok=False,
                error_type="provider_identity_lost",
                message="Data-Juicer output did not preserve DataAgent asset ids",
                duration_seconds=duration,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
        stats_by_id: dict[str, dict[str, Any]] = {}
        if stats_rows:
            if len(stats_rows) != len(rows):
                return ProviderDatasetExecuteResult(
                    ok=False,
                    error_type="provider_stats_alignment_error",
                    message=(
                        "Data-Juicer output rows and stats rows have different lengths: "
                        f"{len(rows)} != {len(stats_rows)}"
                    ),
                    duration_seconds=duration,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                )
            stats_by_id = {
                str(row.get("_dataagent_asset_id")): stats
                for row, stats in zip(rows, stats_rows, strict=True)
            }

        output_fields_by_id: dict[str, dict[str, Any]] = {}
        for internal_id, row in rows_by_id.items():
            output_fields = {
                key: value
                for key, value in row.items()
                if key not in {"_dataagent_asset_id", "images", "text"}
            }
            stats_meta = stats_by_id.get(internal_id, {}).get("__dj__meta__", {})
            if isinstance(stats_meta, dict):
                output_fields.update(stats_meta)
            output_fields_by_id[internal_id] = output_fields

        if request.provider_operator_ref == "image_tagging_vlm_mapper":
            tag_field = str(request.parameters.get("tag_field_name") or "image_tags")
            missing_tags = [
                internal_id
                for internal_id in internal_ids
                if not _has_semantic_value(
                    output_fields_by_id.get(internal_id, {}).get(tag_field)
                )
            ]
            if missing_tags:
                return ProviderDatasetExecuteResult(
                    ok=False,
                    error_type="empty_provider_semantic_output",
                    message=(
                        "Data-Juicer image_tagging_vlm_mapper produced no non-empty "
                        f"{tag_field} for {len(missing_tags)}/{len(internal_ids)} assets"
                    ),
                    duration_seconds=duration,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                )

        artifacts = [
            AssetRef(
                uri=str(export_path),
                media_type="application/x-ndjson",
                sha256=hashlib.sha256(export_path.read_bytes()).hexdigest(),
            )
        ]
        if stats_path.is_file():
            artifacts.append(
                AssetRef(
                    uri=str(stats_path),
                    media_type="application/x-ndjson",
                    sha256=hashlib.sha256(stats_path.read_bytes()).hexdigest(),
                )
            )
        results: list[ProviderDatasetItemResult] = []
        for internal_id, (item, source) in internal_ids.items():
            kept = rows_by_id.get(internal_id)
            output_fields = output_fields_by_id.get(internal_id, {})
            output_path = str(source)
            images = (kept or {}).get("images", [])
            if isinstance(images, list) and images:
                candidate = Path(str(images[0])).expanduser()
                if not candidate.is_absolute():
                    candidate = (execution_root / candidate).resolve()
                if candidate.is_file():
                    output_path = str(candidate)
            results.append(
                ProviderDatasetItemResult(
                    asset_id=item.asset_id,
                    result=OperatorResult(
                        output_path=output_path,
                        metrics=item.input_data.metrics,
                        labels={
                            **item.input_data.labels,
                            "datajuicer_operator": request.provider_operator_ref,
                            "datajuicer_output": {
                                **(
                                    item.input_data.labels.get("datajuicer_output", {})
                                    if isinstance(
                                        item.input_data.labels.get("datajuicer_output"),
                                        dict,
                                    )
                                    else {}
                                ),
                                **output_fields,
                            },
                        },
                        artifacts=[*item.input_data.artifacts, *artifacts],
                        annotations=item.input_data.annotations,
                        embeddings=item.input_data.embeddings,
                        decision="continue" if kept is not None else "reject",
                        reason_codes=(
                            [] if kept is not None else ["DATAJUICER_FILTERED_OUT"]
                        ),
                    ),
                )
            )
        return ProviderDatasetExecuteResult(
            ok=True,
            items=tuple(results),
            duration_seconds=duration,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
        )

    @staticmethod
    def _emit(event_sink: Any, event_type: str, **details: Any) -> None:
        if isinstance(event_sink, Callable):
            event_sink(event_type, details)

    def _run(
        self,
        command: list[str],
        cwd: Path,
        environment: dict[str, str],
        cancel_check: Any,
        *,
        timeout_seconds: int,
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
            deadline = time.monotonic() + timeout_seconds
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
