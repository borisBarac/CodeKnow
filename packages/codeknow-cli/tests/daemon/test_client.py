from __future__ import annotations

import atexit
import contextlib
import os
import signal
import socket
from typing import TYPE_CHECKING

import pytest
from codeknow_cli.client import Client

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_started_pids: set[int] = set()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _atexit_cleanup() -> None:
    for pid in _started_pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


atexit.register(_atexit_cleanup)


@pytest.fixture
def client(tmp_path: Path) -> Generator[Client, None, None]:
    port = _free_port()
    pid_file = str(tmp_path / "test-daemon.pid")
    c = Client(host="127.0.0.1", port=port, pid_file=pid_file)
    yield c
    with contextlib.suppress(TimeoutError, RuntimeError):
        c.stop_daemon(timeout=2)


def test_start_daemon_returns_started(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result["pid"])
    assert result["status"] == "started"
    assert isinstance(result["pid"], int)


def test_start_daemon_process_is_alive(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result["pid"])
    assert client.check_daemon()


def test_stop_daemon_returns_stopped(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result["pid"])
    result = client.stop_daemon(timeout=5)
    assert result["status"] == "stopped"


def test_stop_daemon_clears_running_state(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result["pid"])
    client.stop_daemon(timeout=5)
    assert not client.check_daemon()


def test_check_daemon_false_when_not_running(client: Client) -> None:
    assert not client.check_daemon()


def test_client_uses_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEKNOW_HOST", "myhost")
    monkeypatch.setenv("CODEKNOW_PORT", "4321")
    c = Client()
    assert c.host == "myhost"
    assert c.port == 4321
    assert c.base_url == "http://myhost:4321"


def test_client_constructor_overrides_env(client: Client) -> None:
    assert client.host == "127.0.0.1"
    assert client.port != 9999
