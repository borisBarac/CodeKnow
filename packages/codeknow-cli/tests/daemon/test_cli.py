from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from code_know_api_client.models.list_repos_response import ListReposResponse
from code_know_api_client.models.repo_metadata import RepoMetadata
from codeknow_cli.client import Client, DeleteResult, SearchResult
from codeknow_cli.exceptions import ApiError, ClientError
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
    from codeknow_cli.endpoint import DEFAULT_PID_FILE

    pid_path = Path(DEFAULT_PID_FILE)
    backup = pid_path.read_bytes() if pid_path.exists() else None
    pid_path.unlink(missing_ok=True)
    try:
        result = runner.invoke(cli, ["daemon", "status"])
    finally:
        if backup is not None:
            pid_path.write_bytes(backup)
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
    assert captured["client"].base_url == "http://127.0.0.1:8080"


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
    mock_remove.return_value = DeleteResult(
        status="deleted", slug="my-repo", chunks_deleted=5
    )
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
    mock_search.return_value = SearchResult(
        query="my query", vector_hits=0, graph_expanded=0, results=[]
    )
    result = runner.invoke(cli, ["search", "my query"])
    assert result.exit_code == 0
    mock_search.assert_called_once_with("my query", slugs=None)


@patch.object(Client, "search")
def test_search_command_with_slugs(mock_search: MagicMock, runner: CliRunner) -> None:
    mock_search.return_value = SearchResult(
        query="my query", vector_hits=0, graph_expanded=0, results=[]
    )
    result = runner.invoke(cli, ["search", "my query", "--slug", "a", "--slug", "b"])
    assert result.exit_code == 0
    mock_search.assert_called_once_with("my query", slugs=["a", "b"])


# --- info command tests ---


def test_info_command_shows_in_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "info" in result.output


def test_info_command_shows_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["info", "--help"])
    assert result.exit_code == 0
    assert "daemon status" in result.output.lower() or "Show daemon" in result.output


@patch.object(Client, "check_daemon", return_value=False)
def test_info_when_daemon_not_running(mock_check: MagicMock, runner: CliRunner) -> None:
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "Daemon: not running" in result.output


@patch.object(Client, "list_repos")
@patch.object(Client, "check_daemon", return_value=True)
def test_info_when_daemon_running_with_repos(
    mock_check: MagicMock, mock_list: MagicMock, runner: CliRunner
) -> None:
    repos = [
        RepoMetadata(
            github_ssh_url="git@github.com:org/a.git",
            slug="repo-a",
            commit_hash="abc123",
            built_at="2025-01-01T00:00:00Z",
            node_count=10,
            edge_count=20,
            community_count=2,
        ),
        RepoMetadata(
            github_ssh_url="git@github.com:org/b.git",
            slug="repo-b",
            commit_hash="def456",
            built_at="2025-01-02T00:00:00Z",
            node_count=5,
            edge_count=10,
            community_count=1,
        ),
    ]
    mock_list.return_value = ListReposResponse(
        repos=repos, total=2, page=1, page_size=50
    )
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "Daemon: running" in result.output
    assert "repo-a" in result.output
    assert "repo-b" in result.output


@patch.object(Client, "list_repos")
@patch.object(Client, "check_daemon", return_value=True)
def test_info_when_daemon_running_no_repos(
    mock_check: MagicMock, mock_list: MagicMock, runner: CliRunner
) -> None:
    mock_list.return_value = ListReposResponse(repos=[], total=0, page=1, page_size=50)
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "Daemon: running" in result.output
    assert "none" in result.output.lower()


@patch.object(Client, "list_repos", side_effect=ApiError("unreachable"))
@patch.object(Client, "check_daemon", return_value=True)
def test_info_when_daemon_running_but_api_fails(
    mock_check: MagicMock, mock_list: MagicMock, runner: CliRunner
) -> None:
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "Daemon: running" in result.output
    assert "unavailable" in result.output.lower()
