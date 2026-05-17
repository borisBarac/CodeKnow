from __future__ import annotations

import logging
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

from codeknow.schemas import HybridSearchResponse, HybridSearchResult
from codeknow.vector.search import hybrid_search

if TYPE_CHECKING:
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


def _sort_key(r: HybridSearchResult) -> tuple:
    provenance_order = {"vector": 0, "graph": 1}
    return (
        provenance_order.get(r.provenance, 2),
        r.distance if r.distance is not None else float("inf"),
        len(r.graph_path or []),
    )


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
    graph_dirs = _discover_graph_dirs(graph_base_dir, slugs)

    all_results: list[HybridSearchResult] = []
    total_vector = 0
    total_graph = 0

    for slug, output_dir in graph_dirs:
        collection_name = f"codeknow_{slug}"
        try:
            resp = hybrid_search(
                query,
                output_dir=output_dir,
                collection_name=collection_name,
                n_results=n_results_per_graph,
                traversal_depth=traversal_depth,
                embed_config=embed_config,
                chroma_config=chroma_config,
            )
        except Exception:
            logger.warning("Search failed for slug '%s'", slug, exc_info=True)
            continue

        for r in resp.results:
            r.slug = slug
        all_results.extend(resp.results)
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
