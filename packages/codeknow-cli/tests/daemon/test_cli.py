from __future__ import annotations

from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from codeknow_cli.client import Client, ClientError
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


def test_remove_command_shows_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["remove", "--help"])
    assert result.exit_code == 0
    assert "SLUG" in result.output


def test_remove_command_shows_in_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "remove" in result.output


@patch.object(Client, "remove_from_index")
def test_remove_command_output(mock_remove: MagicMock, runner: CliRunner) -> None:
    mock_remove.return_value = {
        "status": "deleted",
        "slug": "my-repo",
        "chunks_deleted": 5,
    }
    result = runner.invoke(cli, ["remove", "my-repo"])
    assert result.exit_code == 0
    assert "Status: deleted" in result.output
    assert "Slug:   my-repo" in result.output
    assert "Chunks deleted: 5" in result.output


@patch.object(Client, "remove_from_index")
def test_remove_command_propagates_error(
    mock_remove: MagicMock, runner: CliRunner
) -> None:
    mock_remove.side_effect = ClientError("Repo with slug 'x' not found")
    result = runner.invoke(cli, ["remove", "x"])
    assert result.exception is not None
    assert isinstance(result.exception, ClientError)
    assert "Repo with slug 'x' not found" in str(result.exception)


def test_main_catches_client_error_and_exits() -> None:
    with patch("codeknow_cli.main.cli", side_effect=ClientError("boom")):
        from codeknow_cli.main import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


# --- search command tests ---


def test_search_command_shows_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["search", "--help"])
    assert result.exit_code == 0
    assert "QUERY" in result.output


def test_search_command_in_main_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "search" in result.output


@patch.object(Client, "search")
def test_search_command_calls_client(mock_search: MagicMock, runner: CliRunner) -> None:
    mock_search.return_value = {
        "query": "my query",
        "vector_hits": 0,
        "graph_expanded": 0,
        "results": [],
    }
    result = runner.invoke(cli, ["search", "my query"])
    assert result.exit_code == 0
    mock_search.assert_called_once_with("my query", slugs=None)


@patch.object(Client, "search")
def test_search_command_with_slugs(mock_search: MagicMock, runner: CliRunner) -> None:
    mock_search.return_value = {
        "query": "my query",
        "vector_hits": 0,
        "graph_expanded": 0,
        "results": [],
    }
    result = runner.invoke(cli, ["search", "my query", "--slug", "a", "--slug", "b"])
    assert result.exit_code == 0
    mock_search.assert_called_once_with("my query", slugs=["a", "b"])
