"""Tests for GraphSearcher — collapsed hybrid search interface."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import networkx as nx
import pytest
from codeknow.schemas import HybridSearchResponse
from codeknow.vector.store import SearchResult
from networkx.readwrite import json_graph as _jg

if TYPE_CHECKING:
    from pathlib import Path


def _save_graph(graph: nx.Graph, path: Path) -> None:
    data = _jg.node_link_data(graph, edges="links")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_graph() -> nx.Graph:
    G = nx.Graph()
    G.add_node("n1", label="AuthService", chunks=[{"hash": "a" * 64}])
    G.add_node("n2", label="TokenStore", chunks=[{"hash": "b" * 64}])
    G.add_node("n3", label="Logger", chunks=[])
    G.add_edge("n1", "n2", relation="calls")
    G.add_edge("n2", "n3", relation="uses")
    return G


@pytest.fixture
def graph_dir(tmp_path: Path) -> Path:
    _save_graph(_make_graph(), tmp_path / "graph.json")
    return tmp_path


def _mock_store(
    search_results: list[SearchResult] | None = None,
    fetch_results: list[SearchResult] | None = None,
) -> MagicMock:
    store = MagicMock()
    store.search.return_value = search_results or []
    store.get_by_ids.return_value = fetch_results or []
    return store


def _sr(
    hash_: str = "a" * 64,
    distance: float = 0.3,
    document: str = "code",
    file: str = "auth.ts",
    start_line: int = 1,
    end_line: int = 10,
    **extra_meta: Any,
) -> SearchResult:
    meta: dict[str, Any] = {
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
    }
    meta.update(extra_meta)
    return SearchResult(hash=hash_, distance=distance, document=document, metadata=meta)


class TestGraphSearcherInit:
    def test_loads_graph_and_builds_reverse_index(self, graph_dir: Path) -> None:
        from codeknow.vector.search import GraphSearcher

        searcher = GraphSearcher(graph_dir, store=_mock_store())
        assert searcher._graph is not None
        assert searcher._graph.number_of_nodes() == 3
        assert searcher._reverse_index["a" * 64] == ["n1"]
        assert searcher._reverse_index["b" * 64] == ["n2"]

    def test_missing_graph_falls_back_gracefully(self, tmp_path: Path) -> None:
        from codeknow.vector.search import GraphSearcher

        empty_dir = tmp_path / "no_graph"
        empty_dir.mkdir()
        searcher = GraphSearcher(empty_dir, store=_mock_store())
        assert searcher._graph is None
        assert searcher._reverse_index == {}


class TestGraphSearcherSearch:
    def test_returns_hybrid_search_response(self, graph_dir: Path) -> None:
        from codeknow.vector.search import GraphSearcher

        store = _mock_store(search_results=[_sr()])
        searcher = GraphSearcher(graph_dir, store=store)
        result = searcher.search("authentication", top_k=5)

        assert isinstance(result, HybridSearchResponse)
        assert result.query == "authentication"
        assert result.vector_hits == 1
        assert len(result.results) >= 1

    def test_vector_only_when_no_graph(self, tmp_path: Path) -> None:
        from codeknow.vector.search import GraphSearcher

        empty_dir = tmp_path / "no_graph"
        empty_dir.mkdir()
        store = _mock_store(search_results=[_sr()])
        searcher = GraphSearcher(empty_dir, store=store)
        result = searcher.search("auth")

        assert result.vector_hits == 1
        assert result.graph_expanded == 0
        assert len(result.results) == 1
        assert result.results[0].provenance == "vector"

    def test_sparse_only_results_do_not_count_as_vector_hits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from codeknow.vector.search import GraphSearcher

        empty_dir = tmp_path / "no_graph"
        empty_dir.mkdir()
        store = _mock_store(search_results=[])
        searcher = GraphSearcher(empty_dir, store=store)

        monkeypatch.setattr(
            searcher,
            "_bm25_search",
            lambda *_args, **_kwargs: [
                (
                    "s" * 64,
                    10.0,
                    "sparse exact match",
                    {"file": "route.js", "start_line": 10, "end_line": 20},
                )
            ],
        )

        result = searcher.search("exact")

        assert result.vector_hits == 0
        assert result.results[0].provenance == "sparse"

    def test_sparse_candidates_survive_rrf_cutoff(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from codeknow.vector.search import GraphSearcher

        empty_dir = tmp_path / "no_graph"
        empty_dir.mkdir()
        dense_hash = "a" * 64
        sparse_hash = "b" * 64
        store = _mock_store(search_results=[_sr(hash_=dense_hash)])
        searcher = GraphSearcher(empty_dir, store=store)

        monkeypatch.setattr(
            searcher,
            "_bm25_search",
            lambda *_args, **_kwargs: [
                (
                    sparse_hash,
                    10.0,
                    "sparse exact match",
                    {"file": "route.js", "start_line": 10, "end_line": 20},
                )
            ],
        )

        result = searcher.search("exact", top_k=1)

        assert {r.chunk_hash for r in result.results} == {dense_hash, sparse_hash}

    def test_source_sparse_candidates_survive_test_heavy_sparse_results(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from codeknow.vector.search import GraphSearcher

        empty_dir = tmp_path / "no_graph"
        empty_dir.mkdir()
        dense_hash = "a" * 64
        test_hash = "b" * 64
        source_hash = "c" * 64
        store = _mock_store(search_results=[_sr(hash_=dense_hash)])
        searcher = GraphSearcher(empty_dir, store=store)

        monkeypatch.setattr(
            searcher,
            "_bm25_search",
            lambda *_args, **_kwargs: [
                (
                    test_hash,
                    10.0,
                    "test exact match",
                    {"file": "test/route.test.js", "start_line": 10, "end_line": 20},
                ),
                (
                    source_hash,
                    9.0,
                    "source exact match",
                    {"file": "lib/route.js", "start_line": 30, "end_line": 40},
                ),
            ],
        )

        result = searcher.search("exact", top_k=1)

        assert {r.chunk_hash for r in result.results} == {
            dense_hash,
            test_hash,
            source_hash,
        }

    def test_graph_expansion_finds_neighbor_chunks(self, graph_dir: Path) -> None:
        from codeknow.vector.search import GraphSearcher

        store = _mock_store(
            search_results=[_sr(hash_="a" * 64)],
            fetch_results=[_sr(hash_="b" * 64, document="token code", file="token.ts")],
        )
        searcher = GraphSearcher(graph_dir, store=store)
        result = searcher.search("auth")

        assert result.vector_hits == 1
        assert result.graph_expanded == 1

        vector_r = [r for r in result.results if r.provenance == "vector"]
        graph_r = [r for r in result.results if r.provenance == "graph"]
        assert len(vector_r) == 1
        assert len(graph_r) == 1
        assert vector_r[0].chunk_hash == "a" * 64
        assert graph_r[0].chunk_hash == "b" * 64

    def test_vector_results_sorted_before_graph(self, graph_dir: Path) -> None:
        from codeknow.vector.search import GraphSearcher

        store = _mock_store(
            search_results=[_sr(hash_="a" * 64)],
            fetch_results=[_sr(hash_="b" * 64, document="token code", file="token.ts")],
        )
        searcher = GraphSearcher(graph_dir, store=store)
        result = searcher.search("auth")

        provenances = [r.provenance for r in result.results]
        vector_idx = provenances.index("vector")
        graph_idx = provenances.index("graph")
        assert vector_idx < graph_idx

    def test_bm25_result_distance_reflects_sparse_score(self, graph_dir: Path) -> None:
        from codeknow.vector.search import GraphSearcher

        searcher = GraphSearcher(graph_dir, store=_mock_store())
        strong = searcher._make_bm25_result(
            "c" * 64,
            "exact code match",
            {"file": "route.js", "start_line": 10, "end_line": 20},
            score=9.0,
            max_score=10.0,
        )
        weak = searcher._make_bm25_result(
            "d" * 64,
            "weak code match",
            {"file": "route.js", "start_line": 30, "end_line": 40},
            score=1.0,
            max_score=10.0,
        )

        assert strong.distance < weak.distance
        assert strong.distance == pytest.approx(0.1)
        assert weak.distance == pytest.approx(0.9)
        assert strong.provenance == "sparse"
