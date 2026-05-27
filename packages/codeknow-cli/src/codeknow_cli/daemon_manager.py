"""Abstraction layer over daemonocle for daemon lifecycle management."""

from __future__ import annotations

import contextlib
import subprocess
import time
from pathlib import Path

import daemonocle

from codeknow_cli.exceptions import DaemonAlreadyRunningError, DaemonTimeoutError


class DaemonManager:
    """Manages the daemon process lifecycle.

    Wraps daemonocle to provide a clean interface for starting,
    stopping, and checking the status of the daemon process.
    """

    def __init__(self, pid_file: str, worker_command: list[str]) -> None:
        self._pid_file = Path(pid_file)
        self._worker_command = worker_command
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> int:
        if self.is_running():
            pid = self._read_pid()
            msg = f"Daemon already running (PID {pid})"
            raise DaemonAlreadyRunningError(msg)

        proc = subprocess.Popen(  # noqa: S603
            self._worker_command,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._proc = proc
        pid = proc.pid
        self._write_pid(pid)
        return pid

    def stop(self, timeout: float = 5.0) -> None:
        if self._proc is not None:
            self._stop_tracked(timeout)
            return
        self._stop_by_pid(timeout)

    def read_pid(self) -> int | None:
        return self._read_pid()

    def is_running(self) -> bool:
        if self._proc is not None:
            return self._proc.poll() is None
        d = daemonocle.Daemon(pid_file=str(self._pid_file))
        status = d.get_status(fields="status")
        return str(status.get("status")) != "dead"

    def _stop_tracked(self, timeout: float) -> None:
        proc = self._proc
        if proc is None:
            msg = "No tracked process to stop"
            raise RuntimeError(msg)
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        self._remove_pid_file()
        self._proc = None

    def _stop_by_pid(self, timeout: float) -> None:
        import os
        import signal

        pid = self._read_pid()
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            self._remove_pid_file()
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                self._remove_pid_file()
                return
            time.sleep(0.1)
        msg = f"Daemon (PID {pid}) did not stop within timeout"
        raise DaemonTimeoutError(msg)

    def _write_pid(self, pid: int) -> None:
        with self._pid_file.open("w") as f:
            f.write(str(pid))

    def _read_pid(self) -> int | None:
        try:
            with self._pid_file.open() as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return None

    def _remove_pid_file(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._pid_file.unlink()
