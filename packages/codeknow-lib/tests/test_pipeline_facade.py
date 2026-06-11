"""Tests for PipelineFacade — API insulation layer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

from codeknow.schemas import ListReposResponse

if TYPE_CHECKING:
    from pathlib import Path

_DEFAULT_META: dict = {
    "github_ssh_url": "git@github.com:test/repo.git",
    "slug": "test-repo",
    "commit_hash": "abc123",
    "built_at": "2026-01-01T00:00:00Z",
    "node_count": 10,
    "edge_count": 20,
    "community_count": 3,
}


def _write_metadata(graph_dir: Path, slug: str, overrides: dict | None = None) -> None:
    meta = {**_DEFAULT_META, "slug": slug, **(overrides or {})}
    slug_dir = graph_dir / slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    (slug_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")


class TestResolveSlug:
    def test_ssh_url(self) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        assert (
            PipelineFacade.resolve_slug("git@github.com:owner/repo.git") == "owner-repo"
        )

    def test_https_url(self) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        assert (
            PipelineFacade.resolve_slug("https://github.com/owner/repo") == "owner-repo"
        )

    def test_non_github_passthrough(self) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        result = PipelineFacade.resolve_slug("my-custom-slug")
        assert result == "my-custom-slug"


class TestPipelineFacadeInit:
    def test_custom_dirs(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        facade = PipelineFacade(graph_dir=tmp_path / "g", temp_dir=tmp_path / "t")
        assert facade.graph_dir == tmp_path / "g"
        assert facade.temp_dir == tmp_path / "t"

    def test_slug_dir(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        facade = PipelineFacade(graph_dir=tmp_path)
        assert facade.slug_dir("my-repo") == tmp_path / "my-repo"

    def test_has_slug(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        facade = PipelineFacade(graph_dir=tmp_path)
        assert not facade.has_slug("test-repo")
        _write_metadata(tmp_path, "test-repo")
        assert facade.has_slug("test-repo")


class TestListRepos:
    def test_empty_graph_dir(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        facade = PipelineFacade(graph_dir=tmp_path / "nope")
        result = facade.list_repos()
        assert isinstance(result, ListReposResponse)
        assert result.total == 0
        assert result.repos == []

    def test_lists_repos_from_metadata(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        _write_metadata(
            tmp_path,
            "repo-a",
            {"github_ssh_url": "git@github.com:a/b.git", "node_count": 5},
        )
        facade = PipelineFacade(graph_dir=tmp_path)
        result = facade.list_repos()
        assert result.total == 1
        assert result.repos[0].slug == "repo-a"

    def test_pagination(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        for i in range(5):
            _write_metadata(tmp_path, f"repo-{i}")
        facade = PipelineFacade(graph_dir=tmp_path)
        result = facade.list_repos(page=2, page_size=2)
        assert result.total == 5
        assert result.page == 2
        assert len(result.repos) == 2

    def test_build_status_merged(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        _write_metadata(tmp_path, "building-repo")
        facade = PipelineFacade(graph_dir=tmp_path)
        result = facade.list_repos(
            build_status={"building-repo": {"status": "building", "progress": 42}}
        )
        assert result.repos[0].build_status == "building"
        assert result.repos[0].build_progress == 42

    def test_health_check_ok(self, tmp_path: Path) -> None:
        import networkx as nx
        from codeknow.pipeline.facade import PipelineFacade
        from networkx.readwrite import json_graph as _jg

        slug_dir = tmp_path / "healthy-repo"
        slug_dir.mkdir()
        _write_metadata(tmp_path, "healthy-repo")
        G = nx.Graph()
        G.add_node("n1", label="A")
        data = _jg.node_link_data(G, edges="links")
        (slug_dir / "graph.json").write_text(json.dumps(data), encoding="utf-8")

        facade = PipelineFacade(graph_dir=tmp_path)
        result = facade.list_repos(health_check=True)
        assert result.repos[0].health == "ok"


class TestDelete:
    def test_delete_removes_dirs(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        slug_dir = tmp_path / "del-repo"
        slug_dir.mkdir()
        (slug_dir / "metadata.json").write_text("{}", encoding="utf-8")
        temp_dir = tmp_path / "temp" / "del-repo"
        temp_dir.mkdir(parents=True)

        facade = PipelineFacade(graph_dir=tmp_path, temp_dir=tmp_path / "temp")
        with patch("codeknow.pipeline.facade.PipelineFacade._make_store") as mock_store:
            mock_store.return_value.delete_by_slug.return_value = 3
            result = facade.delete("del-repo")

        assert not slug_dir.exists()
        assert not temp_dir.exists()
        assert result.slug == "del-repo"
        assert result.chunks_deleted == 3


class TestSearch:
    def test_search_delegates_to_graph_searcher(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade
        from codeknow.schemas import HybridSearchResponse

        facade = PipelineFacade(graph_dir=tmp_path)
        with patch("codeknow.vector.search.GraphSearcher.multi_search") as mock_search:
            mock_search.return_value = HybridSearchResponse(
                query="test", vector_hits=1, graph_expanded=0, results=[]
            )
            result = facade.search("test", top_k=5, slugs=["repo-a"])

        mock_search.assert_called_once_with(tmp_path, "test", top_k=5, slugs=["repo-a"])
        assert result.vector_hits == 1
