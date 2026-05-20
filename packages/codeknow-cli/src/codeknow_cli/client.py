"""Thin API client for the codeknow daemon."""

from __future__ import annotations

import os
import sys
import time

import httpx

from codeknow_cli.daemon_manager import DaemonManager

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9999
DEFAULT_PID_FILE = "/tmp/codeknow-daemon.pid"  # noqa: S108
_ENV_HOST = "CODEKNOW_HOST"
_ENV_PORT = "CODEKNOW_PORT"


class Client:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        pid_file: str | None = None,
    ) -> None:
        self.host = host or os.environ.get(_ENV_HOST, DEFAULT_HOST)
        self.port = port or int(os.environ.get(_ENV_PORT, str(DEFAULT_PORT)))
        self._bind_host = "127.0.0.1" if self.host == "localhost" else self.host
        self.base_url = f"http://{self._bind_host}:{self.port}"
        self._pid_file = pid_file or DEFAULT_PID_FILE
        self._daemon_pid: int | None = None

        self._manager = DaemonManager(
            pid_file=self._pid_file,
            worker_command=[
                sys.executable,
                "-c",
                (
                    "from codeknow_cli.daemon.fake_server import run_server;"
                    f" run_server(host={self._bind_host!r}, port={self.port})"
                ),
            ],
        )

    def start_daemon(self, timeout: float = 5.0) -> dict:
        pid = self._manager.start()
        self._daemon_pid = pid
        self._wait_for_ready(timeout)
        return {"status": "started", "pid": pid}

    def stop_daemon(self, timeout: float = 5.0) -> dict:
        self._manager.stop(timeout=timeout)
        self._daemon_pid = None
        return {"status": "stopped"}

    def check_daemon(self) -> bool:
        return self._manager.is_running()

    def add_to_index(self, ssh_url: str) -> dict:
        raise NotImplementedError

    def search(self, query: str, slug: str | None = None) -> dict:
        raise NotImplementedError

    def remove_from_index(self, slug: str) -> dict:
        raise NotImplementedError

    def _wait_for_ready(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(f"{self.base_url}/v1/repos", timeout=1.0)
                if resp.status_code == 200:
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            time.sleep(0.1)
        msg = "Daemon did not become ready within timeout"
        raise TimeoutError(msg)
