from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call

import httpx
import pytest
from codeknow_cli.config import UserConfig
from codeknow_cli.exceptions import CodeknowError, ConfigError
from codeknow_cli.server import (
    DOCKER_MODEL_NAME,
    DaemonBackend,
    DockerBackend,
    RemoteBackend,
    get_backend,
)

_COMPOSE_NOT_FOUND = r"infra/docker-compose\.yml not found"


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestDockerBackend:
    def _patches(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:", encoding="utf-8")
        which = MagicMock(return_value="/usr/local/bin/docker")
        monkeypatch.setattr("codeknow_cli.server.shutil.which", which)
        monkeypatch.setattr("codeknow_cli.server.COMPOSE_FILE", compose)

    def test_start_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._patches(monkeypatch, tmp_path)
        run = MagicMock(return_value=_completed(0, "ok"))
        monkeypatch.setattr("codeknow_cli.server.subprocess.run", run)
        DockerBackend().start()
        assert "Docker stack started." in capsys.readouterr().out

    def test_stop_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._patches(monkeypatch, tmp_path)
        compose = tmp_path / "docker-compose.yml"
        run = MagicMock(return_value=_completed(0))
        monkeypatch.setattr("codeknow_cli.server.subprocess.run", run)
        DockerBackend().stop()
        assert "Docker stack stopped." in capsys.readouterr().out
        assert run.call_args_list == [
            call(
                [
                    "/usr/local/bin/docker",
                    "compose",
                    "-f",
                    str(compose),
                    "down",
                ],
                capture_output=True,
                text=True,
                check=False,
            ),
            call(
                ["/usr/local/bin/docker", "model", "unload", DOCKER_MODEL_NAME],
                capture_output=True,
                text=True,
                check=False,
            ),
        ]

    def test_stop_warns_when_model_unload_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._patches(monkeypatch, tmp_path)
        run = MagicMock(
            side_effect=[
                _completed(0),
                _completed(1, stdout="fallback"),
            ]
        )
        monkeypatch.setattr("codeknow_cli.server.subprocess.run", run)
        DockerBackend().stop()
        captured = capsys.readouterr()
        assert "Docker stack stopped." in captured.out
        assert "Warning: docker model unload failed:" in captured.err
        assert "fallback" in captured.err

    def test_status_shows_stdout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._patches(monkeypatch, tmp_path)
        run = MagicMock(return_value=_completed(0, "container-list"))
        monkeypatch.setattr("codeknow_cli.server.subprocess.run", run)
        DockerBackend().status()
        assert "container-list" in capsys.readouterr().out

    def test_start_raises_on_nonzero_return(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patches(monkeypatch, tmp_path)
        run = MagicMock(return_value=_completed(1, stderr="boom"))
        monkeypatch.setattr("codeknow_cli.server.subprocess.run", run)
        with pytest.raises(CodeknowError):
            DockerBackend().start()

    def test_stop_raises_on_compose_failure_without_unload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patches(monkeypatch, tmp_path)
        run = MagicMock(return_value=_completed(1, stderr="boom"))
        monkeypatch.setattr("codeknow_cli.server.subprocess.run", run)
        with pytest.raises(CodeknowError):
            DockerBackend().stop()
        assert run.call_count == 1

    def test_raises_when_docker_not_installed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:", encoding="utf-8")
        monkeypatch.setattr(
            "codeknow_cli.server.shutil.which", MagicMock(return_value=None)
        )
        monkeypatch.setattr("codeknow_cli.server.COMPOSE_FILE", compose)
        with pytest.raises(CodeknowError, match="docker is not installed") as exc:
            DockerBackend().start()
        assert "Install Docker" in str(exc.value)
        assert "codeknow server mode daemon" in str(exc.value)

    def test_start_docker_missing_does_not_print_starting(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:", encoding="utf-8")
        monkeypatch.setattr(
            "codeknow_cli.server.shutil.which", MagicMock(return_value=None)
        )
        monkeypatch.setattr("codeknow_cli.server.COMPOSE_FILE", compose)
        with pytest.raises(CodeknowError):
            DockerBackend().start()
        assert "Starting docker stack..." not in capsys.readouterr().out

    def test_raises_when_compose_file_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        which = MagicMock(return_value="/usr/local/bin/docker")
        monkeypatch.setattr("codeknow_cli.server.shutil.which", which)
        monkeypatch.setattr(
            "codeknow_cli.server.COMPOSE_FILE",
            Path("/nonexistent/docker-compose.yml"),
        )
        with pytest.raises(CodeknowError, match=_COMPOSE_NOT_FOUND):
            DockerBackend().start()


class TestDaemonBackend:
    def _setup_manager(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        start_return: int | object = 42,
        is_running: bool = True,
        read_pid: int | object = 42,
    ) -> MagicMock:
        fake_endpoint = MagicMock()
        fake_endpoint.worker_command = ["x"]
        fake_endpoint.pid_file = "/tmp/p.pid"  # noqa: S108
        monkeypatch.setattr(
            "codeknow_cli.server.resolve_endpoint",
            MagicMock(return_value=fake_endpoint),
        )

        manager_instance = MagicMock()
        manager_instance.start.return_value = start_return
        manager_instance.is_running.return_value = is_running
        manager_instance.read_pid.return_value = read_pid
        manager_instance.stop.return_value = True

        manager_class = MagicMock(return_value=manager_instance)
        monkeypatch.setattr("codeknow_cli.server.DaemonManager", manager_class)
        return manager_instance

    def test_start(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._setup_manager(monkeypatch)
        DaemonBackend().start()
        assert "Daemon started (PID 42)." in capsys.readouterr().out

    def test_start_already_running(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from codeknow_cli.exceptions import DaemonAlreadyRunningError

        manager_instance = self._setup_manager(monkeypatch)
        msg = "already"
        manager_instance.start.side_effect = DaemonAlreadyRunningError(msg)
        DaemonBackend().start()
        assert "already" in capsys.readouterr().out

    def test_stop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        manager_instance = self._setup_manager(monkeypatch)
        DaemonBackend().stop()
        assert "Daemon stopped." in capsys.readouterr().out
        manager_instance.stop.assert_called_once()

    def test_stop_reports_not_running_when_noop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        manager_instance = self._setup_manager(monkeypatch)
        manager_instance.stop.return_value = False
        DaemonBackend().stop()
        assert "Daemon not running." in capsys.readouterr().out
        assert "Daemon stopped." not in capsys.readouterr().out

    def test_status_running(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._setup_manager(monkeypatch, is_running=True, read_pid=42)
        DaemonBackend().status()
        assert "Daemon: running (PID 42)." in capsys.readouterr().out

    def test_status_not_running(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._setup_manager(monkeypatch, is_running=False)
        DaemonBackend().status()
        assert "Daemon: not running." in capsys.readouterr().out

    def test_raises_when_no_worker_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_endpoint = MagicMock()
        fake_endpoint.worker_command = None
        monkeypatch.setattr(
            "codeknow_cli.server.resolve_endpoint",
            MagicMock(return_value=fake_endpoint),
        )
        with pytest.raises(ConfigError):
            DaemonBackend().start()


class TestRemoteBackend:
    def test_start_echoes_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        RemoteBackend("https://x").start()
        assert "nothing to start" in capsys.readouterr().out

    def test_stop_echoes_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        RemoteBackend("https://x").stop()
        assert "nothing to stop" in capsys.readouterr().out

    def test_status_reachable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        resp = MagicMock(status_code=200)
        monkeypatch.setattr(
            "codeknow_cli.server.httpx.get", MagicMock(return_value=resp)
        )
        RemoteBackend("https://x").status()
        assert "reachable" in capsys.readouterr().out

    def test_status_unreachable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        msg = "nope"
        monkeypatch.setattr(
            "codeknow_cli.server.httpx.get",
            MagicMock(side_effect=httpx.HTTPError(msg)),
        )
        RemoteBackend("https://x").status()
        assert "unreachable" in capsys.readouterr().out


class TestGetBackend:
    def test_docker_mode(self) -> None:
        backend = get_backend(UserConfig(mode="docker"))
        assert isinstance(backend, DockerBackend)

    def test_remote_mode(self) -> None:
        backend = get_backend(UserConfig(mode="remote", remote_url="https://x"))
        assert isinstance(backend, RemoteBackend)
        assert backend.url == "https://x"

    def test_remote_mode_empty_url_raises(self) -> None:
        with pytest.raises(ConfigError):
            get_backend(UserConfig(mode="remote", remote_url=""))

    def test_daemon_mode(self) -> None:
        backend = get_backend(UserConfig(mode="daemon"))
        assert isinstance(backend, DaemonBackend)
