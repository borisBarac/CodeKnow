from __future__ import annotations

import click
import pytest
from click.testing import CliRunner
from codeknow_cli.client import Client
from codeknow_cli.main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_cli_help_shows_daemon(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "daemon" in result.output


def test_daemon_help_shows_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["daemon", "--help"])
    assert result.exit_code == 0
    for cmd in ("start", "stop", "restart", "status"):
        assert cmd in result.output


def test_daemon_status_not_running(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["daemon", "status"])
    assert "not running" in result.output.lower()


def test_context_has_client() -> None:
    captured: dict[str, Client] = {}

    @cli.command("__test_ctx")
    @click.pass_context
    def _grab_ctx(ctx: click.Context) -> None:
        captured["client"] = ctx.obj["client"]

    runner = CliRunner()
    runner.invoke(cli, ["__test_ctx"], catch_exceptions=False)
    assert isinstance(captured["client"], Client)
    assert captured["client"].base_url == "http://127.0.0.1:9999"


def test_add_command_shows_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["add", "--help"])
    assert result.exit_code == 0
    assert "SSH_URL" in result.output


def test_add_command_shows_in_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "add" in result.output
