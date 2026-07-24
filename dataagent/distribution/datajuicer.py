from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..operators.providers.catalog_normalization import normalize_provider_catalog
from ..operators.providers.datajuicer import DataJuicerOperatorProvider
from ..operators.providers.datajuicer_executor import DataJuicerSubprocessSearcher
from ..operators.providers.proxy import DATAJUICER_ADMISSIONS


DATAJUICER_VERSION = "1.5.3"
DATAJUICER_EXPECTED_CATALOG_COUNT = 217
DATAJUICER_PYTHON_VERSION = "3.12"
_REGISTRY_SCHEMA_VERSION = 1
_PROFILES = frozenset({"auto", "catalog", "cpu", "remote", "linux-gpu"})
_API_KEY_NAMES = ("OPENAI_API_KEY", "BAILIAN_API_KEY", "DASHSCOPE_API_KEY")


class ProviderInstallError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _descriptor_payload(descriptor: Any) -> dict[str, Any]:
    payload = descriptor.model_dump(mode="json")
    payload["tags"] = sorted(str(item) for item in descriptor.tags)
    payload["supported_runtime_backends"] = sorted(
        str(item) for item in descriptor.supported_runtime_backends
    )
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ProviderInstallError(f"Invalid provider registry: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProviderInstallError(f"Provider registry is not a JSON object: {path}")
    return value


def _install_artifacts(provider_home: Path) -> list[dict[str, Any]]:
    path = provider_home / "install-report.json"
    if not path.is_file():
        return []
    payload = _read_json(path)
    artifacts: list[dict[str, Any]] = []
    for item in payload.get("install", []):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        download = (
            item.get("download_info")
            if isinstance(item.get("download_info"), dict)
            else {}
        )
        archive = (
            download.get("archive_info")
            if isinstance(download.get("archive_info"), dict)
            else {}
        )
        hashes = archive.get("hashes") if isinstance(archive.get("hashes"), dict) else {}
        artifacts.append(
            {
                "name": metadata.get("name"),
                "version": metadata.get("version"),
                "url": download.get("url"),
                "sha256": hashes.get("sha256"),
                "requested": bool(item.get("requested")),
            }
        )
    return artifacts


def provider_registry_path(home: Path) -> Path:
    return home.expanduser().resolve() / "providers" / "registry.json"


def load_provider_registration(home: Path, provider_id: str) -> dict[str, Any] | None:
    path = provider_registry_path(home)
    if not path.is_file():
        return None
    try:
        payload = _read_json(path)
    except ProviderInstallError:
        return None
    if payload.get("schema_version") != _REGISTRY_SCHEMA_VERSION:
        return None
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return None
    record = providers.get(provider_id)
    return dict(record) if isinstance(record, dict) else None


def _runtime_python(runtime_dir: Path) -> Path:
    if os.name == "nt":
        return runtime_dir / "Scripts" / "python.exe"
    return runtime_dir / "bin" / "python"


def _managed_runtime_base(home: Path) -> Path:
    configured = os.getenv("DATAAGENT_PROVIDER_RUNTIME_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else home
        return (base / "DataAgent" / "runtimes").resolve()
    return (home / "providers" / "runtimes").resolve()


def _managed_runtime_dir(home: Path) -> Path:
    home_token = hashlib.sha256(str(home).lower().encode("utf-8")).hexdigest()[:10]
    return (
        _managed_runtime_base(home)
        / f"datajuicer-{DATAJUICER_VERSION}-{home_token}"
    ).resolve()


def _uv_executable() -> Path | None:
    names = ("uv.exe", "uv") if os.name == "nt" else ("uv",)
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
        sibling = Path(sys.executable).resolve().parent / name
        if sibling.is_file():
            return sibling
    return None


def _profile_packages(profile: str) -> tuple[str, ...]:
    base = f"py-data-juicer=={DATAJUICER_VERSION}"
    if profile == "catalog":
        return (base,)
    if profile == "cpu":
        return (base, "imagededup==0.3.3.post2")
    if profile == "remote":
        return (base, "openai>=1,<2")
    if profile == "linux-gpu":
        return (
            f"py-data-juicer[generic,vision]=={DATAJUICER_VERSION}",
            "openai>=1,<2",
        )
    return (base, "imagededup==0.3.3.post2", "openai>=1,<2")


def _probe_command(name: str, *arguments: str) -> dict[str, Any]:
    executable = shutil.which(name)
    if not executable:
        return {"available": False, "path": None, "summary": ""}
    try:
        completed = subprocess.run(
            [executable, *arguments],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        output = (completed.stdout or completed.stderr).strip().splitlines()
        return {
            "available": completed.returncode == 0,
            "path": executable,
            "summary": output[0][:500] if output else "",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "path": executable, "summary": str(exc)[:500]}


def probe_machine() -> dict[str, Any]:
    nvidia = _probe_command("nvidia-smi", "-L")
    ffmpeg = _probe_command("ffmpeg", "-version")
    system = platform.system().lower()
    return {
        "system": system,
        "release": platform.release(),
        "architecture": platform.machine().lower(),
        "python": platform.python_version(),
        "nvidia_smi": nvidia,
        "ffmpeg": ffmpeg,
        "linux_gpu_worker_available": system == "linux" and bool(nvidia["available"]),
        "remote_api_credentials_configured": any(os.getenv(name) for name in _API_KEY_NAMES),
    }


def _dependency_groups(tags: set[str]) -> list[str]:
    groups: list[str] = []
    if tags.intersection({"image", "video", "multimodal"}):
        groups.append("vision")
    if "audio" in tags:
        groups.append("audio")
    if tags.intersection({"gpu", "hf", "vllm"}):
        groups.append("generic")
    if "api" in tags:
        groups.append("ai-services")
    return list(dict.fromkeys(groups))


def _capability_entry(
    descriptor: Any,
    *,
    machine: dict[str, Any],
    installed_profile: str,
) -> dict[str, Any]:
    tags = set(descriptor.tags)
    runtime_candidates: list[str] = []
    if "cpu" in tags:
        runtime_candidates.append("local_cpu")
    if "api" in tags:
        runtime_candidates.append("remote_api")
    if "gpu" in tags:
        runtime_candidates.append("linux_gpu_worker")

    machine_compatible: list[str] = []
    if "local_cpu" in runtime_candidates:
        machine_compatible.append("local_cpu")
    if "remote_api" in runtime_candidates and machine["remote_api_credentials_configured"]:
        machine_compatible.append("remote_api")
    if "linux_gpu_worker" in runtime_candidates and machine["linux_gpu_worker_available"]:
        machine_compatible.append("linux_gpu_worker")

    admitted_refs = {item.ref for item in DATAJUICER_ADMISSIONS}
    admitted = descriptor.provider_operator_ref in admitted_refs
    governance_status = "PERSONAL_RELEASE" if admitted else "PROVIDER_AVAILABLE"
    callable_profiles: list[str] = []
    cpu_pack_installed = installed_profile in {"auto", "cpu", "linux-gpu"}
    remote_pack_installed = installed_profile in {"auto", "remote", "linux-gpu"}
    gpu_pack_installed = installed_profile == "linux-gpu"
    if cpu_pack_installed and "local_cpu" in machine_compatible:
        callable_profiles.append("local_cpu")
    if remote_pack_installed and "remote_api" in machine_compatible:
        callable_profiles.append("remote_api")
    if gpu_pack_installed and "linux_gpu_worker" in machine_compatible:
        callable_profiles.append("linux_gpu_worker")

    blocked_reasons: list[str] = []
    if "local_cpu" in runtime_candidates and not cpu_pack_installed:
        blocked_reasons.append("CPU_PROVIDER_PACK_REQUIRED")
    if "remote_api" in runtime_candidates and not remote_pack_installed:
        blocked_reasons.append("REMOTE_PROVIDER_PACK_REQUIRED")
    if "remote_api" in runtime_candidates and not machine["remote_api_credentials_configured"]:
        blocked_reasons.append("REMOTE_API_CREDENTIALS_REQUIRED")
    if "linux_gpu_worker" in runtime_candidates and not machine["linux_gpu_worker_available"]:
        blocked_reasons.append("LINUX_GPU_WORKER_REQUIRED")
    if tags.intersection({"video", "audio"}) and not machine["ffmpeg"]["available"]:
        blocked_reasons.append("FFMPEG_REQUIRED")
    dependency_groups = _dependency_groups(tags)

    return {
        "operator_ref": descriptor.provider_operator_ref,
        "operator_type": descriptor.provider_operator_type,
        "tags": sorted(tags),
        "execution_scope": str(descriptor.suggested_execution_scope),
        "runtime_candidates": runtime_candidates,
        "machine_compatible_profiles": machine_compatible,
        "governance_status": governance_status,
        "dataagent_verified": admitted,
        "callable_profiles": callable_profiles,
        "dependency_groups": dependency_groups,
        "blocked_reasons": list(dict.fromkeys(blocked_reasons)),
        "source_digest": descriptor.source_digest,
    }


class DataJuicerInstaller:
    def __init__(
        self,
        home: Path,
        *,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        searcher_factory: Callable[[Path], Any] = DataJuicerSubprocessSearcher,
    ) -> None:
        self.home = home.expanduser().resolve()
        self.command_runner = command_runner
        self.searcher_factory = searcher_factory

    def install(
        self,
        *,
        profile: str = "auto",
        existing_python: Path | None = None,
        wheelhouse: Path | None = None,
        force: bool = False,
        allow_catalog_drift: bool = False,
    ) -> dict[str, Any]:
        if profile not in _PROFILES:
            raise ProviderInstallError(
                f"Unsupported Data-Juicer profile {profile!r}; expected one of {sorted(_PROFILES)}"
            )
        machine = probe_machine()
        if profile == "linux-gpu" and not machine["linux_gpu_worker_available"]:
            raise ProviderInstallError(
                "The linux-gpu profile requires Linux and a working nvidia-smi runtime"
            )
        provider_home = (
            self.home / "providers" / "datajuicer" / DATAJUICER_VERSION
        ).resolve()
        runtime_dir = _managed_runtime_dir(self.home)
        marker = provider_home / ".installing"
        if force and provider_home.exists():
            self._remove_provider_home(provider_home)
        if force and runtime_dir.exists():
            self._remove_managed_runtime(runtime_dir)
        provider_home.mkdir(parents=True, exist_ok=True)
        marker.write_text(_utc_now() + "\n", encoding="ascii")
        try:
            python_executable = (
                existing_python.expanduser().resolve()
                if existing_python is not None
                else self._install_runtime(runtime_dir, profile, wheelhouse, provider_home)
            )
            if not python_executable.is_file():
                raise ProviderInstallError(
                    f"Data-Juicer Python executable not found: {python_executable}"
                )
            dependency_lock_digest = self._write_lock(python_executable, provider_home)
            result = self._discover_and_register(
                provider_home=provider_home,
                runtime_dir=runtime_dir,
                python_executable=python_executable,
                profile=profile,
                machine=machine,
                allow_catalog_drift=allow_catalog_drift,
                dependency_lock_digest=dependency_lock_digest,
            )
            marker.unlink(missing_ok=True)
            return result
        except Exception:
            raise

    def verify(self) -> dict[str, Any]:
        record = load_provider_registration(self.home, "datajuicer")
        if record is None:
            raise ProviderInstallError("Data-Juicer Provider is not registered")
        provider_home = Path(str(record["install_root"])).resolve()
        runtime_dir = Path(str(record.get("runtime_root") or provider_home / "runtime"))
        python_executable = Path(str(record["python"])).resolve()
        result = self._discover_and_register(
            provider_home=provider_home,
            runtime_dir=runtime_dir,
            python_executable=python_executable,
            profile=str(record.get("profile") or "auto"),
            machine=probe_machine(),
            allow_catalog_drift=False,
            dependency_lock_digest=str(record.get("dependency_lock_digest") or ""),
        )
        result["verified"] = True
        return result

    def report(self) -> dict[str, Any]:
        record = load_provider_registration(self.home, "datajuicer")
        if record is None:
            raise ProviderInstallError("Data-Juicer Provider is not registered")
        path = Path(str(record.get("capability_report"))).resolve()
        if not path.is_file():
            raise ProviderInstallError(f"Provider capability report is missing: {path}")
        return _read_json(path)

    def _install_runtime(
        self,
        runtime_dir: Path,
        profile: str,
        wheelhouse: Path | None,
        provider_home: Path,
    ) -> Path:
        python_executable = _runtime_python(runtime_dir)
        if not python_executable.is_file():
            if sys.version_info[:2] <= (3, 12):
                create_command = [sys.executable, "-m", "venv", str(runtime_dir)]
            else:
                uv = _uv_executable()
                if uv is None:
                    raise ProviderInstallError(
                        "Data-Juicer 1.5.3 requires a Python 3.12 runtime on this platform, "
                        "but the uv runtime manager is unavailable"
                    )
                create_command = [
                    str(uv),
                    "venv",
                    "--python",
                    DATAJUICER_PYTHON_VERSION,
                    "--seed",
                    str(runtime_dir),
                ]
            self._run_checked(
                create_command,
                timeout=600,
                description="create isolated Data-Juicer Python 3.12 runtime",
            )
        command = [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--report",
            str(provider_home / "install-report.json"),
        ]
        if wheelhouse is not None:
            resolved_wheelhouse = wheelhouse.expanduser().resolve()
            if not resolved_wheelhouse.is_dir():
                raise ProviderInstallError(f"Wheelhouse directory not found: {resolved_wheelhouse}")
            command.extend(["--no-index", "--find-links", str(resolved_wheelhouse)])
        command.extend(_profile_packages(profile))
        self._run_checked(command, timeout=3600, description="install Data-Juicer Provider")
        return python_executable

    def _discover_and_register(
        self,
        *,
        provider_home: Path,
        runtime_dir: Path,
        python_executable: Path,
        profile: str,
        machine: dict[str, Any],
        allow_catalog_drift: bool,
        dependency_lock_digest: str,
    ) -> dict[str, Any]:
        searcher = self.searcher_factory(python_executable)
        health = searcher.health()
        provider_version = str(health.get("provider_version") or "unavailable")
        if provider_version != DATAJUICER_VERSION:
            raise ProviderInstallError(
                f"Data-Juicer version mismatch: expected {DATAJUICER_VERSION}, got {provider_version}"
            )
        process_bin = Path(str(health.get("process_bin") or "")).expanduser().resolve()
        if not process_bin.is_file():
            raise ProviderInstallError(f"dj-process executable was not found: {process_bin}")
        provider = DataJuicerOperatorProvider(
            searcher_factory=lambda: searcher,
            provider_version=provider_version,
        )
        descriptors = provider.refresh_catalog()
        if len(descriptors) != DATAJUICER_EXPECTED_CATALOG_COUNT and not allow_catalog_drift:
            raise ProviderInstallError(
                "Data-Juicer catalog count mismatch: expected "
                f"{DATAJUICER_EXPECTED_CATALOG_COUNT}, got {len(descriptors)}"
            )
        descriptors = sorted(
            descriptors, key=lambda item: item.provider_operator_ref
        )
        normalized = normalize_provider_catalog(descriptors)
        catalog_payload = {
            "provider_id": "datajuicer",
            "provider_version": provider_version,
            "expected_operator_count": DATAJUICER_EXPECTED_CATALOG_COUNT,
            "operator_count": len(descriptors),
            "operators": [_descriptor_payload(item) for item in descriptors],
        }
        catalog_digest = _digest(catalog_payload["operators"])
        catalog_payload["catalog_digest"] = catalog_digest
        catalog_path = provider_home / "catalog.json"
        _write_json(catalog_path, catalog_payload)

        operators = [
            _capability_entry(
                item.descriptor,
                machine=machine,
                installed_profile=profile,
            )
            for item in normalized
        ]
        counts = {
            "discovered": len(operators),
            "local_cpu_candidates": sum(
                "local_cpu" in item["runtime_candidates"] for item in operators
            ),
            "remote_api_candidates": sum(
                "remote_api" in item["runtime_candidates"] for item in operators
            ),
            "linux_gpu_candidates": sum(
                "linux_gpu_worker" in item["runtime_candidates"] for item in operators
            ),
            "provider_callable_now": sum(
                bool(item["callable_profiles"]) for item in operators
            ),
            "dataagent_verified": sum(
                bool(item["dataagent_verified"]) for item in operators
            ),
            "provider_available": sum(
                item["governance_status"] == "PROVIDER_AVAILABLE"
                for item in operators
            ),
        }
        install_artifacts = _install_artifacts(provider_home)
        report = {
            "schema_version": 2,
            "generated_at": _utc_now(),
            "provider_id": "datajuicer",
            "provider_version": provider_version,
            "profile": profile,
            "machine": machine,
            "catalog_digest": catalog_digest,
            "dependency_lock_digest": dependency_lock_digest,
            "package_identity": health.get("package_identity") or {},
            "install_artifacts": install_artifacts,
            "counts": counts,
            "operators": operators,
        }
        report_path = provider_home / "capability-report.json"
        _write_json(report_path, report)
        record = {
            "provider_id": "datajuicer",
            "provider_version": provider_version,
            "profile": profile,
            "python": str(python_executable),
            "process_bin": str(process_bin),
            "install_root": str(provider_home),
            "runtime_root": str(runtime_dir),
            "catalog": str(catalog_path),
            "catalog_count": len(descriptors),
            "catalog_digest": catalog_digest,
            "dependency_lock_digest": dependency_lock_digest,
            "package_identity": health.get("package_identity") or {},
            "install_artifacts": install_artifacts,
            "capability_report": str(report_path),
            "registered_at": _utc_now(),
            "platform": machine,
        }
        self._save_registration(record)
        return {"registration": record, "report": report}

    def _save_registration(self, record: dict[str, Any]) -> None:
        path = provider_registry_path(self.home)
        payload: dict[str, Any] = {
            "schema_version": _REGISTRY_SCHEMA_VERSION,
            "providers": {},
        }
        if path.is_file():
            existing = _read_json(path)
            if existing.get("schema_version") == _REGISTRY_SCHEMA_VERSION:
                payload = existing
        providers = payload.setdefault("providers", {})
        if not isinstance(providers, dict):
            raise ProviderInstallError("Provider registry has an invalid providers field")
        providers["datajuicer"] = record
        payload["updated_at"] = _utc_now()
        _write_json(path, payload)

    def _write_lock(self, python_executable: Path, provider_home: Path) -> str:
        completed = self._run_checked(
            [str(python_executable), "-m", "pip", "freeze", "--all"],
            timeout=120,
            description="freeze Data-Juicer Provider dependencies",
        )
        content = completed.stdout.strip() + "\n"
        lock_path = provider_home / "requirements.lock.txt"
        lock_path.write_text(content, encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        (provider_home / "requirements.lock.sha256").write_text(
            digest + "\n", encoding="ascii"
        )
        return digest

    def _run_checked(
        self,
        command: Sequence[str],
        *,
        timeout: int,
        description: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = self.command_runner(
                list(command),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderInstallError(f"Failed to {description}: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-4000:]
            raise ProviderInstallError(f"Failed to {description}: {detail}")
        return completed

    def _remove_provider_home(self, provider_home: Path) -> None:
        providers_root = (self.home / "providers").resolve()
        resolved = provider_home.resolve()
        if resolved == providers_root or providers_root not in resolved.parents:
            raise ProviderInstallError(f"Refusing to remove unsafe provider path: {resolved}")
        shutil.rmtree(resolved)

    def _remove_managed_runtime(self, runtime_dir: Path) -> None:
        runtime_base = _managed_runtime_base(self.home)
        resolved = runtime_dir.resolve()
        if resolved == runtime_base or runtime_base not in resolved.parents:
            raise ProviderInstallError(f"Refusing to remove unsafe runtime path: {resolved}")
        shutil.rmtree(resolved)


__all__ = [
    "DATAJUICER_EXPECTED_CATALOG_COUNT",
    "DATAJUICER_VERSION",
    "DataJuicerInstaller",
    "ProviderInstallError",
    "load_provider_registration",
    "probe_machine",
    "provider_registry_path",
]
