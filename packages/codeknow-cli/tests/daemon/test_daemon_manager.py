from __future__ import annotations

import atexit
import contextlib
import os
import signal
import socket
import sys
from typing import TYPE_CHECKING

import pytest
from codeknow_cli.daemon_manager import DaemonManager

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_started_pids: set[int] = set()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _worker_command(port: int) -> list[str]:
    code = (
        "from codeknow_cli.daemon.fake_server import run_server;"
        f" run_server(port={port})"
    )
    return [sys.executable, "-c", code]


def _atexit_cleanup() -> None:
    for pid in _started_pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


atexit.register(_atexit_cleanup)


@pytest.fixture
def daemon_manager(tmp_path: Path) -> Generator[DaemonManager, None, None]:
    pid_file = str(tmp_path / "test-daemon.pid")
    port = _free_port()
    manager = DaemonManager(pid_file=pid_file, worker_command=_worker_command(port))
    yield manager
    with contextlib.suppress(TimeoutError, RuntimeError):
        manager.stop(timeout=2)


def test_start_starts_process(daemon_manager: DaemonManager) -> None:
    pid = daemon_manager.start()
    _started_pids.add(pid)
    assert daemon_manager.is_running()
    assert isinstance(pid, int)


def test_start_writes_pid_file(daemon_manager: DaemonManager, tmp_path: Path) -> None:
    pid = daemon_manager.start()
    _started_pids.add(pid)
    pid_file = tmp_path / "test-daemon.pid"
    assert pid_file.exists()
    assert int(pid_file.read_text().strip()) == pid


def test_start_raises_if_already_running(daemon_manager: DaemonManager) -> None:
    pid = daemon_manager.start()
    _started_pids.add(pid)
    with pytest.raises(RuntimeError, match="already running"):
        daemon_manager.start()


def test_stop_kills_process(daemon_manager: DaemonManager) -> None:
    pid = daemon_manager.start()
    _started_pids.add(pid)
    daemon_manager.stop(timeout=5)
    assert not daemon_manager.is_running()


def test_stop_removes_pid_file(daemon_manager: DaemonManager, tmp_path: Path) -> None:
    pid = daemon_manager.start()
    _started_pids.add(pid)
    daemon_manager.stop(timeout=5)
    assert not (tmp_path / "test-daemon.pid").exists()


def test_stop_is_noop_when_not_running(daemon_manager: DaemonManager) -> None:
    daemon_manager.stop(timeout=2)


def test_is_running_false_when_no_pid_file(tmp_path: Path) -> None:
    manager = DaemonManager(
        pid_file=str(tmp_path / "nonexistent.pid"),
        worker_command=[],
    )
    assert not manager.is_running()
