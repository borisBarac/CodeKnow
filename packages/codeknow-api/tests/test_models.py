"""Tests for codeknow_api.models — Pydantic request/response models."""

from __future__ import annotations

import pytest
from codeknow_api.models import BuildRequest, BuildResponse, DeleteRepoRequest
from pydantic import ValidationError


class TestBuildRequest:
    def test_valid_url_with_git_suffix(self) -> None:
        req = BuildRequest(github_ssh_url="git@github.com:owner/repo.git")
        assert req.github_ssh_url == "git@github.com:owner/repo.git"

    def test_valid_url_without_git_suffix(self) -> None:
        req = BuildRequest(github_ssh_url="git@github.com:owner/repo")
        assert req.github_ssh_url == "git@github.com:owner/repo"

    def test_valid_url_with_dots_and_hyphens(self) -> None:
        req = BuildRequest(github_ssh_url="git@github.com:my-org/my.repo.git")
        assert req.github_ssh_url == "git@github.com:my-org/my.repo.git"

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/owner/repo.git",
            "git@gitlab.com:owner/repo.git",
            "git@github.com:bad",
            "",
            "git@github.com:/repo.git",
            "git@github.com:owner/",
            " not-a-url",
        ],
    )
    def test_invalid_url_raises(self, url: str) -> None:
        with pytest.raises(ValidationError, match="Invalid GitHub SSH URL"):
            BuildRequest(github_ssh_url=url)

    def test_missing_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            BuildRequest()


class TestBuildResponse:
    def test_all_fields(self) -> None:
        resp = BuildResponse(
            status="done",
            slug="owner-repo",
            commit_hash="a" * 40,
            node_count=42,
            edge_count=99,
            community_count=7,
        )
        d = resp.model_dump()
        assert d["status"] == "done"
        assert d["slug"] == "owner-repo"
        assert d["commit_hash"] == "a" * 40
        assert d["node_count"] == 42
        assert d["edge_count"] == 99
        assert d["community_count"] == 7

    def test_optional_fields_default_none(self) -> None:
        resp = BuildResponse(status="pending")
        assert resp.slug is None
        assert resp.commit_hash is None
        assert resp.node_count is None
        assert resp.edge_count is None
        assert resp.community_count is None

    def test_model_dump_round_trip(self) -> None:
        resp = BuildResponse(status="done", slug="x", commit_hash="abc", node_count=1)
        d = resp.model_dump()
        resp2 = BuildResponse(**d)
        assert resp2 == resp


class TestDeleteRepoRequest:
    def test_valid(self) -> None:
        req = DeleteRepoRequest(url="git@github.com:owner/repo.git")
        assert req.url == "git@github.com:owner/repo.git"

    def test_accepts_any_string(self) -> None:
        req = DeleteRepoRequest(url="anything")
        assert req.url == "anything"

    def test_missing_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            DeleteRepoRequest()
