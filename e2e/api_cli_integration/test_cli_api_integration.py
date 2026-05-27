"""E2E test: CLI commands through the real FastAPI server with StubMiddleware.

Spins up the daemon using ``server.py`` (``uvicorn``) with
``CODEKNOW_STUB=1`` so that ``StubMiddleware`` intercepts every route and
returns canned JSON.  This exercises the full ASGI stack (FastAPI +
middleware) without requiring ChromaDB, Ollama, Redis, or any network
access.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import socket
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from codeknow_cli.client import Client, ClientError
from codeknow_cli.main import cli

if TYPE_CHECKING:
    from collections.abc import Sequence

_STARTED_PIDS: set[int] = set()
_CLIENT: Client | None = None
_CLI_ENV: dict[str, str] = {}
_SAVED_ENV: dict[str, str | None] = {}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _atexit_cleanup() -> None:
    for pid in _STARTED_PIDS:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def _save_env(keys: Sequence[str]) -> None:
    for key in keys:
        _SAVED_ENV[key] = os.environ.get(key)


def _restore_env() -> None:
    for key, original in _SAVED_ENV.items():
        if original is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original
    _SAVED_ENV.clear()


atexit.register(_atexit_cleanup)


@pytest.fixture(scope="module", autouse=True)
def _daemon_lifecycle(tmp_path_factory: pytest.TempPathFactory) -> None:
    global _CLIENT  # noqa: PLW0603

    _save_env(["CODEKNOW_STUB", "CODEKNOW_HOST", "CODEKNOW_API_PORT"])
    os.environ["CODEKNOW_STUB"] = "1"

    pid_file = str(tmp_path_factory.mktemp("daemon") / "test-daemon.pid")
    port = _free_port()

    _CLIENT = Client(host="127.0.0.1", port=port, pid_file=pid_file)
    result = _CLIENT.start_daemon(timeout=10)
    _STARTED_PIDS.add(result["pid"])

    _CLI_ENV.update(
        {
            "CODEKNOW_STUB": "1",
            "CODEKNOW_HOST": _CLIENT.host,
            "CODEKNOW_API_PORT": str(_CLIENT.port),
        }
    )

    yield

    if _CLIENT is not None:
        with contextlib.suppress(TimeoutError, RuntimeError):
            _CLIENT.stop_daemon(timeout=5)
        _STARTED_PIDS.discard(result["pid"])
    _restore_env()


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------


def test_daemon_starts_and_is_running() -> None:
    assert _CLIENT is not None
    assert _CLIENT.check_daemon()


# ---------------------------------------------------------------------------
# Client-level tests (API round-trip through StubMiddleware)
# ---------------------------------------------------------------------------


def test_add_to_index_returns_stub_response() -> None:
    assert _CLIENT is not None
    result = _CLIENT.add_to_index("git@github.com:stub/repo.git")
    assert result.status == "done"
    assert result.slug == "stub-repo"
    assert result.node_count == 10
    assert result.edge_count == 20


def test_search_returns_stub_response() -> None:
    assert _CLIENT is not None
    result = _CLIENT.search("test query")
    assert result["query"] == "test query"
    assert result["vector_hits"] == 0
    assert result["graph_expanded"] == 0
    assert result["results"] == []


def test_search_with_slug_filter() -> None:
    assert _CLIENT is not None
    result = _CLIENT.search("test", slugs=["stub-repo"])
    assert result["query"] == "test"
    assert result["results"] == []


def test_remove_from_index_returns_stub_response() -> None:
    assert _CLIENT is not None
    result = _CLIENT.remove_from_index("stub-repo")
    assert result["status"] == "deleted"
    assert result["slug"] == "stub-repo"
    assert result["chunks_deleted"] == 0


def test_remove_nonexistent_slug_raises() -> None:
    assert _CLIENT is not None
    with pytest.raises(ClientError, match="not found"):
        _CLIENT.remove_from_index("nonexistent")


# ---------------------------------------------------------------------------
# CLI-level tests (Click CliRunner → Client → StubMiddleware)
# ---------------------------------------------------------------------------


def test_cli_add_command() -> None:
    result = CliRunner().invoke(
        cli,
        ["add", "git@github.com:stub/repo.git"],
        env=_CLI_ENV,
    )
    assert result.exit_code == 0, result.output
    assert "Status: done" in result.output
    assert "stub-repo" in result.output


def test_cli_search_command() -> None:
    result = CliRunner().invoke(
        cli,
        ["search", "test"],
        env=_CLI_ENV,
    )
    assert result.exit_code == 0, result.output
    assert "Query: test" in result.output
    assert "0 vector" in result.output


def test_cli_remove_command() -> None:
    result = CliRunner().invoke(
        cli,
        ["remove", "stub-repo"],
        env=_CLI_ENV,
    )
    assert result.exit_code == 0, result.output
    assert "Status: deleted" in result.output


# ---------------------------------------------------------------------------
# Daemon stop (must be last test)
# ---------------------------------------------------------------------------


def test_daemon_stop_cleans_up() -> None:
    assert _CLIENT is not None
    pid_file = Path(_CLIENT._pid_file)  # noqa: SLF001

    _CLIENT.stop_daemon(timeout=5)
    assert not _CLIENT.check_daemon()
    assert not pid_file.exists()
