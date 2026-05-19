from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

from codeknow.schemas import HybridSearchResponse, HybridSearchResult
from codeknow.vector.search import hybrid_search
from codeknow.vector.search import sort_key as _sort_key

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

    from codeknow.vector.chroma import ChromaConfig
    from codeknow.vector.embeddings import EmbeddingConfig

logger = logging.getLogger(__name__)


def _discover_graph_dirs(
    graph_base_dir: Path,
    slugs: list[str] | None = None,
) -> list[tuple[str, Path]]:
    if slugs is not None:
        return [
            (s, graph_base_dir / s)
            for s in slugs
            if (graph_base_dir / s / "metadata.json").exists()
        ]

    dirs: list[tuple[str, Path]] = []
    if not graph_base_dir.is_dir():
        return dirs
    for child in sorted(graph_base_dir.iterdir()):
        if child.is_dir() and (child / "metadata.json").exists():
            dirs.append((child.name, child))
    return dirs


def _search_single_graph(
    query: str,
    slug: str,
    graph_dir: Path,
    collection_name: str,
    n_results: int,
    traversal_depth: int,
    chroma_config: ChromaConfig | None,
    embeddings: Embeddings,
) -> tuple[str, HybridSearchResponse] | None:
    try:
        resp = hybrid_search(
            query,
            graph_dir=graph_dir,
            collection_name=collection_name,
            n_results=n_results,
            traversal_depth=traversal_depth,
            chroma_config=chroma_config,
            embeddings=embeddings,
        )
    except Exception:
        logger.warning("Search failed for slug '%s'", slug, exc_info=True)
        return None
    else:
        return (slug, resp)


def multi_graph_search(
    query: str,
    *,
    graph_base_dir: Path,
    slugs: list[str] | None = None,
    n_results_per_graph: int = 5,
    total_limit: int = 20,
    traversal_depth: int = 2,
    embed_config: EmbeddingConfig | None = None,
    chroma_config: ChromaConfig | None = None,
) -> HybridSearchResponse:
    _QUERY_EMPTY = "query must be a non-empty string"
    if not query or not query.strip():
        raise ValueError(_QUERY_EMPTY)

    total_limit = max(1, total_limit)
    n_results_per_graph = max(1, n_results_per_graph)

    if not graph_base_dir.is_dir():
        logger.warning("graph_base_dir does not exist: %s", graph_base_dir)

    graph_dirs = _discover_graph_dirs(graph_base_dir, slugs)

    if not graph_dirs:
        return HybridSearchResponse(
            query=query,
            vector_hits=0,
            graph_expanded=0,
            results=[],
        )

    from codeknow.vector.embeddings import EmbeddingConfig as _EmbedCfg
    from codeknow.vector.embeddings import create_embeddings

    embeddings = create_embeddings(embed_config or _EmbedCfg())

    all_results: list[HybridSearchResult] = []
    total_vector = 0
    total_graph = 0

    def _task(item: tuple[str, Path]) -> tuple[str, HybridSearchResponse] | None:
        slug, graph_dir = item
        return _search_single_graph(
            query,
            slug,
            graph_dir,
            collection_name=f"codeknow_{slug}",
            n_results=n_results_per_graph,
            traversal_depth=traversal_depth,
            chroma_config=chroma_config,
            embeddings=embeddings,
        )

    with ThreadPoolExecutor() as executor:
        results = list(executor.map(_task, graph_dirs))

    for item in results:
        if item is None:
            continue
        slug, resp = item
        tagged = [r.model_copy(update={"slug": slug}) for r in resp.results]
        all_results.extend(tagged)
        total_vector += resp.vector_hits
        total_graph += resp.graph_expanded

    all_results.sort(key=_sort_key)
    all_results = all_results[:total_limit]

    return HybridSearchResponse(
        query=query,
        vector_hits=total_vector,
        graph_expanded=total_graph,
        results=all_results,
    )
