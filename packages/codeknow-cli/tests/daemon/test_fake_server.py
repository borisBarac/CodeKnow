from __future__ import annotations

import threading
from http.server import HTTPServer

import httpx
import pytest
from codeknow_cli.daemon.fake_server import StubAPIHandler


@pytest.fixture
def live_server() -> int:
    server = HTTPServer(("127.0.0.1", 0), StubAPIHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


@pytest.fixture
def base_url(live_server: int) -> str:
    return f"http://127.0.0.1:{live_server}"


def test_get_repos_returns_200(base_url: str) -> None:
    resp = httpx.get(base_url + "/v1/repos")
    assert resp.status_code == 200


def test_get_repos_returns_stub_repo(base_url: str) -> None:
    resp = httpx.get(base_url + "/v1/repos")
    data = resp.json()
    assert len(data["repos"]) == 1
    assert data["repos"][0]["slug"] == "stub-slug"
    assert data["total"] == 1


def test_post_build_returns_202(base_url: str) -> None:
    resp = httpx.post(
        base_url + "/v1/build",
        json={"github_ssh_url": "git@github.com:owner/repo.git"},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "done"


def test_post_search_returns_200(base_url: str) -> None:
    resp = httpx.post(base_url + "/v1/search", json={"query": "test"})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_post_search_returns_full_response(base_url: str) -> None:
    resp = httpx.post(base_url + "/v1/search", json={"query": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "query" in data
    assert "vector_hits" in data
    assert "graph_expanded" in data
    assert "results" in data


def test_delete_repos_returns_200(base_url: str) -> None:
    resp = httpx.request(
        "DELETE",
        base_url + "/v1/repos",
        json={"url": "git@github.com:owner/repo.git"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


def test_get_unknown_returns_404(base_url: str) -> None:
    resp = httpx.get(base_url + "/unknown")
    assert resp.status_code == 404
