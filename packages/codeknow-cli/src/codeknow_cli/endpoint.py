"""Resolve the API endpoint (remote URL or local daemon address)."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass

from codeknow_cli.exceptions import ConfigError

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8080
DEFAULT_PID_FILE = "/tmp/codeknow-daemon.pid"  # noqa: S108

_ENV_HOST = "CODEKNOW_HOST"
_ENV_PORT = "CODEKNOW_API_PORT"
_ENV_API_URL = "CODEKNOW_API_URL"


@dataclass
class EndpointConfig:
    base_url: str
    is_remote: bool
    host: str
    port: int
    bind_host: str
    pid_file: str
    worker_command: list[str] | None = None


def resolve_endpoint(
    host: str | None = None,
    port: int | None = None,
    pid_file: str | None = None,
) -> EndpointConfig:
    api_url = os.environ.get(_ENV_API_URL)
    if api_url is not None:
        return EndpointConfig(
            base_url=api_url.rstrip("/"),
            is_remote=True,
            host="",
            port=0,
            bind_host="",
            pid_file=DEFAULT_PID_FILE,
        )

    resolved_host = host or os.environ.get(_ENV_HOST) or DEFAULT_HOST
    resolved_port = port or int(os.environ.get(_ENV_PORT) or str(DEFAULT_PORT))
    bind_host = "127.0.0.1" if resolved_host == "localhost" else resolved_host
    resolved_pid_file = pid_file or DEFAULT_PID_FILE

    if os.getenv("FAKE_SERVER", "").lower() in ("1", "true"):
        worker_command = [
            sys.executable,
            "-c",
            (
                "from codeknow_cli.daemon.fake_server import run_server;"
                f" run_server(host={bind_host!r}, port={resolved_port})"
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
            str(resolved_port),
        ]

    return EndpointConfig(
        base_url=f"http://{bind_host}:{resolved_port}",
        is_remote=False,
        host=resolved_host,
        port=resolved_port,
        bind_host=bind_host,
        pid_file=resolved_pid_file,
        worker_command=worker_command,
    )
