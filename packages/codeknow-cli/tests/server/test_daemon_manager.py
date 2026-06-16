from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from codeknow_cli.daemon_manager import DaemonManager
from codeknow_cli.exceptions import DaemonAlreadyRunningError, DaemonTimeoutError

from .conftest import _free_port, _started_pids

if TYPE_CHECKING:
    from collections.abc import Generator


def _worker_command(port: int) -> list[str]:
    code = (
        "from codeknow_cli.daemon.fake_server import run_server;"
        f" run_server(port={port})"
    )
    return [sys.executable, "-c", code]


@pytest.fixture
def daemon_manager(tmp_path: Path) -> Generator[DaemonManager, None, None]:
    pid_file = str(tmp_path / "test-daemon.pid")
    port = _free_port()
    manager = DaemonManager(pid_file=pid_file, worker_command=_worker_command(port))
    yield manager
    with contextlib.suppress(DaemonTimeoutError, DaemonAlreadyRunningError):
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
    with pytest.raises(DaemonAlreadyRunningError, match="already running"):
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


def test_stop_returns_true_for_tracked_process(daemon_manager: DaemonManager) -> None:
    pid = daemon_manager.start()
    _started_pids.add(pid)
    assert daemon_manager.stop(timeout=5) is True


def test_stop_returns_false_when_nothing_running(tmp_path: Path) -> None:
    manager = DaemonManager(
        pid_file=str(tmp_path / "absent.pid"),
        worker_command=[],
    )
    assert manager.stop(timeout=2) is False


def test_stop_by_pid_kills_cross_process_daemon(tmp_path: Path) -> None:
    pid_file = str(tmp_path / "cross.pid")
    port = _free_port()
    starter = DaemonManager(pid_file=pid_file, worker_command=_worker_command(port))
    pid = starter.start()
    _started_pids.add(pid)

    # A fresh manager has no tracked proc, so stop() must go via _stop_by_pid.
    other = DaemonManager(pid_file=pid_file, worker_command=_worker_command(port))
    assert other.stop(timeout=5) is True
    assert not other.is_running()
    assert not Path(pid_file).exists()
