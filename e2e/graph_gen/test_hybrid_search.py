"""E2E test for hybrid search (vector + graph traversal).

Runs the full pipeline on code-test-small, saves artifacts, embeds chunks
into ChromaDB, then calls GraphSearcher.search() and validates the response.

Requires running Ollama + ChromaDB (checked at import time).
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import chromadb
import pytest
from check_services import check_chroma, check_ollama
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
from dotenv import load_dotenv
from judge import LLMJudge, from_hybrid_response

# ruff: noqa: T201

logger = logging.getLogger(__name__)

CODE_TEST_SMALL = Path(__file__).parent / "code-test-small"

# ── 1. Health-check services ──────────────────────────────────────────
check_ollama()
check_chroma()

# ── 2. Run pipeline ───────────────────────────────────────────────────
_extractor = Extractor()
_discovery = _extractor.discover(CODE_TEST_SMALL)
_extraction = _extractor.extract_from_discovery(_discovery)
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
    store = kwargs.pop("store", None)
    searcher = GraphSearcher(
        _OUTPUT_DIR,
        collection_name=_COLLECTION,
        store=store,
        **kwargs,
    )
    return searcher.search(query)


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


# ── 6. LLM judge config ────────────────────────────────────────────────
load_dotenv()
_JUDGE_KEY = os.environ.get("JUDGE_LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")

_TRAVERSAL_DEPTH = int(os.environ.get("E2E_TRAVERSAL_DEPTH", "3"))

_REPO_BRIEF = (
    "tRPC + SSE chat app (Next.js). "
    "Features: server-sent events subscriptions, Drizzle ORM, next-auth. "
    "Key dirs: src/server/routers/, src/app/channels/."
)

_QUERIES: list[tuple[str, str]] = [
    ("post creation flow", "how does creating a new post work end to end"),
    ("adding a message", "what happens when a user sends a message"),
    ("typing indicator", "how does the typing indicator work"),
    ("auth guard", "how does authentication guard the tRPC procedures"),
]


# ── 7. Retrieval Quality Ground Truth ──────────────────────────────────
# Maps (label → set of file-path suffixes) that are relevant for each query.
# r.file is an absolute path, so matching is done via endswith().

_RELEVANCE_GROUND_TRUTH: dict[str, set[str]] = {
    "post creation flow": {
        "src/server/routers/post.ts",
        "src/app/channels/[channelId]/chat.tsx",
    },
    "adding a message": {
        "src/server/routers/post.ts",
        "src/app/channels/[channelId]/chat.tsx",
        "src/app/channels/[channelId]/hooks.ts",
    },
    "typing indicator": {
        "src/app/channels/[channelId]/chat.tsx",
        "src/app/channels/[channelId]/hooks.ts",
    },
    "auth guard": {
        "src/server/auth.tsx",
        "src/server/trpc.ts",
        "src/server/context.ts",
    },
    "database connection and queries": {
        "src/server/db/client.ts",
        "src/server/db/schema.ts",
        "drizzle.config.ts",
    },
    "user authentication": {
        "src/server/auth.tsx",
        "src/app/api/auth/[...nextauth]/route.ts",
    },
    "trpc router setup": {
        "src/server/trpc.ts",
        "src/server/routers/_app.ts",
        "src/lib/trpc.ts",
    },
    "create channel dialog": {
        "src/app/create-channel.tsx",
        "src/components/dialog.tsx",
        "src/server/routers/channel.ts",
    },
    "database schema and migrations": {
        "src/server/db/schema.ts",
    },
}

_GT_QUERIES: list[tuple[str, str]] = [
    ("database connection and queries", "database connection and queries"),
    ("user authentication", "user authentication"),
    ("trpc router setup", "trpc router setup"),
    ("create channel dialog", "create channel dialog"),
    ("database schema and migrations", "database schema and migrations"),
    *_QUERIES,
]

_K_VALUES = (5, 10)


def _is_relevant(result_file: str, relevant_suffixes: set[str]) -> bool:
    return any(result_file.endswith(s) for s in relevant_suffixes)


def _precision_at_k(
    results: list,
    relevant: set[str],
    k: int,
) -> float:
    top_k = results[:k]
    if not top_k:
        return 0.0
    return sum(1 for r in top_k if _is_relevant(r.file, relevant)) / k


def _recall_at_k(
    results: list,
    relevant: set[str],
    k: int,
) -> float:
    if not relevant:
        return 0.0
    found = {r.file for r in results[:k] if _is_relevant(r.file, relevant)}
    return len(found) / len(relevant)


def _f1_at_k(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _compute_retrieval_metrics(
    results: list,
    relevant: set[str],
) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = {}
    for k in _K_VALUES:
        p = _precision_at_k(results, relevant, k)
        r = _recall_at_k(results, relevant, k)
        metrics[f"@{k}"] = {"precision": p, "recall": r, "f1": _f1_at_k(p, r)}
    return metrics


def _print_retrieval_report(
    label: str,
    metrics: dict[str, dict[str, float]],
) -> None:
    sep = "-" * 70
    print(f"\n{sep}")
    print(f"RETRIEVAL METRICS: {label}")
    for at_k, m in metrics.items():
        print(
            f"  {at_k}  P={m['precision']:.2f}  R={m['recall']:.2f}  F1={m['f1']:.2f}"
        )
    print(sep)


def _synthesize_analysis(resp: HybridSearchResponse) -> str:
    """Generate a brief analysis from search results for groundedness evaluation."""
    files_seen: dict[str, list[str]] = {}
    graph_paths: list[str] = []
    for r in resp.results:
        files_seen.setdefault(r.file, []).append(f"L{r.start_line}-L{r.end_line}")
        if r.graph_path:
            graph_paths.append(" → ".join(r.graph_path))

    parts = [f"Query: {resp.query}"]
    parts.append(
        f"Retrieved {resp.vector_hits} vector hits, "
        f"{resp.graph_expanded} graph-expanded hits "
        f"({len(resp.results)} total)."
    )
    parts.append("Files found:")
    for f, lines in sorted(files_seen.items()):
        parts.append(f"  {f}: {', '.join(lines)}")
    if graph_paths:
        parts.append("Graph paths traversed:")
        for p in graph_paths:
            parts.append(f"  {p}")
    return "\n".join(parts)


def _enforce_semantic_saturation(output, graph_hit_count: int) -> None:
    """Post-process: if no graph hits, force kg_expansion_value=null
    and recalc score using the saturation formula.
    """
    if graph_hit_count > 0:
        return
    if output.subscores.kg_expansion_value is None:
        return

    logger.info(
        "Enforcing semantic saturation: overriding kg_expansion_value %s → null",
        output.subscores.kg_expansion_value,
    )
    output.subscores.kg_expansion_value = None
    s = output.subscores
    output.final_score = (
        s.semantic_relevance * 0.35
        + 50 * 0.20
        + s.coverage * 0.20
        + s.groundedness * 0.15
        + s.noise_control * 0.10
    )


def _print_judge_report(output, label):
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"JUDGE REPORT: {label}")
    print(f"FINAL SCORE: {output.final_score:.1f}/100")
    print(f"CONFIDENCE:  {output.confidence}")
    print(f"WINNER:      {output.winner}")
    print("\nSUBSCORES:")
    for field, val in output.subscores.model_dump().items():
        display = f"{val:3d}" if val is not None else "N/A"
        print(f"  {field:25s} {display}")
    print("\nSTRENGTHS:")
    for s in output.strengths:
        print(f"  + {s}")
    print("\nWEAKNESSES:")
    for w in output.weaknesses:
        print(f"  - {w}")
    if output.unsupported_claims:
        print("\nUNSUPPORTED CLAIMS:")
        for uc in output.unsupported_claims:
            print(f"  ! {uc.claim}")
            print(f"    Reason: {uc.reason}")
    if output.missing_evidence:
        print("\nMISSING EVIDENCE:")
        for me in output.missing_evidence:
            print(f"  ? {me}")
    print(f"\nRATIONALE:\n  {output.rationale}")
    print("\nEVIDENCE USED:")
    eu = output.evidence_used
    print(f"  semantic: {len(eu.semantic_hit_ids)} hits")
    print(f"  graph:    {len(eu.graph_hit_ids)} hits")
    print(f"  merged:   {len(eu.merged_hit_ids)} hits")
    print(sep)


# ── 8. Deterministic Retrieval Quality Tests ───────────────────────────


@pytest.mark.parametrize(("label", "query"), _GT_QUERIES)
def test_retrieval_precision_at_5(label, query):
    relevant = _RELEVANCE_GROUND_TRUTH.get(label, set())
    resp = _search(query, n_results=10, traversal_depth=2)
    metrics = _compute_retrieval_metrics(resp.results, relevant)
    _print_retrieval_report(label, metrics)
    p5 = metrics["@5"]["precision"]
    assert p5 >= 0.4, f"P@5={p5:.2f} < 0.4 for '{label}' — query: '{query}'"


@pytest.mark.parametrize(("label", "query"), _GT_QUERIES)
def test_retrieval_recall_at_10(label, query):
    relevant = _RELEVANCE_GROUND_TRUTH.get(label, set())
    resp = _search(query, n_results=10, traversal_depth=2)
    metrics = _compute_retrieval_metrics(resp.results, relevant)
    r10 = metrics["@10"]["recall"]
    assert r10 >= 0.5, f"R@10={r10:.2f} < 0.5 for '{label}' — query: '{query}'"


@pytest.mark.parametrize(("label", "query"), _GT_QUERIES)
def test_retrieval_f1_at_10(label, query):
    relevant = _RELEVANCE_GROUND_TRUTH.get(label, set())
    resp = _search(query, n_results=10, traversal_depth=2)
    metrics = _compute_retrieval_metrics(resp.results, relevant)
    f1_10 = metrics["@10"]["f1"]
    assert f1_10 >= 0.4, f"F1@10={f1_10:.2f} < 0.4 for '{label}' — query: '{query}'"


def test_retrieval_metrics_summary():
    scores: list[dict[str, float]] = []
    for label, query in _GT_QUERIES:
        relevant = _RELEVANCE_GROUND_TRUTH.get(label, set())
        resp = _search(query, n_results=10, traversal_depth=2)
        metrics = _compute_retrieval_metrics(resp.results, relevant)
        scores.append(
            {
                "p5": metrics["@5"]["precision"],
                "r10": metrics["@10"]["recall"],
                "f1_10": metrics["@10"]["f1"],
            }
        )

    avg_p5 = sum(s["p5"] for s in scores) / len(scores)
    avg_r10 = sum(s["r10"] for s in scores) / len(scores)
    avg_f1 = sum(s["f1_10"] for s in scores) / len(scores)

    print(f"\n{'=' * 70}")
    print("AGGREGATE RETRIEVAL QUALITY")
    print(f"  Avg P@5  = {avg_p5:.3f}  (threshold: 0.50)")
    print(f"  Avg R@10 = {avg_r10:.3f}  (threshold: 0.60)")
    print(f"  Avg F1@10= {avg_f1:.3f}  (threshold: 0.50)")
    print(f"{'=' * 70}")

    assert avg_p5 >= 0.50, f"Mean P@5={avg_p5:.3f} < 0.50"
    assert avg_r10 >= 0.60, f"Mean R@10={avg_r10:.3f} < 0.60"
    assert avg_f1 >= 0.50, f"Mean F1@10={avg_f1:.3f} < 0.50"


# ── 9. Judge LLM gate ──────────────────────────────────────────────────


@pytest.mark.llm_judge
@pytest.mark.skipif(not _JUDGE_KEY, reason="no JUDGE_LLM_API_KEY or OPENROUTER_API_KEY")
@pytest.mark.parametrize(("label", "query"), _QUERIES)
def test_judge_hybrid_search_quality(label, query):
    resp = _search(query, n_results=10, traversal_depth=_TRAVERSAL_DEPTH)
    relevant = _RELEVANCE_GROUND_TRUTH.get(label, set())
    metrics = _compute_retrieval_metrics(resp.results, relevant)
    _print_retrieval_report(f"{label} — {query}", metrics)

    analysis = _synthesize_analysis(resp)
    judge_input = from_hybrid_response(
        resp, repo_brief=_REPO_BRIEF, agent_analysis=analysis
    )
    output = LLMJudge().judge(judge_input)
    _enforce_semantic_saturation(output, graph_hit_count=resp.graph_expanded)
    _print_judge_report(output, f"{label} — {query}")
    assert output.final_score >= 60, (
        f"Query '{query}' scored {output.final_score:.1f}/100 (threshold: 60). "
        f"Weaknesses: {output.weaknesses}"
    )
