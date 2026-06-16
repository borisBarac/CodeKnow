"""Server lifecycle backends dispatched by configured mode."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click
import httpx

from codeknow_cli.config import UserConfig, load_config
from codeknow_cli.daemon_manager import DaemonManager
from codeknow_cli.endpoint import resolve_endpoint
from codeknow_cli.exceptions import (
    CodeknowError,
    ConfigError,
    DaemonAlreadyRunningError,
)

COMPOSE_FILE = Path("infra/docker-compose.yml")


class ServerBackend:
    """Base interface for mode-specific server lifecycle control."""

    def start(self) -> None:
        msg = "start not implemented"
        raise NotImplementedError(msg)

    def stop(self) -> None:
        msg = "stop not implemented"
        raise NotImplementedError(msg)

    def status(self) -> None:
        msg = "status not implemented"
        raise NotImplementedError(msg)


class DockerBackend(ServerBackend):
    """Manage the docker compose stack at infra/docker-compose.yml."""

    def _preflight(self) -> str:
        """Ensure docker is installed and the compose file exists.

        Returns the resolved docker binary path. Raises ``CodeknowError``
        if any prerequisite is missing.
        """
        docker_bin = shutil.which("docker")
        if docker_bin is None:
            msg = (
                "docker is not installed or not on PATH.\n"
                "Install Docker, or switch modes with: "
                "codeknow server mode daemon"
            )
            raise CodeknowError(msg)
        if not COMPOSE_FILE.exists():
            msg = (
                "infra/docker-compose.yml not found — "
                "run 'codeknow server start' from the repository root"
            )
            raise CodeknowError(msg)
        return docker_bin

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        docker_bin = self._preflight()
        cmd = [docker_bin, "compose", "-f", str(COMPOSE_FILE), *args]
        return subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603

    def start(self) -> None:
        self._preflight()
        click.echo("Starting docker stack...")
        result = self._run(["up", "-d"])
        if result.returncode != 0:
            msg = f"docker compose up failed:\n{result.stderr}"
            raise CodeknowError(msg)
        click.echo("Docker stack started.")

    def stop(self) -> None:
        click.echo("Stopping docker stack...")
        result = self._run(["down"])
        if result.returncode != 0:
            msg = f"docker compose down failed:\n{result.stderr}"
            raise CodeknowError(msg)
        click.echo("Docker stack stopped.")

    def status(self) -> None:
        result = self._run(["ps"])
        click.echo(result.stdout)
        if result.returncode != 0:
            click.echo(result.stderr, err=True)


class DaemonBackend(ServerBackend):
    """Manage the local daemon process via DaemonManager."""

    def _manager(self) -> DaemonManager:
        cfg = resolve_endpoint()
        if cfg.worker_command is None:
            msg = "daemon mode has no worker command"
            raise ConfigError(msg)
        return DaemonManager(pid_file=cfg.pid_file, worker_command=cfg.worker_command)

    def start(self) -> None:
        manager = self._manager()
        try:
            pid = manager.start()
        except DaemonAlreadyRunningError as exc:
            click.echo(str(exc))
            return
        click.echo(f"Daemon started (PID {pid}).")

    def stop(self) -> None:
        manager = self._manager()
        manager.stop()
        click.echo("Daemon stopped.")

    def status(self) -> None:
        manager = self._manager()
        if manager.is_running():
            pid = manager.read_pid()
            if pid:
                click.echo(f"Daemon: running (PID {pid}).")
            else:
                click.echo("Daemon: running.")
        else:
            click.echo("Daemon: not running.")


class RemoteBackend(ServerBackend):
    """No-op lifecycle backend for a remote server URL."""

    def __init__(self, url: str) -> None:
        self.url = url

    def start(self) -> None:
        click.echo(f"Remote server ({self.url}) — nothing to start.")

    def stop(self) -> None:
        click.echo(f"Remote server ({self.url}) — nothing to stop.")

    def status(self) -> None:
        reachable = False
        try:
            resp = httpx.get(f"{self.url.rstrip('/')}/v1/repos", timeout=3.0)
            reachable = resp.status_code == 200
        except httpx.HTTPError:
            reachable = False
        state = "reachable" if reachable else "unreachable"
        click.echo(f"Remote server ({self.url}): {state}.")


def get_backend(cfg: UserConfig | None = None) -> ServerBackend:
    """Return the server backend for the resolved (or provided) mode."""
    resolved = cfg or load_config()
    if resolved.mode == "docker":
        return DockerBackend()
    if resolved.mode == "remote":
        if not resolved.remote_url:
            msg = "remote_url is not set. Run: codeknow server mode remote"
            raise ConfigError(msg)
        return RemoteBackend(resolved.remote_url)
    return DaemonBackend()
