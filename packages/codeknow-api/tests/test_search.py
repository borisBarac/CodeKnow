"""Integration tests for POST /v1/search endpoint."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import codeknow_api.app as app_module
import pytest
from codeknow_api.app import create_app
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def graph_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    gdir = tmp_path / "graph"
    gdir.mkdir()
    monkeypatch.setattr(app_module, "GRAPH_DIR", gdir)
    monkeypatch.setattr(app_module._facade, "graph_dir", gdir)
    return gdir


@pytest.fixture
def client(graph_dir: Path) -> TestClient:
    app = create_app()
    return TestClient(app)


def _seed_repo(graph_dir: Path, slug: str) -> None:
    repo_dir = graph_dir / slug
    repo_dir.mkdir()
    (repo_dir / "metadata.json").write_text(json.dumps({"slug": slug}))


class TestSearchSlugValidation:
    def test_unknown_slug_returns_400(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        resp = client.post(
            "/v1/search",
            json={"query": "test", "repos": ["nonexistent-repo"]},
        )
        assert resp.status_code == 400
        assert "nonexistent-repo" in resp.json()["detail"]

    def test_one_known_one_unknown_returns_400(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        _seed_repo(graph_dir, "known-repo")
        resp = client.post(
            "/v1/search",
            json={"query": "test", "repos": ["known-repo", "ghost-repo"]},
        )
        assert resp.status_code == 400
        assert "ghost-repo" in resp.json()["detail"]
        assert "known-repo" not in resp.json()["detail"]

    def test_all_known_slugs_accepted(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        _seed_repo(graph_dir, "repo-a")
        _seed_repo(graph_dir, "repo-b")
        resp = client.post(
            "/v1/search",
            json={"query": "test", "repos": ["repo-a", "repo-b"]},
        )
        assert resp.status_code == 200


class TestSearchQueryValidation:
    def test_empty_query_returns_422(self, client: TestClient) -> None:
        resp = client.post("/v1/search", json={"query": ""})
        assert resp.status_code == 422

    def test_whitespace_query_returns_422(self, client: TestClient) -> None:
        resp = client.post("/v1/search", json={"query": "   "})
        assert resp.status_code == 422

    def test_top_k_below_minimum_returns_422(self, client: TestClient) -> None:
        resp = client.post("/v1/search", json={"query": "test", "top_k": 0})
        assert resp.status_code == 422

    def test_top_k_above_maximum_returns_422(self, client: TestClient) -> None:
        resp = client.post("/v1/search", json={"query": "test", "top_k": 101})
        assert resp.status_code == 422


class TestSearchBuildCollision:
    def test_building_slug_returns_409(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        _seed_repo(graph_dir, "building-repo")
        client.app.state.build_status["building-repo"] = {
            "status": "building",
            "progress": 42,
        }
        try:
            resp = client.post(
                "/v1/search",
                json={"query": "test", "repos": ["building-repo"]},
            )
            assert resp.status_code == 409
            assert "building-repo" in resp.json()["detail"]
        finally:
            del client.app.state.build_status["building-repo"]

    def test_done_slug_is_searchable(self, client: TestClient, graph_dir: Path) -> None:
        _seed_repo(graph_dir, "done-repo")
        client.app.state.build_status["done-repo"] = {
            "status": "done",
            "progress": 100,
        }
        try:
            resp = client.post(
                "/v1/search",
                json={"query": "test", "repos": ["done-repo"]},
            )
            assert resp.status_code == 200
        finally:
            del client.app.state.build_status["done-repo"]

    def test_mix_building_and_done_reports_only_building(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        _seed_repo(graph_dir, "done-repo")
        _seed_repo(graph_dir, "building-repo")
        client.app.state.build_status["done-repo"] = {
            "status": "done",
            "progress": 100,
        }
        client.app.state.build_status["building-repo"] = {
            "status": "building",
            "progress": 50,
        }
        try:
            resp = client.post(
                "/v1/search",
                json={"query": "test", "repos": ["done-repo", "building-repo"]},
            )
            assert resp.status_code == 409
            assert "building-repo" in resp.json()["detail"]
            assert "done-repo" not in resp.json()["detail"]
        finally:
            del client.app.state.build_status["done-repo"]
            del client.app.state.build_status["building-repo"]


class TestSearchResponseShape:
    def test_success_returns_correct_shape(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        _seed_repo(graph_dir, "test-repo")
        resp = client.post(
            "/v1/search",
            json={"query": "test", "repos": ["test-repo"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "query" in body
        assert "vector_hits" in body
        assert "graph_expanded" in body
        assert "results" in body
        assert isinstance(body["results"], list)

    def test_no_repos_searches_all(self, client: TestClient, graph_dir: Path) -> None:
        _seed_repo(graph_dir, "repo-a")
        resp = client.post(
            "/v1/search",
            json={"query": "test"},
        )
        assert resp.status_code == 200

    def test_empty_repos_list_allowed(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        _seed_repo(graph_dir, "repo-a")
        resp = client.post(
            "/v1/search",
            json={"query": "test", "repos": []},
        )
        assert resp.status_code == 200
