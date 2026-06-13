"""Resolve the API endpoint (remote URL or local daemon address)."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass

from codeknow_cli.config import load_config
from codeknow_cli.exceptions import ConfigError

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8080
DEFAULT_API_URL = "http://localhost:8080"
DEFAULT_PID_FILE = "/tmp/codeknow-daemon.pid"  # noqa: S108


@dataclass
class EndpointConfig:
    base_url: str
    is_remote: bool
    host: str
    port: int
    bind_host: str
    pid_file: str
    worker_command: list[str] | None = None


def resolve_endpoint() -> EndpointConfig:
    cfg = load_config()
    if cfg.mode == "docker":
        return EndpointConfig(
            base_url=DEFAULT_API_URL,
            is_remote=True,
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
            bind_host="",
            pid_file=DEFAULT_PID_FILE,
        )
    if cfg.mode == "remote":
        if not cfg.remote_url:
            msg = "remote_url is not set. Run: codeknow server mode remote"
            raise ConfigError(msg)
        return EndpointConfig(
            base_url=cfg.remote_url.rstrip("/"),
            is_remote=True,
            host="",
            port=0,
            bind_host="",
            pid_file=DEFAULT_PID_FILE,
        )
    return _resolve_daemon(cfg.host, cfg.port, DEFAULT_PID_FILE)


def _resolve_daemon(host: str, port: int, pid_file: str) -> EndpointConfig:
    bind_host = "127.0.0.1" if host == "localhost" else host

    if os.getenv("FAKE_SERVER", "").lower() in ("1", "true", "yes", "on"):
        worker_command = [
            sys.executable,
            "-c",
            (
                "from codeknow_cli.daemon.fake_server import run_server;"
                f" run_server(host={bind_host!r}, port={port})"
            ),
        ]
    else:
        api_bin = shutil.which("codeknow-api")
        if api_bin is None:
            msg = "codeknow-api is not installed. Run: uv sync"
            raise ConfigError(msg)
        worker_command = [
            api_bin,
            "--host",
            bind_host,
            "--port",
            str(port),
        ]

    return EndpointConfig(
        base_url=f"http://{bind_host}:{port}",
        is_remote=False,
        host=host,
        port=port,
        bind_host=bind_host,
        pid_file=pid_file,
        worker_command=worker_command,
    )
