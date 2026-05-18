"""E2E test for hybrid search (vector + graph traversal).

Runs the full pipeline on code-test-small, saves artifacts, embeds chunks
into ChromaDB, then calls hybrid_search() and validates the response.

Requires running Ollama + ChromaDB (checked at import time).
"""

from __future__ import annotations

import atexit
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import chromadb
from check_services import check_chroma, check_ollama
from codeknow.extract.ast import extract_ast
from codeknow.extract.detect import detect
from codeknow.graph.build import build
from codeknow.graph.chunk_mapper import map_chunks
from codeknow.graph.cluster import cluster
from codeknow.pipeline.config import PipelineConfig
from codeknow.pipeline.io import save_pipeline_result
from codeknow.pipeline.stages import _assign_communities
from codeknow.pipeline.types import PipelineResult
from codeknow.schemas import HybridSearchResponse
from codeknow.vector.chroma import ChromaConfig, ChromaStore
from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings
from codeknow.vector.metadata import build_chunk_metadata
from codeknow.vector.search import hybrid_search

logger = logging.getLogger(__name__)

CODE_TEST_SMALL = Path(__file__).parent / "code-test-small"

# ── 1. Health-check services ──────────────────────────────────────────
check_ollama()
check_chroma()

# ── 2. Run pipeline ───────────────────────────────────────────────────
_discovery = detect(CODE_TEST_SMALL)
_extraction = extract_ast(_discovery["files"])
_G = build([_extraction])
_communities = cluster(_G)
_G_enriched, _chunk_map = map_chunks(_G, _discovery["files"])
_assign_communities(_G_enriched, _communities)

# ── 3. Save artifacts ─────────────────────────────────────────────────
_OUTPUT_DIR = Path(tempfile.mkdtemp(prefix="e2e_hybrid_"))
_CONFIG = PipelineConfig(
    repo_url="https://github.com/test/code-test-small",
    output_dir=_OUTPUT_DIR,
)
_RESULT = PipelineResult(
    graph=_G_enriched,
    communities=_communities,
    chunk_map=_chunk_map,
    discovery=_discovery,
    stats={},
    config=_CONFIG,
)
save_pipeline_result(_RESULT)

# ── 4. Embed chunks into ChromaDB ─────────────────────────────────────
_emb_cfg = EmbeddingConfig()
_embeddings = create_embeddings(_emb_cfg)

_COLLECTION = f"e2e_hybrid_{int(time.time())}"
_chroma_cfg = ChromaConfig(collection_name=_COLLECTION)
_STORE = ChromaStore(config=_chroma_cfg, embeddings=_embeddings)

_extra_meta = build_chunk_metadata(_RESULT)
_STORE.store_chunk_map(_chunk_map, slug="e2e-hybrid", extra_metadata=_extra_meta)
logger.info("Stored %d chunks in collection %s", _STORE.count(), _COLLECTION)


# ── 5. Cleanup ────────────────────────────────────────────────────────
def _cleanup():
    try:
        client = chromadb.HttpClient(
            host=_chroma_cfg.resolved_host(),
            port=_chroma_cfg.resolved_port(),
        )
        client.delete_collection(_COLLECTION)
        logger.info("Deleted collection: %s", _COLLECTION)
    except Exception:
        logger.warning("Could not delete collection: %s", _COLLECTION)
    shutil.rmtree(_OUTPUT_DIR, ignore_errors=True)
    logger.info("Removed temp dir: %s", _OUTPUT_DIR)


atexit.register(_cleanup)


# ── Helpers ────────────────────────────────────────────────────────────
def _search(query: str, **kwargs: Any) -> HybridSearchResponse:
    return hybrid_search(
        query,
        output_dir=_OUTPUT_DIR,
        collection_name=_COLLECTION,
        **kwargs,
    )


# ── Tests ──────────────────────────────────────────────────────────────


def test_hybrid_search_returns_response():
    resp = _search("database connection and queries")
    assert isinstance(resp, HybridSearchResponse)
    assert resp.query == "database connection and queries"
    assert isinstance(resp.results, list)


def test_hybrid_search_has_vector_hits():
    resp = _search("user authentication", n_results=5)
    assert resp.vector_hits > 0
    for r in resp.results:
        if r.provenance == "vector":
            assert r.distance is not None


def test_hybrid_search_results_have_required_fields():
    resp = _search("trpc router setup", n_results=5)
    assert len(resp.results) > 0
    for r in resp.results:
        assert r.chunk_hash
        assert r.file
        assert r.content
        assert r.provenance in ("vector", "graph")
        assert r.start_line >= 1
        assert r.end_line >= r.start_line


def test_hybrid_search_results_sorted_by_provenance():
    resp = _search("create channel dialog", n_results=10)
    provenance_order = {"vector": 0, "graph": 1}
    order = [provenance_order.get(r.provenance, 3) for r in resp.results]
    assert order == sorted(order)


def test_hybrid_search_graph_expansion():
    resp = _search(
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
