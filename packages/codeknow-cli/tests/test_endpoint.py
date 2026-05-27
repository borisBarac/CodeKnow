"""Tests for codeknow_cli.endpoint."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from codeknow_cli.endpoint import (
    _ENV_API_URL,
    _ENV_HOST,
    _ENV_PORT,
    DEFAULT_HOST,
    DEFAULT_PID_FILE,
    DEFAULT_PORT,
    resolve_endpoint,
)
from codeknow_cli.exceptions import ConfigError

_MOCK_WHICH = "codeknow_cli.endpoint.shutil.which"


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (_ENV_API_URL, _ENV_HOST, _ENV_PORT, "FAKE_SERVER"):
        monkeypatch.delenv(key, raising=False)


def _mocked_resolve(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> object:
    with patch(_MOCK_WHICH, return_value="/usr/local/bin/codeknow-api"):
        return resolve_endpoint(**kwargs)


class TestResolveEndpointRemote:
    def test_returns_remote_when_api_url_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv(_ENV_API_URL, "https://api.example.com")
        cfg = resolve_endpoint()
        assert cfg.is_remote is True

    def test_base_url_strips_trailing_slash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv(_ENV_API_URL, "https://api.example.com/")
        cfg = resolve_endpoint()
        assert cfg.base_url == "https://api.example.com"

    def test_base_url_no_slash_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv(_ENV_API_URL, "https://api.example.com")
        cfg = resolve_endpoint()
        assert cfg.base_url == "https://api.example.com"

    def test_remote_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv(_ENV_API_URL, "https://remote.host")
        cfg = resolve_endpoint()
        assert cfg.host == ""
        assert cfg.port == 0
        assert cfg.bind_host == ""
        assert cfg.pid_file == DEFAULT_PID_FILE
        assert cfg.worker_command is None

    def test_ignores_host_port_pid_file_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv(_ENV_API_URL, "https://remote.host")
        cfg = resolve_endpoint(
            host="custom",
            port=9999,
            pid_file="/tmp/x.pid",  # noqa: S108
        )
        assert cfg.is_remote is True
        assert cfg.host == ""
        assert cfg.port == 0
        assert cfg.pid_file == DEFAULT_PID_FILE


class TestResolveEndpointLocalDefaults:
    def test_default_host_and_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        cfg = _mocked_resolve(monkeypatch)
        assert cfg.host == DEFAULT_HOST
        assert cfg.port == DEFAULT_PORT

    def test_bind_host_maps_localhost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        cfg = _mocked_resolve(monkeypatch)
        assert cfg.bind_host == "127.0.0.1"

    def test_base_url_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        cfg = _mocked_resolve(monkeypatch)
        assert cfg.base_url == f"http://127.0.0.1:{DEFAULT_PORT}"

    def test_is_remote_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        cfg = _mocked_resolve(monkeypatch)
        assert cfg.is_remote is False

    def test_default_pid_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        cfg = _mocked_resolve(monkeypatch)
        assert cfg.pid_file == DEFAULT_PID_FILE


class TestResolveEndpointLocalExplicit:
    def test_explicit_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        cfg = _mocked_resolve(monkeypatch, host="0.0.0.0")  # noqa: S104
        assert cfg.host == "0.0.0.0"  # noqa: S104

    def test_explicit_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        cfg = _mocked_resolve(monkeypatch, port=9090)
        assert cfg.port == 9090

    def test_explicit_pid_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        cfg = _mocked_resolve(monkeypatch, pid_file="/var/run/ck.pid")
        assert cfg.pid_file == "/var/run/ck.pid"

    def test_custom_host_not_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        cfg = _mocked_resolve(monkeypatch, host="192.168.1.1", port=3000)
        assert cfg.bind_host == "192.168.1.1"
        assert cfg.base_url == "http://192.168.1.1:3000"

    def test_localhost_explicit_maps_to_bind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_env(monkeypatch)
        cfg = _mocked_resolve(monkeypatch, host="localhost", port=4000)
        assert cfg.bind_host == "127.0.0.1"
        assert cfg.base_url == "http://127.0.0.1:4000"


class TestResolveEndpointLocalEnvFallback:
    def test_env_host_used_when_no_arg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv(_ENV_HOST, "10.0.0.1")
        cfg = _mocked_resolve(monkeypatch)
        assert cfg.host == "10.0.0.1"

    def test_env_port_used_when_no_arg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv(_ENV_PORT, "7070")
        cfg = _mocked_resolve(monkeypatch)
        assert cfg.port == 7070

    def test_arg_overrides_env_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv(_ENV_HOST, "10.0.0.1")
        cfg = _mocked_resolve(monkeypatch, host="custom.local")
        assert cfg.host == "custom.local"

    def test_arg_overrides_env_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv(_ENV_PORT, "7070")
        cfg = _mocked_resolve(monkeypatch, port=5050)
        assert cfg.port == 5050


class TestResolveEndpointWorkerCommand:
    def test_fake_server_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv("FAKE_SERVER", "1")
        cfg = resolve_endpoint(host="localhost", port=5555)
        assert cfg.worker_command is not None
        assert cfg.worker_command[0] == sys.executable
        assert cfg.worker_command[1] == "-c"
        assert "fake_server" in cfg.worker_command[2]
        assert "127.0.0.1" in cfg.worker_command[2]
        assert "5555" in cfg.worker_command[2]

    def test_fake_server_true_lowercase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv("FAKE_SERVER", "true")
        cfg = resolve_endpoint()
        assert cfg.worker_command is not None
        assert cfg.worker_command[0] == sys.executable

    def test_real_api_binary_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        with patch(_MOCK_WHICH, return_value="/usr/local/bin/codeknow-api"):
            cfg = resolve_endpoint(host="0.0.0.0", port=6000)  # noqa: S104
        assert cfg.worker_command == [
            "/usr/local/bin/codeknow-api",
            "--host",
            "0.0.0.0",  # noqa: S104
            "--port",
            "6000",
        ]

    def test_raises_config_error_when_binary_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_env(monkeypatch)
        with (
            patch(_MOCK_WHICH, return_value=None),
            pytest.raises(ConfigError, match="codeknow-api is not installed"),
        ):
            resolve_endpoint()

    def test_fake_server_false_uses_real_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv("FAKE_SERVER", "0")
        with patch(_MOCK_WHICH, return_value="/bin/codeknow-api"):
            cfg = resolve_endpoint()
        assert cfg.worker_command is not None
        assert cfg.worker_command[0] == "/bin/codeknow-api"


class TestResolveEndpointPriority:
    def test_api_url_takes_priority_over_local_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_ENV_API_URL, "https://cloud.example.com")
        monkeypatch.setenv(_ENV_HOST, "should-be-ignored")
        monkeypatch.setenv(_ENV_PORT, "9999")
        cfg = resolve_endpoint(host="also-ignored", port=1234)
        assert cfg.is_remote is True
        assert cfg.base_url == "https://cloud.example.com"
