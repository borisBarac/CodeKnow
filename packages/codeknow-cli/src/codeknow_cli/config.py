from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from codeknow.pipeline.config import _CODEKNOW_HOME

VALID_MODES = {"docker", "remote", "daemon"}


@dataclass
class UserConfig:
    mode: str = "docker"
    remote_url: str = ""
    host: str = "localhost"
    port: int = 8080


CONFIG_PATH = _CODEKNOW_HOME / "config.jsonl"


def load_config(path: Path | None = None) -> UserConfig:
    target = path or CONFIG_PATH
    try:
        text = Path(target).read_text(encoding="utf-8")
    except OSError:
        return UserConfig()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return UserConfig()

    if not isinstance(data, dict):
        return UserConfig()

    mode = data.get("mode", "docker")
    if not isinstance(mode, str) or mode not in VALID_MODES:
        mode = "docker"

    remote_url = data.get("remote_url", "")
    if not isinstance(remote_url, str):
        remote_url = ""

    host = data.get("host", "localhost")
    if not isinstance(host, str):
        host = "localhost"

    port = 8080
    raw_port = data.get("port", 8080)
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = 8080

    return UserConfig(
        mode=mode,
        remote_url=remote_url,
        host=host,
        port=port,
    )


def save_config(cfg: UserConfig, path: Path | None = None) -> None:
    target = path or CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(cfg), separators=(",", ":"))
    Path(target).write_text(payload + "\n", encoding="utf-8")
