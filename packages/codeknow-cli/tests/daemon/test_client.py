from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from code_know_api_client import errors as api_errors
from code_know_api_client.models import (
    delete_repo_v1_repos_delete_response_delete_repo_v1_repos_delete as _del_resp,
)
from code_know_api_client.models.http_validation_error import HTTPValidationError
from code_know_api_client.models.search_request import SearchRequest
from code_know_api_client.models.search_response import SearchResponse
from code_know_api_client.models.validation_error import (
    ValidationError as ApiValidationError,
)
from code_know_api_client.types import Response as ApiResponse
from codeknow_cli.client import (
    BuildStatusResult,
    Client,
    DaemonStartResult,
    DeleteResult,
    SearchResult,
)
from codeknow_cli.exceptions import (
    ApiError,
    DaemonNotRunningError,
    RepoConflictError,
    RepoNotFoundError,
    ValidationError,
)

from .conftest import _free_port, _started_pids

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Client, None, None]:
    monkeypatch.setenv("FAKE_SERVER", "1")
    port = _free_port()
    pid_file = str(tmp_path / "test-daemon.pid")
    c = Client(host="127.0.0.1", port=port, pid_file=pid_file)
    yield c
    with contextlib.suppress(DaemonNotRunningError, Exception):
        c.stop_daemon(timeout=2)


