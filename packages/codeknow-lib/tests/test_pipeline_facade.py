"""Tests for PipelineFacade — API insulation layer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
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

    def test_health_check_uses_custom_generation_graph_name(
        self, tmp_path: Path
    ) -> None:
        import networkx as nx
        from codeknow.pipeline.facade import PipelineFacade
        from codeknow.pipeline.io import GenerationRef, publish_generation
        from networkx.readwrite import json_graph as _jg

        slug_dir = tmp_path / "custom-repo"
        generation = slug_dir / "generations" / "one"
        generation.mkdir(parents=True)
        _write_metadata(generation.parent, "one", {"slug": "custom-repo"})
        graph = nx.Graph()
        graph.add_node("n1", label="A")
        (generation / "custom.json").write_text(
            json.dumps(_jg.node_link_data(graph, edges="links")),
            encoding="utf-8",
        )
        (generation / "chunk_map.json").write_text("{}", encoding="utf-8")
        publish_generation(
            slug_dir,
            GenerationRef(
                "one",
                "collection-one",
                generation,
                graph_filename="custom.json",
            ),
        )

        result = PipelineFacade(graph_dir=tmp_path).list_repos(health_check=True)

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
        with (
            patch(
                "codeknow.vector.chroma.list_collection_names",
                side_effect=[{"codeknow_del-repo"}, set()],
            ),
            patch("codeknow.pipeline.facade.PipelineFacade._make_store") as mock_store,
        ):
            mock_store.return_value.delete_by_slug.return_value = 3
            result = facade.delete("del-repo")

        assert not slug_dir.exists()
        assert not temp_dir.exists()
        assert result.slug == "del-repo"
        assert result.chunks_deleted == 3

    def test_delete_discovers_collection_when_metadata_is_corrupt(
        self,
        tmp_path: Path,
    ) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        slug_dir = tmp_path / "del-repo"
        generation = slug_dir / "generations" / "broken"
        generation.mkdir(parents=True)
        (generation / "metadata.json").write_text("broken", encoding="utf-8")
        stores: dict[str, MagicMock] = {}

        def make_store(_slug: str, collection_name: str) -> MagicMock:
            store = MagicMock()
            store.delete_by_slug.return_value = (
                2 if collection_name == "custom-generation" else 0
            )
            store.count.return_value = 0
            stores[collection_name] = store
            return store

        facade = PipelineFacade(graph_dir=tmp_path)
        with (
            patch(
                "codeknow.vector.chroma.list_collection_names",
                side_effect=[
                    {"custom-generation", "unrelated"},
                    {"unrelated"},
                ],
            ),
            patch.object(facade, "_make_store", side_effect=make_store),
        ):
            result = facade.delete("del-repo")

        assert result.chunks_deleted == 2
        stores["custom-generation"].drop_collection.assert_called_once_with(strict=True)
        stores["unrelated"].drop_collection.assert_not_called()
        assert not slug_dir.exists()

    def test_delete_continues_after_collection_failure_and_preserves_state(
        self,
        tmp_path: Path,
    ) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        slug_dir = tmp_path / "del-repo"
        slug_dir.mkdir()
        stores = {name: MagicMock() for name in ("first", "second")}
        stores["first"].delete_by_slug.side_effect = RuntimeError("offline")
        stores["second"].delete_by_slug.return_value = 1
        stores["second"].count.return_value = 0
        facade = PipelineFacade(graph_dir=tmp_path)

        with (
            patch(
                "codeknow.vector.chroma.list_collection_names",
                side_effect=[set(stores), {"first"}],
            ),
            patch.object(
                facade,
                "_make_store",
                side_effect=lambda _slug, name: stores.get(name, MagicMock()),
            ),
            pytest.raises(RuntimeError, match="1 ChromaDB collection"),
        ):
            facade.delete("del-repo")

        stores["second"].delete_by_slug.assert_called_once_with("del-repo")
        assert slug_dir.exists()

    def test_delete_preserves_state_when_collection_drop_fails(
        self,
        tmp_path: Path,
    ) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        slug_dir = tmp_path / "del-repo"
        slug_dir.mkdir()
        store = MagicMock()
        store.delete_by_slug.return_value = 2
        store.drop_collection.side_effect = RuntimeError("drop failed")
        facade = PipelineFacade(graph_dir=tmp_path)

        with (
            patch(
                "codeknow.vector.chroma.list_collection_names",
                return_value={"codeknow_del-repo"},
            ),
            patch.object(facade, "_make_store", return_value=store),
            pytest.raises(RuntimeError, match="1 ChromaDB collection"),
        ):
            facade.delete("del-repo")

        store.drop_collection.assert_called_once_with(strict=True)
        assert slug_dir.exists()

    def test_delete_fails_closed_when_collection_enumeration_fails(
        self,
        tmp_path: Path,
    ) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        slug_dir = tmp_path / "del-repo"
        generation = slug_dir / "generations" / "broken"
        generation.mkdir(parents=True)
        (generation / "metadata.json").write_text("broken", encoding="utf-8")
        facade = PipelineFacade(graph_dir=tmp_path)

        with (
            patch("codeknow.vector.chroma.list_collection_names", return_value=None),
            pytest.raises(RuntimeError, match="enumerate ChromaDB"),
        ):
            facade.delete("del-repo")

        assert slug_dir.exists()

    def test_cleanup_keeps_internal_lock_directory(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        locks = tmp_path / ".locks"
        locks.mkdir()
        (locks / "repo.lock").touch()
        facade = PipelineFacade(graph_dir=tmp_path)

        assert facade.cleanup() == []
        assert locks.exists()

    def test_recover_cleans_each_known_slug(self, tmp_path: Path) -> None:
        from codeknow.pipeline.facade import PipelineFacade

        slug_dir = tmp_path / "owner-repo"
        slug_dir.mkdir()
        facade = PipelineFacade(graph_dir=tmp_path)

        with patch("codeknow.pipeline.runner._cleanup_old_generations") as cleanup:
            facade.recover()

        cleanup.assert_called_once()
        assert cleanup.call_args.args[0].resolved_output_dir() == slug_dir


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
