"""Thin API client for the codeknow daemon."""

from __future__ import annotations

import os
import sys
import time

import httpx
from code_know_api_client import errors as api_errors
from code_know_api_client.api.default import build_v1_build_post
from code_know_api_client.client import Client as GeneratedClient
from code_know_api_client.models.build_request import BuildRequest
from code_know_api_client.models.build_response import BuildResponse
from code_know_api_client.models.http_validation_error import HTTPValidationError
from code_know_api_client.types import Unset

from codeknow_cli.daemon_manager import DaemonManager

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9999
DEFAULT_PID_FILE = "/tmp/codeknow-daemon.pid"  # noqa: S108
_ENV_HOST = "CODEKNOW_HOST"
_ENV_PORT = "CODEKNOW_PORT"


class ClientError(Exception): ...


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

        self._api_client = GeneratedClient(
            base_url=self.base_url,
            raise_on_unexpected_status=True,
            timeout=httpx.Timeout(300.0),
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

    def add_to_index(self, ssh_url: str) -> BuildResponse:
        try:
            resp = build_v1_build_post.sync_detailed(
                client=self._api_client,
                body=BuildRequest(github_ssh_url=ssh_url),
            )
        except api_errors.UnexpectedStatus as exc:
            if exc.status_code == 409:
                msg = "Repo is already being built"
                raise ClientError(msg) from exc
            body = exc.content.decode(errors="ignore")
            msg_0 = f"Unexpected API status {exc.status_code}: {body}"
            raise ClientError(
                msg_0
            ) from exc

        if resp.status_code == 202 and isinstance(resp.parsed, BuildResponse):
            return resp.parsed
        if resp.status_code == 422 and isinstance(resp.parsed, HTTPValidationError):
            detail = resp.parsed.detail
            if not isinstance(detail, Unset) and detail:
                msgs = [str(d) for d in detail]
                msg_0 = f"Validation error: {', '.join(msgs)}"
                raise ClientError(msg_0)
            msg_0 = "Validation error: Invalid GitHub SSH URL"
            raise ClientError(msg_0)
        msg = f"Unexpected response from API (status {resp.status_code})"
        raise ClientError(msg)

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
