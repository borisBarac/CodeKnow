"""Tests for POST /v1/build and GET /v1/build/{slug} endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
from codeknow_api.app import ApiConfig, _evict_completed_jobs, create_app
from codeknow_api.models import BuildJob
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def graph_dir(tmp_path: Path) -> Path:
    gdir = tmp_path / "graph"
    gdir.mkdir()
    return gdir


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    tdir = tmp_path / "temp"
    tdir.mkdir()
    return tdir


@pytest.fixture
def config(graph_dir: Path, temp_dir: Path) -> ApiConfig:
    return ApiConfig(
        graph_dir=graph_dir,
        temp_dir=temp_dir,
        job_ttl=timedelta(hours=1),
        cache_ttl=300,
    )


@pytest.fixture
def client(config: ApiConfig) -> TestClient:
    app = create_app(config=config)
    return TestClient(app)


class TestBuildSubmit:
    def test_submit_returns_202_with_correct_shape(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        from unittest.mock import Mock, patch

        def fake_create_task(coro):
            coro.close()
            return Mock()

        with patch("asyncio.create_task", side_effect=fake_create_task):
            resp = client.post(
                "/v1/build",
                json={"github_ssh_url": "git@github.com:owner/repo.git"},
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "queued"
        assert body["slug"] == "owner-repo"
        assert body["status_url"] == "/v1/build/owner-repo"
        assert body["progress"] == 0
        assert "Location" in resp.headers
        assert resp.headers["Location"] == "/v1/build/owner-repo"
        assert "Retry-After" in resp.headers

        del client.app.state.codeknow.build_jobs["owner-repo"]
        client.app.state.codeknow.builds_in_flight.discard("owner-repo")

    def test_first_five_distinct_repositories_are_accepted(
        self, client: TestClient
    ) -> None:
        from unittest.mock import Mock, patch

        def fake_create_task(coro):
            coro.close()
            return Mock()

        with patch("asyncio.create_task", side_effect=fake_create_task):
            responses = [
                client.post(
                    "/v1/build",
                    json={"github_ssh_url": f"git@github.com:owner/repo-{i}.git"},
                )
                for i in range(5)
            ]

        assert [response.status_code for response in responses] == [202] * 5

    def test_sixth_distinct_repository_is_rejected(self, client: TestClient) -> None:
        from unittest.mock import Mock, patch

        state = client.app.state.codeknow
        state.builds_in_flight.update(f"owner-repo-{i}" for i in range(5))

        with patch("asyncio.create_task", return_value=Mock()):
            response = client.post(
                "/v1/build",
                json={"github_ssh_url": "git@github.com:owner/repo-5.git"},
            )

        assert response.status_code == 409
        assert response.json()["detail"] == "Repository limit of 5 reached"

    def test_existing_build_conflict_message_is_unchanged(
        self, client: TestClient
    ) -> None:
        client.app.state.codeknow.builds_in_flight.add("owner-repo")

        response = client.post(
            "/v1/build",
            json={"github_ssh_url": "git@github.com:owner/repo.git"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Build already in progress for this repo"

    def test_rebuild_at_repository_limit_is_accepted(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        from unittest.mock import Mock, patch

        for i in range(5):
            repo_dir = graph_dir / f"owner-repo-{i}"
            repo_dir.mkdir()
            (repo_dir / "metadata.json").write_text(
                '{"github_ssh_url":"git@github.com:owner/repo.git",'
                f'"slug":"owner-repo-{i}","commit_hash":"abc",'
                '"built_at":"2026-01-01T00:00:00Z","node_count":1,'
                '"edge_count":1,"community_count":1}',
                encoding="utf-8",
            )

        def fake_create_task(coro):
            coro.close()
            return Mock()

        with patch("asyncio.create_task", side_effect=fake_create_task):
            response = client.post(
                "/v1/build",
                json={"github_ssh_url": "git@github.com:owner/repo-0.git"},
            )

        assert response.status_code == 202

    def test_in_flight_new_repository_consumes_slot(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        for i in range(4):
            repo_dir = graph_dir / f"owner-indexed-{i}"
            repo_dir.mkdir()
            (repo_dir / "metadata.json").write_text(
                '{"github_ssh_url":"git@github.com:owner/repo.git",'
                f'"slug":"owner-indexed-{i}","commit_hash":"abc",'
                '"built_at":"2026-01-01T00:00:00Z","node_count":1,'
                '"edge_count":1,"community_count":1}',
                encoding="utf-8",
            )
        client.app.state.codeknow.builds_in_flight.add("owner-building")

        response = client.post(
            "/v1/build",
            json={"github_ssh_url": "git@github.com:owner/new.git"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Repository limit of 5 reached"


class TestBuildStatus:
    def test_not_found_returns_404(self, client: TestClient) -> None:
        resp = client.get("/v1/build/nonexistent-slug")
        assert resp.status_code == 404
        assert "nonexistent-slug" in resp.json()["detail"]

    def test_queued_returns_202(self, client: TestClient) -> None:
        client.app.state.codeknow.build_jobs["test-repo"] = BuildJob(slug="test-repo")
        try:
            resp = client.get("/v1/build/test-repo")
            assert resp.status_code == 202
            body = resp.json()
            assert body["status"] == "queued"
            assert body["progress"] == 0
            assert resp.headers.get("retry-after") == "3"
        finally:
            del client.app.state.codeknow.build_jobs["test-repo"]

    def test_running_returns_202(self, client: TestClient) -> None:
        client.app.state.codeknow.build_jobs["test-repo"] = BuildJob(
            slug="test-repo",
            status="running",
            progress=42,
            stage="build",
            message="Building graph...",
        )
        try:
            resp = client.get("/v1/build/test-repo")
            assert resp.status_code == 202
            body = resp.json()
            assert body["status"] == "running"
            assert body["progress"] == 42
            assert body["stage"] == "build"
            assert resp.headers.get("retry-after") == "3"
        finally:
            del client.app.state.codeknow.build_jobs["test-repo"]

    def test_succeeded_returns_200(self, client: TestClient) -> None:
        client.app.state.codeknow.build_jobs["test-repo"] = BuildJob(
            slug="test-repo",
            status="succeeded",
            progress=100,
            commit_hash="abc123",
            node_count=10,
            edge_count=20,
            community_count=2,
        )
        try:
            resp = client.get("/v1/build/test-repo")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "succeeded"
            assert body["progress"] == 100
            assert body["commit_hash"] == "abc123"
            assert body["node_count"] == 10
        finally:
            del client.app.state.codeknow.build_jobs["test-repo"]

    def test_failed_returns_200(self, client: TestClient) -> None:
        client.app.state.codeknow.build_jobs["test-repo"] = BuildJob(
            slug="test-repo",
            status="failed",
            progress=28,
            stage="detect",
            message="Discovering files...",
            error="Something went wrong",
        )
        try:
            resp = client.get("/v1/build/test-repo")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "failed"
            assert body["error"] == "Something went wrong"
        finally:
            del client.app.state.codeknow.build_jobs["test-repo"]


def test_delete_rejects_repo_with_build_in_flight(
    client: TestClient,
    graph_dir: Path,
) -> None:
    slug_dir = graph_dir / "owner-repo"
    slug_dir.mkdir()
    (slug_dir / "metadata.json").write_text("{}", encoding="utf-8")
    client.app.state.codeknow.builds_in_flight.add("owner-repo")
    try:
        response = client.request(
            "DELETE",
            "/v1/repos",
            json={"slug": "owner-repo"},
        )
    finally:
        client.app.state.codeknow.builds_in_flight.discard("owner-repo")

    assert response.status_code == 409


class TestJobEviction:
    def test_expired_terminal_jobs_are_evicted(self) -> None:
        old = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        jobs: dict[str, BuildJob] = {
            "old-done": BuildJob(
                slug="old-done",
                status="succeeded",
                progress=100,
                completed_at=old,
            ),
            "old-failed": BuildJob(
                slug="old-failed",
                status="failed",
                error="boom",
                completed_at=old,
            ),
            "recent-done": BuildJob(
                slug="recent-done",
                status="succeeded",
                progress=100,
                completed_at=datetime.now(tz=timezone.utc),
            ),
            "still-running": BuildJob(
                slug="still-running",
                status="running",
                progress=50,
            ),
        }
        _evict_completed_jobs(jobs, timedelta(hours=1))
        assert "old-done" not in jobs
        assert "old-failed" not in jobs
        assert "recent-done" in jobs
        assert "still-running" in jobs

    def test_jobs_without_completed_at_are_kept(self) -> None:
        jobs: dict[str, BuildJob] = {
            "queued": BuildJob(slug="queued"),
            "running": BuildJob(slug="running", status="running", progress=10),
        }
        _evict_completed_jobs(jobs, timedelta(hours=1))
        assert len(jobs) == 2

    def test_eviction_on_build_status_endpoint(self, client: TestClient) -> None:
        old = datetime.now(tz=timezone.utc) - timedelta(days=7)
        client.app.state.codeknow.build_jobs["stale-repo"] = BuildJob(
            slug="stale-repo",
            status="succeeded",
            progress=100,
            completed_at=old,
        )
        resp = client.get("/v1/build/stale-repo")
        assert resp.status_code == 404
        assert "stale-repo" not in client.app.state.codeknow.build_jobs
