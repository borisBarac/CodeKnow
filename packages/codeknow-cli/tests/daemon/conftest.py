from __future__ import annotations

import atexit
import contextlib
import os
import signal
import socket

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
