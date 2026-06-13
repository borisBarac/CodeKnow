from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from code_know_api_client.models.list_repos_response import ListReposResponse
from code_know_api_client.models.repo_metadata import RepoMetadata
from codeknow_cli.client import Client, DeleteResult, SearchResult
from codeknow_cli.config import UserConfig
from codeknow_cli.exceptions import ApiError, ClientError
from codeknow_cli.main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _server_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: pretend the API server is reachable for decorated commands.

    Down-path tests override ``Client.check_server`` to return False.
    """
    monkeypatch.setattr(Client, "check_server", lambda *_args, **_kwargs: True)


def test_cli_help_shows_server(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "server" in result.output
    assert "daemon" not in result.output


def test_server_help_shows_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["server", "--help"])
    assert result.exit_code == 0
    for cmd in ("mode", "start", "stop", "status"):
        assert cmd in result.output


def test_server_mode_shows_current(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "codeknow_cli.main.load_config",
        MagicMock(return_value=UserConfig(mode="daemon")),
    )
    result = runner.invoke(cli, ["server", "mode"])
    assert result.exit_code == 0
    assert "Mode: daemon" in result.output


def test_server_mode_sets_valid(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_save = MagicMock()
    monkeypatch.setattr("codeknow_cli.main.save_config", mock_save)
    result = runner.invoke(cli, ["server", "mode", "remote"])
    assert result.exit_code == 0
    assert "Mode set to: remote" in result.output
    mock_save.assert_called_once()
    saved_cfg = mock_save.call_args[0][0]
    assert isinstance(saved_cfg, UserConfig)
    assert saved_cfg.mode == "remote"


def test_server_mode_rejects_invalid(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["server", "mode", "bogus"])
    assert result.exit_code != 0
    assert "invalid mode" in result.output


def test_server_start_dispatches(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = MagicMock()
    monkeypatch.setattr("codeknow_cli.main.get_backend", MagicMock(return_value=fake))
    result = runner.invoke(cli, ["server", "start"])
    assert result.exit_code == 0
    fake.start.assert_called_once()


def test_server_stop_dispatches(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = MagicMock()
    monkeypatch.setattr("codeknow_cli.main.get_backend", MagicMock(return_value=fake))
    result = runner.invoke(cli, ["server", "stop"])
    assert result.exit_code == 0
    fake.stop.assert_called_once()


def test_server_status_dispatches(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = MagicMock()
    monkeypatch.setattr("codeknow_cli.main.get_backend", MagicMock(return_value=fake))
    result = runner.invoke(cli, ["server", "status"])
    assert result.exit_code == 0
    fake.status.assert_called_once()


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


@patch.object(Client, "check_server", return_value=False)
def test_add_when_server_down_exits_1(mock_check: MagicMock, runner: CliRunner) -> None:
    result = runner.invoke(cli, ["add", "git@github.com:org/repo.git"])
    assert result.exit_code == 1
    assert "Server is not running" in result.output
    assert "codeknow server start" in result.output


@patch.object(Client, "check_server", return_value=False)
def test_remove_when_server_down_exits_1(
    mock_check: MagicMock, runner: CliRunner
) -> None:
    result = runner.invoke(cli, ["remove", "my-repo"])
    assert result.exit_code == 1
    assert "Server is not running" in result.output


@patch.object(Client, "check_server", return_value=False)
def test_search_when_server_down_exits_1(
    mock_check: MagicMock, runner: CliRunner
) -> None:
    result = runner.invoke(cli, ["search", "anything"])
    assert result.exit_code == 1
    assert "Server is not running" in result.output


def test_main_catches_client_error_and_exits() -> None:
    with patch("codeknow_cli.main.cli", side_effect=ClientError("boom")):
        from codeknow_cli.main import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


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


def test_info_command_shows_in_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "info" in result.output


def test_info_command_shows_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["info", "--help"])
    assert result.exit_code == 0
    assert "API endpoint status" in result.output or "repo slugs" in result.output


@patch.object(Client, "list_repos")
def test_info_remote_mode_shows_api_and_repos(
    mock_list: MagicMock, runner: CliRunner
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
    ]
    mock_list.return_value = ListReposResponse(
        repos=repos, total=1, page=1, page_size=50
    )
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "API:" in result.output
    assert "repo-a" in result.output


@patch.object(Client, "list_repos", side_effect=ApiError("unreachable"))
def test_info_remote_mode_api_error(mock_list: MagicMock, runner: CliRunner) -> None:
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "unavailable" in result.output.lower()


def _daemon_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "codeknow_cli.endpoint.load_config",
        MagicMock(return_value=UserConfig(mode="daemon")),
    )


@patch.object(Client, "check_server", return_value=False)
def test_info_when_daemon_not_running(
    mock_check: MagicMock,
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _daemon_mode(monkeypatch)
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "Daemon: not running" in result.output


@patch.object(Client, "list_repos")
@patch.object(Client, "check_server", return_value=True)
def test_info_when_daemon_running_with_repos(
    mock_check: MagicMock,
    mock_list: MagicMock,
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _daemon_mode(monkeypatch)
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
@patch.object(Client, "check_server", return_value=True)
def test_info_when_daemon_running_no_repos(
    mock_check: MagicMock,
    mock_list: MagicMock,
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _daemon_mode(monkeypatch)
    mock_list.return_value = ListReposResponse(repos=[], total=0, page=1, page_size=50)
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "Daemon: running" in result.output
    assert "none" in result.output.lower()


@patch.object(Client, "list_repos", side_effect=ApiError("unreachable"))
@patch.object(Client, "check_server", return_value=True)
def test_info_when_daemon_running_but_api_fails(
    mock_check: MagicMock,
    mock_list: MagicMock,
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _daemon_mode(monkeypatch)
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "Daemon: running" in result.output
    assert "unavailable" in result.output.lower()
