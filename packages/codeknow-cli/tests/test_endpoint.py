from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from codeknow_cli.config import UserConfig
from codeknow_cli.endpoint import (
    DEFAULT_API_URL,
    DEFAULT_HOST,
    DEFAULT_PID_FILE,
    DEFAULT_PORT,
    resolve_endpoint,
)
from codeknow_cli.exceptions import ConfigError


def _set_mode(monkeypatch: pytest.MonkeyPatch, cfg: UserConfig) -> None:
    monkeypatch.setattr(
        "codeknow_cli.endpoint.load_config", MagicMock(return_value=cfg)
    )


def test_docker_mode_defaults(monkeypatch: pytest.MonkeyPatch):
    _set_mode(monkeypatch, UserConfig(mode="docker"))
    cfg = resolve_endpoint()
    assert cfg.is_remote is True
    assert cfg.base_url == DEFAULT_API_URL
    assert cfg.worker_command is None
    assert cfg.host == DEFAULT_HOST
    assert cfg.port == DEFAULT_PORT
    assert cfg.bind_host == ""
    assert cfg.pid_file == DEFAULT_PID_FILE


def test_remote_mode_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch):
    _set_mode(
        monkeypatch, UserConfig(mode="remote", remote_url="https://api.example.com/")
    )
    cfg = resolve_endpoint()
    assert cfg.base_url == "https://api.example.com"
    assert cfg.is_remote is True
    assert cfg.host == ""
    assert cfg.port == 0
    assert cfg.worker_command is None


def test_remote_mode_empty_url_raises(monkeypatch: pytest.MonkeyPatch):
    _set_mode(monkeypatch, UserConfig(mode="remote", remote_url=""))
    with pytest.raises(ConfigError):
        resolve_endpoint()


def test_daemon_mode_fake_server_localhost(monkeypatch: pytest.MonkeyPatch):
    _set_mode(monkeypatch, UserConfig(mode="daemon"))
    monkeypatch.setenv("FAKE_SERVER", "1")
    cfg = resolve_endpoint()
    assert cfg.is_remote is False
    assert cfg.worker_command is not None
    assert cfg.base_url == "http://127.0.0.1:8080"
    assert cfg.bind_host == "127.0.0.1"


def test_daemon_mode_fake_server_custom_host(monkeypatch: pytest.MonkeyPatch):
    _set_mode(
        monkeypatch,
        UserConfig(mode="daemon", host="0.0.0.0", port=6000),  # noqa: S104
    )
    monkeypatch.setenv("FAKE_SERVER", "1")
    cfg = resolve_endpoint()
    assert cfg.bind_host == "0.0.0.0"  # noqa: S104
    assert cfg.base_url == "http://0.0.0.0:6000"
    assert cfg.is_remote is False


def test_daemon_mode_real_binary(monkeypatch: pytest.MonkeyPatch):
    _set_mode(monkeypatch, UserConfig(mode="daemon", host="192.168.1.1", port=3000))
    monkeypatch.delenv("FAKE_SERVER", raising=False)
    which = MagicMock(return_value="/usr/local/bin/codeknow-api")
    with patch("codeknow_cli.endpoint.shutil.which", which):
        cfg = resolve_endpoint()
    assert cfg.worker_command == [
        "/usr/local/bin/codeknow-api",
        "--host",
        "192.168.1.1",
        "--port",
        "3000",
    ]
    assert cfg.base_url == "http://192.168.1.1:3000"
    assert cfg.is_remote is False


def test_daemon_mode_binary_missing_raises(monkeypatch: pytest.MonkeyPatch):
    _set_mode(monkeypatch, UserConfig(mode="daemon"))
    monkeypatch.delenv("FAKE_SERVER", raising=False)
    which = MagicMock(return_value=None)
    with (
        patch("codeknow_cli.endpoint.shutil.which", which),
        pytest.raises(ConfigError, match="codeknow-api is not installed"),
    ):
        resolve_endpoint()
