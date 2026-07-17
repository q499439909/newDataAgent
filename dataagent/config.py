from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _env_path(name: str) -> Path | None:
    raw = os.getenv(name)
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    base_url: str
    planning_model: str
    vision_model: str
    home: Path
    owner: str
    env_path: Path | None
    allow_model_download: bool = False
    model_cache: Path | None = None
    datajuicer_enabled: bool = True
    datajuicer_python: Path | None = None
    datajuicer_process_bin: Path | None = None
    datajuicer_timeout_seconds: int = 300

    @classmethod
    def load(cls, cwd: Path | None = None) -> "Settings":
        root = (cwd or Path.cwd()).resolve()
        candidates = []
        explicit = os.getenv("DATAAGENT_ENV_FILE")
        if explicit:
            candidates.append(Path(explicit).expanduser())
        candidates.extend([root / "model.env", root / "model.env.txt", root / ".env"])
        env_path = next((path.resolve() for path in candidates if path.exists()), None)
        if env_path:
            _load_env_file(env_path)
        local_env = root / "dataagent.local.env"
        if local_env.exists():
            _load_env_file(local_env)

        home_raw = os.getenv("DATAAGENT_HOME", ".dataagent")
        home = Path(home_raw).expanduser()
        if not home.is_absolute():
            home = root / home
        model_cache_raw = os.getenv("DATAAGENT_MODEL_CACHE")
        model_cache = Path(model_cache_raw).expanduser() if model_cache_raw else home / "models"
        if not model_cache.is_absolute():
            model_cache = root / model_cache
        return cls(
            api_key=os.getenv("BAILIAN_API_KEY") or os.getenv("DASHSCOPE_API_KEY"),
            base_url=os.getenv(
                "BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/apps/anthropic"
            ).rstrip("/"),
            planning_model=os.getenv("CODE_MODEL", "glm-5.2"),
            vision_model=os.getenv("PRIMARY_VISION_MODEL", "qwen3.7-plus"),
            home=home.resolve(),
            owner=os.getenv("DATAAGENT_OWNER", os.getenv("USERNAME", "local")),
            env_path=env_path,
            allow_model_download=_env_bool("DATAAGENT_ALLOW_MODEL_DOWNLOAD", False),
            model_cache=model_cache.resolve(),
            datajuicer_enabled=_env_bool("DATAAGENT_DATAJUICER_ENABLED", True),
            datajuicer_python=_env_path("DATAAGENT_DATAJUICER_PYTHON"),
            datajuicer_process_bin=_env_path("DATAAGENT_DATAJUICER_PROCESS_BIN"),
            datajuicer_timeout_seconds=int(
                os.getenv("DATAAGENT_DATAJUICER_TIMEOUT_SECONDS", "300")
            ),
        )

    def ensure_directories(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "datasets").mkdir(exist_ok=True)
        (self.home / "reports").mkdir(exist_ok=True)
        if self.model_cache is not None:
            self.model_cache.mkdir(parents=True, exist_ok=True)
