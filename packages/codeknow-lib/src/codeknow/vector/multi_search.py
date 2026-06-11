from __future__ import annotations

from typing import TYPE_CHECKING

from codeknow.vector.search import GraphSearcher

if TYPE_CHECKING:
    from pathlib import Path

    from codeknow.schemas import HybridSearchResponse
    from codeknow.vector.chroma import ChromaConfig
    from codeknow.vector.embeddings import EmbeddingConfig


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
    return GraphSearcher.multi_search(
        graph_base_dir,
        query,
        top_k=total_limit,
        n_results_per_graph=n_results_per_graph,
        traversal_depth=traversal_depth,
        slugs=slugs,
        embed_config=embed_config,
        chroma_config=chroma_config,
    )
