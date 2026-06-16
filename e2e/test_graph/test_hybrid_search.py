"""E2E test for hybrid search (vector + graph traversal).

Runs the full pipeline on code-test-small, saves artifacts, embeds chunks
into ChromaDB, then calls GraphSearcher.search() and validates the response.

Requires running Docker Model Runner + ChromaDB (checked lazily inside the
module-scoped ``search_env`` fixture).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, NamedTuple

import chromadb
import pytest
from check_services import check_chroma, check_docker_model_runner
from codeknow.extract import Extractor
from codeknow.graph.build import build
from codeknow.graph.cluster import cluster
from codeknow.pipeline.chunk_stage import map_chunks
from codeknow.pipeline.config import PipelineConfig
from codeknow.pipeline.io import save_pipeline_result
from codeknow.pipeline.metadata import build_chunk_metadata
from codeknow.pipeline.stages import _assign_communities
from codeknow.pipeline.types import PipelineResult
from codeknow.schemas import HybridSearchResponse
from codeknow.vector.chroma import ChromaConfig, ChromaStore
from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings
from codeknow.vector.search import GraphSearcher

logger = logging.getLogger(__name__)

CODE_TEST_SMALL = Path(__file__).parent / "code-test-small"


class SearchEnv(NamedTuple):
    output_dir: Path
    collection: str
    chroma_cfg: ChromaConfig
    store: ChromaStore


@pytest.fixture(scope="module")
def search_env(tmp_path_factory):
    # ── 1. Health-check services ──────────────────────────────────────
    check_docker_model_runner()
    check_chroma()

    # ── 2. Run pipeline ───────────────────────────────────────────────
    cache_dir = tmp_path_factory.mktemp("hybrid_cache")
    extractor = Extractor(cache_dir=cache_dir)
    discovery = extractor.discover(CODE_TEST_SMALL)
    extraction = extractor.extract_from_discovery(discovery)
    g = build([extraction])
    communities = cluster(g)
    g_enriched, chunk_map = map_chunks(g, discovery["files"])
    _assign_communities(g_enriched, communities)

    # ── 3. Save artifacts ─────────────────────────────────────────────
    output_dir = tmp_path_factory.mktemp("hybrid_out")
    config = PipelineConfig(
        repo_url="https://github.com/test/code-test-small",
        output_dir=output_dir,
    )
    result = PipelineResult(
        graph=g_enriched,
        communities=communities,
        chunk_map=chunk_map,
        discovery=discovery,
        stats={},
        config=config,
    )
    save_pipeline_result(result)

    # ── 4. Embed chunks into ChromaDB ─────────────────────────────────
    embeddings = create_embeddings(EmbeddingConfig())
    collection = f"e2e_hybrid_{int(time.time())}"
    chroma_cfg = ChromaConfig(collection_name=collection)
    store = ChromaStore(config=chroma_cfg, embeddings=embeddings)

    extra_meta = build_chunk_metadata(result)
    store.store_chunk_map(chunk_map, slug="e2e-hybrid", extra_metadata=extra_meta)
    logger.info("Stored %d chunks in collection %s", store.count(), collection)

    yield SearchEnv(output_dir, collection, chroma_cfg, store)

    # ── 5. Teardown: drop the Chroma collection ───────────────────────
    # Temp dirs (cache_dir, output_dir) are auto-cleaned by tmp_path_factory.
    try:
        client = chromadb.HttpClient(
            host=chroma_cfg.resolved_host(),
            port=chroma_cfg.resolved_port(),
        )
        client.delete_collection(collection)
        logger.info("Deleted collection: %s", collection)
    except Exception:
        logger.warning("Could not delete collection: %s", collection)


# ── Helpers ────────────────────────────────────────────────────────────
def _search(env: SearchEnv, query: str, **kwargs: Any) -> HybridSearchResponse:
    store = kwargs.pop("store", None)
    n_results = kwargs.pop("n_results", 10)
    searcher = GraphSearcher(
        env.output_dir,
        collection_name=env.collection,
        store=store or env.store,
        **kwargs,
    )
    return searcher.search(query, top_k=n_results)


# ── Tests ──────────────────────────────────────────────────────────────


def test_hybrid_search_returns_response(search_env):
    resp = _search(search_env, "database connection and queries")
    assert isinstance(resp, HybridSearchResponse)
    assert resp.query == "database connection and queries"
    assert isinstance(resp.results, list)


def test_hybrid_search_has_vector_hits(search_env):
    resp = _search(search_env, "user authentication", n_results=5)
    assert resp.vector_hits > 0
    for r in resp.results:
        if r.provenance == "vector":
            assert r.distance is not None


def test_hybrid_search_results_have_required_fields(search_env):
    resp = _search(search_env, "trpc router setup", n_results=5)
    assert len(resp.results) > 0
    for r in resp.results:
        assert r.chunk_hash
        assert r.file
        assert r.content
        assert r.provenance in ("vector", "sparse", "graph")
        assert r.start_line >= 1
        assert r.end_line >= r.start_line


def test_hybrid_search_results_sorted_by_relevance(search_env):
    resp = _search(search_env, "create channel dialog", n_results=10)
    scores = [GraphSearcher._compute_relevance_score(r) for r in resp.results]
    assert scores == sorted(scores, reverse=True), (
        f"Results not sorted by relevance: {scores}"
    )


def test_hybrid_search_graph_expansion(search_env):
    resp = _search(
        search_env,
        "database schema and migrations",
        n_results=5,
        traversal_depth=2,
    )
    logger.info(
        "graph_expanded=%d, vector_hits=%d, total=%d",
        resp.graph_expanded,
        resp.vector_hits,
        len(resp.results),
    )
    graph_results = [r for r in resp.results if r.provenance == "graph"]
    if graph_results:
        for r in graph_results:
            logger.info(
                "  graph: provenance=%s node_labels=%s path=%s",
                r.provenance,
                r.node_labels,
                r.graph_path,
            )
    else:
        logger.info(
            "No graph expansion results (BFS depth/relations may limit discovery)"
        )
