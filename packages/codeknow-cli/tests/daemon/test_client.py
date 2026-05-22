from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from code_know_api_client import errors as api_errors
from code_know_api_client.models import (
    delete_repo_v1_repos_delete_response_delete_repo_v1_repos_delete as _del_resp,
)
from code_know_api_client.models.build_response import BuildResponse
from code_know_api_client.models.http_validation_error import HTTPValidationError
from code_know_api_client.models.list_repos_response import ListReposResponse
from code_know_api_client.models.repo_metadata import RepoMetadata
from code_know_api_client.models.validation_error import ValidationError
from code_know_api_client.types import Response as ApiResponse
from codeknow_cli.client import Client, ClientError

from .conftest import _free_port, _started_pids

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def client(tmp_path: Path) -> Generator[Client, None, None]:
    port = _free_port()
    pid_file = str(tmp_path / "test-daemon.pid")
    c = Client(host="127.0.0.1", port=port, pid_file=pid_file)
    yield c
    with contextlib.suppress(TimeoutError, RuntimeError):
        c.stop_daemon(timeout=2)


def test_start_daemon_process_is_alive(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result["pid"])
    assert client.check_daemon()


def test_stop_daemon_clears_running_state(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result["pid"])
    client.stop_daemon(timeout=5)
    assert not client.check_daemon()


def test_check_daemon_false_when_not_running(client: Client) -> None:
    assert not client.check_daemon()


def test_client_uses_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEKNOW_HOST", "myhost")
    monkeypatch.setenv("CODEKNOW_PORT", "4321")
    c = Client()
    assert c.host == "myhost"
    assert c.port == 4321
    assert c.base_url == "http://myhost:4321"


def test_remove_from_index_success(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result["pid"])
    resp = client.remove_from_index("stub-slug")
    assert resp["status"] == "deleted"
    assert resp["slug"] == "stub-slug"
    assert resp["chunks_deleted"] == 0


def test_remove_from_index_slug_not_found(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result["pid"])
    with pytest.raises(ClientError, match="Repo with slug 'nonexistent' not found"):
        client.remove_from_index("nonexistent")


def _make_list_response(repos: list[RepoMetadata]) -> ApiResponse:
    parsed = ListReposResponse(
        repos=repos,
        total=len(repos),
        page=1,
        page_size=50,
    )
    return ApiResponse(status_code=200, content=b"", headers={}, parsed=parsed)


def _make_repo(slug: str, ssh_url: str) -> RepoMetadata:
    return RepoMetadata(
        github_ssh_url=ssh_url,
        slug=slug,
        commit_hash="abc123",
        built_at="2025-01-01T00:00:00Z",
        node_count=10,
        edge_count=20,
        community_count=2,
    )


def _make_delete_response(data: dict) -> ApiResponse:
    parsed = _del_resp.DeleteRepoV1ReposDeleteResponseDeleteRepoV1ReposDelete()
    parsed.additional_properties = data
    return ApiResponse(status_code=200, content=b"", headers={}, parsed=parsed)


@patch("codeknow_cli.client.delete_repo_v1_repos_delete")
@patch("codeknow_cli.client.list_repos_v1_repos_get")
def test_remove_resolves_slug_to_ssh_url(
    mock_list: MagicMock,
    mock_delete: MagicMock,
) -> None:
    c = Client(
        host="127.0.0.1",
        port=19999,
        pid_file="/var/tmp/test-remove.pid",  # noqa: S108
    )
    repo = _make_repo("my-repo", "git@github.com:org/my-repo.git")
    mock_list.sync_detailed.return_value = _make_list_response([repo])
    mock_delete.sync_detailed.return_value = _make_delete_response(
        {"status": "deleted", "slug": "my-repo", "chunks_deleted": 5},
    )

    result = c.remove_from_index("my-repo")

    assert result["status"] == "deleted"
    assert result["chunks_deleted"] == 5
    mock_delete.sync_detailed.assert_called_once()
    call_body = mock_delete.sync_detailed.call_args[1]["body"]
    assert call_body.url == "git@github.com:org/my-repo.git"


