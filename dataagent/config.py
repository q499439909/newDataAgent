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


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    base_url: str
    planning_model: str
    vision_model: str
    home: Path
    owner: str
    env_path: Path | None

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

        home_raw = os.getenv("DATAAGENT_HOME", ".dataagent")
        home = Path(home_raw).expanduser()
        if not home.is_absolute():
            home = root / home
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
        )

    def ensure_directories(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "datasets").mkdir(exist_ok=True)
        (self.home / "reports").mkdir(exist_ok=True)