def test_start_daemon_process_is_alive(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    assert isinstance(result, DaemonStartResult)
    _started_pids.add(result.pid)
    assert client.check_daemon()


def test_stop_daemon_clears_running_state(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result.pid)
    client.stop_daemon(timeout=5)
    assert not client.check_daemon()


def test_check_daemon_false_when_not_running(client: Client) -> None:
    assert not client.check_daemon()


def test_client_uses_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEKNOW_HOST", "myhost")
    monkeypatch.setenv("CODEKNOW_API_PORT", "4321")
    c = Client()
    assert c.host == "myhost"
    assert c.port == 4321
    assert c.base_url == "http://myhost:4321"


def test_remove_from_index_success(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result.pid)
    resp = client.remove_from_index("stub-repo")
    assert isinstance(resp, DeleteResult)
    assert resp.status == "deleted"
    assert resp.slug == "stub-repo"
    assert resp.chunks_deleted == 0


def test_remove_from_index_slug_not_found(client: Client) -> None:
    result = client.start_daemon(timeout=5)
    _started_pids.add(result.pid)
    with pytest.raises(
        RepoNotFoundError, match="Repo with slug 'nonexistent' not found"
    ):
        client.remove_from_index("nonexistent")


def _make_delete_response(data: dict) -> ApiResponse:
    parsed = _del_resp.DeleteRepoV1ReposDeleteResponseDeleteRepoV1ReposDelete()
    parsed.additional_properties = data
    return ApiResponse(status_code=200, content=b"", headers={}, parsed=parsed)


@patch("codeknow_cli.client.delete_repo_v1_repos_delete")
def test_remove_sends_slug_in_body(
    mock_delete: MagicMock,
) -> None:
    c = Client(
        host="127.0.0.1",
        port=19999,
        pid_file="/var/tmp/test-remove.pid",  # noqa: S108
    )
    mock_delete.sync_detailed.return_value = _make_delete_response(
        {"status": "deleted", "slug": "my-repo", "chunks_deleted": 5},
    )

    result = c.remove_from_index("my-repo")

    assert isinstance(result, DeleteResult)
    assert result.status == "deleted"
    assert result.chunks_deleted == 5
    mock_delete.sync_detailed.assert_called_once()
    call_body = mock_delete.sync_detailed.call_args[1]["body"]
    assert call_body.slug == "my-repo"


@patch("codeknow_cli.client.delete_repo_v1_repos_delete")
def test_remove_raises_when_slug_not_found(mock_delete: MagicMock) -> None:
    c = Client(
        host="127.0.0.1",
        port=19999,
        pid_file="/var/tmp/test-remove-notfound.pid",  # noqa: S108
    )
    mock_delete.sync_detailed.side_effect = api_errors.UnexpectedStatus(
        404, b"not found"
    )

    with pytest.raises(RepoNotFoundError, match="Repo with slug 'missing' not found"):
        c.remove_from_index("missing")


# --- add_to_index tests ---


def _unit_client() -> Client:
    return Client(
        host="127.0.0.1",
        port=19998,
        pid_file="/var/tmp/test-unit-client.pid",  # noqa: S108
    )


def _mock_post_response(status_code: int, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = json.dumps(json_data or {}).encode()
    resp.text = json.dumps(json_data or {})
    resp.json.return_value = json_data or {}
    resp.headers = {}
    return resp


@patch("codeknow_cli.client.httpx.get")
@patch("codeknow_cli.client.httpx.post")
def test_add_submit_and_poll_success(
    mock_post: MagicMock,
    mock_get: MagicMock,
) -> None:
    c = _unit_client()
    mock_post.return_value = _mock_post_response(
        202,
        {
            "status": "queued",
            "slug": "org-repo",
            "status_url": "/v1/build/org-repo",
            "progress": 0,
        },
    )
    mock_get.return_value = _mock_post_response(
        200,
        {
            "status": "succeeded",
            "slug": "org-repo",
            "progress": 100,
            "commit_hash": "abc",
            "node_count": 10,
            "edge_count": 20,
            "community_count": 3,
        },
    )

    with patch("codeknow_cli.client.time.sleep"):
        result = c.add_to_index("git@github.com:org/repo.git")

    assert isinstance(result, BuildStatusResult)
    assert result.status == "succeeded"
    assert result.slug == "org-repo"
    assert result.node_count == 10


@patch("codeknow_cli.client.httpx.post")
def test_add_raises_on_409(mock_post: MagicMock) -> None:
    c = _unit_client()
    mock_post.return_value = _mock_post_response(409, {"detail": "conflict"})
    with pytest.raises(RepoConflictError, match="already being built"):
        c.add_to_index("git@github.com:org/repo.git")


@patch("codeknow_cli.client.httpx.post")
def test_add_raises_on_422(mock_post: MagicMock) -> None:
    c = _unit_client()
    mock_post.return_value = _mock_post_response(422, {"detail": [{"msg": "bad url"}]})
    with pytest.raises(ValidationError, match="bad url"):
        c.add_to_index("not-a-url")


@patch("codeknow_cli.client.httpx.post")
def test_add_raises_on_unexpected_status(mock_post: MagicMock) -> None:
    c = _unit_client()
    mock_post.return_value = _mock_post_response(500, {"detail": "error"})
    with pytest.raises(ApiError, match="Unexpected API status 500"):
        c.add_to_index("git@github.com:org/repo.git")


@patch("codeknow_cli.client.httpx.get")
@patch("codeknow_cli.client.httpx.post")
def test_add_raises_on_build_failed(
    mock_post: MagicMock,
    mock_get: MagicMock,
) -> None:
    c = _unit_client()
    mock_post.return_value = _mock_post_response(
        202,
        {
            "status": "queued",
            "slug": "org-repo",
            "status_url": "/v1/build/org-repo",
            "progress": 0,
        },
    )
    mock_get.return_value = _mock_post_response(
        200,
        {"status": "failed", "slug": "org-repo", "progress": 28, "error": "boom"},
    )

    with (
        pytest.raises(ApiError, match="Build failed: boom"),
        patch("codeknow_cli.client.time.sleep"),
    ):
        c.add_to_index("git@github.com:org/repo.git")


@patch("codeknow_cli.client.httpx.post")
def test_add_raises_on_transport_error(mock_post: MagicMock) -> None:
    import httpx

    c = _unit_client()
    mock_post.side_effect = httpx.TransportError("connection refused")
    with pytest.raises(DaemonNotRunningError):
        c.add_to_index("git@github.com:org/repo.git")


@patch("codeknow_cli.client.httpx.get")
@patch("codeknow_cli.client.httpx.post")
def test_add_progress_callback_invoked(
    mock_post: MagicMock,
    mock_get: MagicMock,
) -> None:
    c = _unit_client()
    mock_post.return_value = _mock_post_response(
        202,
        {
            "status": "queued",
            "slug": "org-repo",
            "status_url": "/v1/build/org-repo",
            "progress": 0,
        },
    )
    progress_responses = [
        _mock_post_response(
            202,
            {
                "status": "running",
                "slug": "org-repo",
                "progress": 25,
                "stage": "cloning",
                "message": "Downloading repository",
            },
        ),
        _mock_post_response(
            202,
            {
                "status": "running",
                "slug": "org-repo",
                "progress": 60,
                "stage": "building",
                "message": "Building graph",
            },
        ),
        _mock_post_response(
            200,
            {
                "status": "succeeded",
                "slug": "org-repo",
                "progress": 100,
                "commit_hash": "def456",
                "node_count": 42,
                "edge_count": 99,
                "community_count": 7,
            },
        ),
    ]
    mock_get.side_effect = progress_responses

    calls: list[tuple[str, int, str]] = []

    def capture(stage: str, percent: int, message: str) -> None:
        calls.append((stage, percent, message))

    with patch("codeknow_cli.client.time.sleep"):
        result = c.add_to_index(
            "git@github.com:org/repo.git", progress_callback=capture
        )

    assert len(calls) == 2
    assert calls[0] == ("cloning", 25, "Downloading repository")
    assert calls[1] == ("building", 60, "Building graph")
    assert result.status == "succeeded"
    assert result.commit_hash == "def456"
    assert result.community_count == 7


@patch("codeknow_cli.client.httpx.get")
@patch("codeknow_cli.client.httpx.post")
def test_add_poll_raises_on_500(
    mock_post: MagicMock,
    mock_get: MagicMock,
) -> None:
    c = _unit_client()
    mock_post.return_value = _mock_post_response(
        202,
        {
            "status": "queued",
            "slug": "org-repo",
            "status_url": "/v1/build/org-repo",
            "progress": 0,
        },
    )
    mock_get.return_value = _mock_post_response(500, {"detail": "internal error"})

    with (
        pytest.raises(ApiError, match="Unexpected API status 500"),
        patch("codeknow_cli.client.time.sleep"),
    ):
        c.add_to_index("git@github.com:org/repo.git")


# --- remove_from_index error-path tests ---


@patch("codeknow_cli.client.delete_repo_v1_repos_delete")
def test_remove_raises_on_404_from_delete(
    mock_delete: MagicMock,
) -> None:
    c = _unit_client()
    mock_delete.sync_detailed.side_effect = api_errors.UnexpectedStatus(
        404, b"not found"
    )
    with pytest.raises(RepoNotFoundError, match="Repo with slug 'x' not found"):
        c.remove_from_index("x")


# --- search tests ---


def _make_search_response(data: dict) -> ApiResponse:
    parsed = SearchResponse(
        query=data.get("query", ""),
        vector_hits=data.get("vector_hits", 0),
        graph_expanded=data.get("graph_expanded", 0),
        results=[],
    )
    return ApiResponse(status_code=200, content=b"", headers={}, parsed=parsed)


@patch("codeknow_cli.client.search_v1_search_post")
def test_search_basic_returns_search_result(mock_search: MagicMock) -> None:
    c = _unit_client()
    mock_search.sync_detailed.return_value = _make_search_response(
        {"query": "test", "vector_hits": 5, "graph_expanded": 3, "results": []}
    )
    result = c.search("test")
    assert isinstance(result, SearchResult)
    assert result.query == "test"
    assert result.vector_hits == 5
    assert result.graph_expanded == 3


@patch("codeknow_cli.client.search_v1_search_post")
def test_search_with_slugs_sends_repos_in_body(mock_search: MagicMock) -> None:
    c = _unit_client()
    mock_search.sync_detailed.return_value = _make_search_response(
        {"query": "test", "vector_hits": 0, "graph_expanded": 0, "results": []}
    )
    c.search("test", slugs=["repo-a", "repo-b"])
    call_body = mock_search.sync_detailed.call_args[1]["body"]
    assert isinstance(call_body, SearchRequest)
    assert call_body.repos == ["repo-a", "repo-b"]


@patch("codeknow_cli.client.search_v1_search_post")
def test_search_without_slugs_omits_repos(mock_search: MagicMock) -> None:
    c = _unit_client()
    mock_search.sync_detailed.return_value = _make_search_response(
        {"query": "test", "vector_hits": 0, "graph_expanded": 0, "results": []}
    )
    c.search("test")
    call_body = mock_search.sync_detailed.call_args[1]["body"]
    assert isinstance(call_body, SearchRequest)
    assert call_body.repos is None or isinstance(call_body.repos, object)


@patch("codeknow_cli.client.search_v1_search_post")
def test_search_400_unknown_slugs(mock_search: MagicMock) -> None:
    c = _unit_client()
    mock_search.sync_detailed.side_effect = api_errors.UnexpectedStatus(
        400, b'{"detail":"Unknown slugs: [\\"ghost\\"]"}'
    )
    with pytest.raises(RepoNotFoundError, match="Unknown slugs"):
        c.search("test", slugs=["ghost"])


@patch("codeknow_cli.client.search_v1_search_post")
def test_search_409_rebuilding(mock_search: MagicMock) -> None:
    c = _unit_client()
    mock_search.sync_detailed.side_effect = api_errors.UnexpectedStatus(
        409, b'{"detail":"Repos being rebuilt: [\\"repo\\"]"}'
    )
    with pytest.raises(RepoConflictError, match="Repos being rebuilt"):
        c.search("test")


@patch("codeknow_cli.client.search_v1_search_post")
def test_search_422_validation(mock_search: MagicMock) -> None:
    c = _unit_client()
    parsed = HTTPValidationError(
        detail=[ApiValidationError(loc=["body"], msg="bad input", type_="value_error")]
    )
    mock_search.sync_detailed.return_value = ApiResponse(
        status_code=422, content=b"", headers={}, parsed=parsed
    )
    with pytest.raises(ValidationError, match=r"Validation error.*bad input"):
        c.search("test")