@patch("codeknow_cli.client.list_repos_v1_repos_get")
def test_remove_raises_when_slug_not_found(mock_list: MagicMock) -> None:
    c = Client(
        host="127.0.0.1",
        port=19999,
        pid_file="/var/tmp/test-remove-notfound.pid",  # noqa: S108
    )
    mock_list.sync_detailed.return_value = _make_list_response([])

    with pytest.raises(ClientError, match="Repo with slug 'missing' not found"):
        c.remove_from_index("missing")


# --- add_to_index error-path tests ---


def _make_build_response() -> ApiResponse:
    parsed = BuildResponse(status="done", slug="my-repo", node_count=1, edge_count=2)
    return ApiResponse(status_code=202, content=b"", headers={}, parsed=parsed)


@patch("codeknow_cli.client.build_v1_build_post")
def test_add_raises_on_409(mock_build: MagicMock) -> None:
    c = _unit_client()
    mock_build.sync_detailed.side_effect = api_errors.UnexpectedStatus(409, b"conflict")
    with pytest.raises(ClientError, match="already being built"):
        c.add_to_index("git@github.com:org/repo.git")


@patch("codeknow_cli.client.build_v1_build_post")
def test_add_raises_on_unexpected_status(mock_build: MagicMock) -> None:
    c = _unit_client()
    mock_build.sync_detailed.side_effect = api_errors.UnexpectedStatus(
        500, b"server error"
    )
    with pytest.raises(ClientError, match="Unexpected API status 500"):
        c.add_to_index("git@github.com:org/repo.git")


@patch("codeknow_cli.client.build_v1_build_post")
def test_add_raises_on_422_with_detail(mock_build: MagicMock) -> None:
    c = _unit_client()
    parsed = HTTPValidationError(
        detail=[ValidationError(loc=["body"], msg="bad url", type_="value_error")]
    )
    mock_build.sync_detailed.return_value = ApiResponse(
        status_code=422, content=b"", headers={}, parsed=parsed
    )
    with pytest.raises(ClientError, match=r"Validation error.*bad url"):
        c.add_to_index("not-a-url")


@patch("codeknow_cli.client.build_v1_build_post")
def test_add_raises_on_422_without_detail(mock_build: MagicMock) -> None:
    c = _unit_client()
    parsed = HTTPValidationError()
    mock_build.sync_detailed.return_value = ApiResponse(
        status_code=422, content=b"", headers={}, parsed=parsed
    )
    with pytest.raises(ClientError, match="Validation error: Invalid GitHub SSH URL"):
        c.add_to_index("not-a-url")


@patch("codeknow_cli.client.build_v1_build_post")
def test_add_raises_on_unexpected_response_code(mock_build: MagicMock) -> None:
    c = _unit_client()
    mock_build.sync_detailed.return_value = ApiResponse(
        status_code=503, content=b"", headers={}, parsed=None
    )
    with pytest.raises(ClientError, match="Unexpected response from API"):
        c.add_to_index("git@github.com:org/repo.git")


@patch("codeknow_cli.client.build_v1_build_post")
def test_add_returns_build_response_on_202(mock_build: MagicMock) -> None:
    c = _unit_client()
    mock_build.sync_detailed.return_value = _make_build_response()
    result = c.add_to_index("git@github.com:org/repo.git")
    assert result.status == "done"
    assert result.slug == "my-repo"


# --- remove_from_index error-path tests ---


@patch("codeknow_cli.client.delete_repo_v1_repos_delete")
@patch("codeknow_cli.client.list_repos_v1_repos_get")
def test_remove_raises_on_404_from_delete(
    mock_list: MagicMock,
    mock_delete: MagicMock,
) -> None:
    c = _unit_client()
    repo = _make_repo("x", "git@github.com:org/x.git")
    mock_list.sync_detailed.return_value = _make_list_response([repo])
    mock_delete.sync_detailed.side_effect = api_errors.UnexpectedStatus(
        404, b"not found"
    )
    with pytest.raises(ClientError, match="Repo not found"):
        c.remove_from_index("x")


def _unit_client() -> Client:
    return Client(
        host="127.0.0.1",
        port=19998,
        pid_file="/var/tmp/test-unit-client.pid",  # noqa: S108
    )
