"""Tests for POST /v1/build and GET /v1/build/{slug} endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import codeknow_api.app as app_module
import pytest
from codeknow_api.app import _evict_completed_jobs, create_app
from codeknow_api.models import BuildJob
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def graph_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    gdir = tmp_path / "graph"
    gdir.mkdir()
    monkeypatch.setattr(app_module, "GRAPH_DIR", gdir)
    return gdir


@pytest.fixture
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tdir = tmp_path / "temp"
    tdir.mkdir()
    monkeypatch.setattr(app_module, "TEMP_DIR", tdir)
    return tdir


@pytest.fixture
def client(graph_dir: Path, temp_dir: Path) -> TestClient:
    app = create_app()
    return TestClient(app)


class TestBuildSubmit:
    def test_submit_returns_202_with_correct_shape(
        self, client: TestClient, graph_dir: Path
    ) -> None:
        import asyncio
        from unittest.mock import patch

        fake_lock = asyncio.Lock()
        client.app.state.build_locks["owner-repo"] = fake_lock

        async def fake_create_task(coro: object) -> None:
            pass

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

        del client.app.state.build_jobs["owner-repo"]
        del client.app.state.build_locks["owner-repo"]


class TestBuildStatus:
    def test_not_found_returns_404(self, client: TestClient) -> None:
        resp = client.get("/v1/build/nonexistent-slug")
        assert resp.status_code == 404
        assert "nonexistent-slug" in resp.json()["detail"]

    def test_queued_returns_202(self, client: TestClient) -> None:
        client.app.state.build_jobs["test-repo"] = BuildJob(slug="test-repo")
        try:
            resp = client.get("/v1/build/test-repo")
            assert resp.status_code == 202
            body = resp.json()
            assert body["status"] == "queued"
            assert body["progress"] == 0
            assert resp.headers.get("retry-after") == "3"
        finally:
            del client.app.state.build_jobs["test-repo"]

    def test_running_returns_202(self, client: TestClient) -> None:
        client.app.state.build_jobs["test-repo"] = BuildJob(
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
            del client.app.state.build_jobs["test-repo"]

    def test_succeeded_returns_200(self, client: TestClient) -> None:
        client.app.state.build_jobs["test-repo"] = BuildJob(
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
            del client.app.state.build_jobs["test-repo"]

    def test_failed_returns_200(self, client: TestClient) -> None:
        client.app.state.build_jobs["test-repo"] = BuildJob(
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
            del client.app.state.build_jobs["test-repo"]


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
        _evict_completed_jobs(jobs)
        assert "old-done" not in jobs
        assert "old-failed" not in jobs
        assert "recent-done" in jobs
        assert "still-running" in jobs

    def test_jobs_without_completed_at_are_kept(self) -> None:
        jobs: dict[str, BuildJob] = {
            "queued": BuildJob(slug="queued"),
            "running": BuildJob(slug="running", status="running", progress=10),
        }
        _evict_completed_jobs(jobs)
        assert len(jobs) == 2

    def test_eviction_on_build_status_endpoint(
        self, client: TestClient
    ) -> None:
        old = datetime.now(tz=timezone.utc) - timedelta(days=7)
        client.app.state.build_jobs["stale-repo"] = BuildJob(
            slug="stale-repo",
            status="succeeded",
            progress=100,
            completed_at=old,
        )
        resp = client.get("/v1/build/stale-repo")
        assert resp.status_code == 404
        assert "stale-repo" not in client.app.state.build_jobs
